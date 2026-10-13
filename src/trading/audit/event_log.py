"""Unveraenderliches Ereignisprotokoll (append-only, Hash-Kette).

Eigenschaften:
* Es gibt nur `append`. Es existiert absichtlich keine Methode zum Aendern oder Loeschen.
* Jedes Ereignis enthaelt den SHA-256-Hash seines Vorgaengers. Wird irgendein
  frueheres Ereignis veraendert, loeschen oder umsortiert, stimmt die Kette nicht mehr,
  und `verify()` meldet die erste kaputte Stelle.
* Die Datei-Variante schreibt jede Zeile sofort auf die Platte (flush + fsync) und prueft
  beim Start die gesamte Kette. Ist sie beschaedigt, wird der Start verweigert.

Grenze (ehrlich): Wer vollen Schreibzugriff auf die Datei hat, kann die GESAMTE Kette neu
berechnen. Die Hash-Kette macht Manipulation sichtbar, verhindert sie aber nicht gegen
einen Angreifer mit Systemzugriff. Spaeter: Hash regelmaessig extern ablegen (z. B. per
E-Mail an sich selbst) und die PostgreSQL-Trigger aus db/init.sql nutzen.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from trading.audit.events import EventType, to_jsonable
from trading.clock import Clock, SystemClock

GENESIS_HASH = "0" * 64


class EventLogCorruptedError(RuntimeError):
    """Die Hash-Kette ist beschaedigt. Es darf nicht weiter gehandelt werden."""


@dataclass(frozen=True)
class LoggedEvent:
    seq: int
    ts: str
    event_type: str
    payload_json: str  # kanonischer JSON-Text - exakt das, was gehasht wurde
    prev_hash: str
    hash: str

    @property
    def payload(self) -> Dict[str, Any]:
        return json.loads(self.payload_json)  # type: ignore[no-any-return]


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    first_bad_seq: Optional[int] = None
    message: str = ""


def _compute_hash(seq: int, ts: str, event_type: str, prev_hash: str, payload_json: str) -> str:
    material = "|".join((str(seq), ts, event_type, prev_hash, payload_json))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def verify_events(events: List[LoggedEvent]) -> VerifyResult:
    prev = GENESIS_HASH
    for index, ev in enumerate(events):
        if ev.seq != index + 1:
            return VerifyResult(False, index, ev.seq, "Sequenznummer unterbrochen")
        if ev.prev_hash != prev:
            return VerifyResult(False, index, ev.seq, "Verkettung gebrochen")
        expected = _compute_hash(ev.seq, ev.ts, ev.event_type, ev.prev_hash, ev.payload_json)
        if ev.hash != expected:
            return VerifyResult(False, index, ev.seq, "Inhalt veraendert (Hash passt nicht)")
        prev = ev.hash
    return VerifyResult(True, len(events))


class EventLog(ABC):
    """Nur Anhaengen und Lesen - bewusst keine update/delete-Methoden."""

    def __init__(self, clock: Optional[Clock] = None) -> None:
        self._clock = clock or SystemClock()
        self._events = []  # type: List[LoggedEvent]
        self._lock = threading.Lock()

    def append(self, event_type: EventType, payload: Any) -> LoggedEvent:
        # Payload sofort in einen Text einfrieren: spaetere Aenderungen am Original-Objekt
        # koennen die Historie nicht mehr beeinflussen.
        payload_json = json.dumps(
            to_jsonable(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        with self._lock:
            seq = len(self._events) + 1
            prev = self._events[-1].hash if self._events else GENESIS_HASH
            ts = self._clock.now().isoformat()
            digest = _compute_hash(seq, ts, event_type.value, prev, payload_json)
            event = LoggedEvent(seq, ts, event_type.value, payload_json, prev, digest)
            self._persist(event)  # erst sichern, dann im Speicher uebernehmen
            self._events.append(event)
            return event

    def events(self) -> Tuple[LoggedEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def of_type(self, event_type: EventType) -> Tuple[LoggedEvent, ...]:
        return tuple(e for e in self.events() if e.event_type == event_type.value)

    def verify(self) -> VerifyResult:
        return verify_events(list(self.events()))

    @abstractmethod
    def _persist(self, event: LoggedEvent) -> None:
        """Dauerhaft speichern. Muss bei Fehlern eine Exception werfen (fail closed)."""


class InMemoryEventLog(EventLog):
    def _persist(self, event: LoggedEvent) -> None:
        return None


class JsonlEventLog(EventLog):
    """Eine JSON-Zeile pro Ereignis."""

    def __init__(self, path: str, clock: Optional[Clock] = None) -> None:
        super().__init__(clock)
        self._path = path
        if os.path.exists(path):
            loaded = self._read_file()
            result = verify_events(loaded)
            if not result.ok:
                raise EventLogCorruptedError(
                    "Ereignisprotokoll %s beschaedigt bei Ereignis %s: %s"
                    % (path, result.first_bad_seq, result.message)
                )
            self._events = loaded

    def verify_file(self) -> VerifyResult:
        """Prueft die Datei auf der Platte (nicht nur den Arbeitsspeicher)."""
        return verify_events(self._read_file())

    def _read_file(self) -> List[LoggedEvent]:
        events = []  # type: List[LoggedEvent]
        with open(self._path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(LoggedEvent(**json.loads(line)))
                except (ValueError, TypeError) as exc:
                    raise EventLogCorruptedError("Unlesbare Zeile im Protokoll: %s" % exc) from exc
        return events

    def _persist(self, event: LoggedEvent) -> None:
        record = {
            "seq": event.seq,
            "ts": event.ts,
            "event_type": event.event_type,
            "payload_json": event.payload_json,
            "prev_hash": event.prev_hash,
            "hash": event.hash,
        }
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
