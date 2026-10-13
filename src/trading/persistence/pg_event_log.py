"""Ereignisprotokoll in PostgreSQL (gleiche Hash-Kette wie die Datei-Variante).

Die Datenbank prueft jede neue Zeile selbst (Sequenz, Verkettung, Hash; siehe db/schema.sql).
Mehrere Prozesse duerfen gleichzeitig schreiben: Eine Advisory-Sperre reiht sie in eine Warteschlange.
"""

from __future__ import annotations

import json
import threading
from typing import Any, List, Optional

from trading.audit.event_log import (
    GENESIS_HASH,
    EventLog,
    LoggedEvent,
    VerifyResult,
    _compute_hash,
    verify_events,
)
from trading.audit.events import EventType, to_jsonable
from trading.clock import Clock
from trading.persistence.db import connect

CHAIN_LOCK_KEY = 7001  # Schluessel der Advisory-Sperre fuer das Protokoll


def append_raw(cur: Any, ts_text: str, event_type: str, payload_json: str) -> LoggedEvent:
    """Haengt ein Ereignis an die Kette an - INNERHALB der Transaktion des Aufrufers.

    So wird ein Handel und sein Protokolleintrag gemeinsam gespeichert oder gemeinsam verworfen.
    (Eigener Tupel-Cursor auf derselben Verbindung, damit es egal ist, welches Zeilenformat `cur` hat.)
    """
    with cur.connection.cursor() as c:
        c.execute("SELECT pg_advisory_xact_lock(%s)", (CHAIN_LOCK_KEY,))
        c.execute("SELECT seq, hash FROM event_log_head WHERE id = 1")
        head = c.fetchone()
        assert head is not None
        seq, prev = int(head[0]) + 1, str(head[1])
        digest = _compute_hash(seq, ts_text, event_type, prev, payload_json)
        c.execute(
            "INSERT INTO event_log (seq, ts_text, ts, event_type, payload_json, payload, prev_hash, hash)"
            " VALUES (%s, %s, %s::timestamptz, %s, %s, %s::jsonb, %s, %s)",
            (seq, ts_text, ts_text, event_type, payload_json, payload_json, prev, digest),
        )
    return LoggedEvent(seq, ts_text, event_type, payload_json, prev, digest)


class PostgresEventLog(EventLog):
    """Append-only, prozessuebergreifend, mit Hash-Kette. `append` ist eine eigene Transaktion."""

    def __init__(self, url: str, clock: Optional[Clock] = None) -> None:
        super().__init__(clock)
        self._conn = connect(url)
        self._conn_lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()

    def append(self, event_type: EventType, payload: Any) -> LoggedEvent:
        payload_json = json.dumps(
            to_jsonable(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        with self._conn_lock, self._conn.transaction(), self._conn.cursor() as cur:
            return append_raw(cur, self._clock.now().isoformat(), event_type.value, payload_json)

    def events(self) -> "tuple[LoggedEvent, ...]":
        with self._conn_lock, self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                "SELECT seq, ts_text, event_type, payload_json, prev_hash, hash"
                " FROM event_log ORDER BY seq"
            )
            rows: List[Any] = cur.fetchall()
        return tuple(LoggedEvent(int(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in rows)

    def verify(self) -> VerifyResult:
        """Prueft die Kette UND vergleicht ihr Ende mit dem Zeiger `event_log_head`.

        So faellt auch auf, wenn die LETZTEN Eintraege entfernt wurden - das kann eine Hash-Kette allein nicht erkennen.
        """
        events = self.events()
        result = verify_events(list(events))
        if not result.ok:
            return result
        with self._conn_lock, self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("SELECT seq, hash FROM event_log_head WHERE id = 1")
            head = cur.fetchone()
        expected = (events[-1].seq, events[-1].hash) if events else (0, GENESIS_HASH)
        if head is None or (int(head[0]), str(head[1])) != expected:
            return VerifyResult(
                False, len(events), expected[0] + 1,
                "Kettenende passt nicht zum Zeiger (wurden Eintraege am Ende entfernt?)",
            )
        return result

    def _persist(self, event: LoggedEvent) -> None:  # wird nicht benutzt (append ist ueberschrieben)
        raise NotImplementedError
