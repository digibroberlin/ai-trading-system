"""Globaler Not-Aus ("Kill Switch").

Ist der Not-Aus ausgeloest, eroeffnet das System KEINE neue Position mehr - egal welcher Prozess, welches Konto,
welche Strategie. Schliessen bleibt erlaubt (Stop-Loss, Take-Profit, Liquidation, Ausstiegssignal, Funding):
Risiko abbauen muss immer moeglich sein.

Ausloeser:
* von Hand (`trip(HaltReason.MANUAL, ...)`),
* automatisch: formal fehlerhafte Marktdaten oder Zeitstempel aus der Zukunft (Datenfehler), ein Fehler beim Schreiben
  oder Pruefen des Ereignisprotokolls (Protokollfehler), die Notbremsen Tagesverlust und Drawdown.

Freigabe nur BEWUSST: `release(operator, confirmation)` verlangt einen Namen und genau den Bestaetigungstext
`RELEASE_CONFIRMATION`. In der Datenbank darf zusaetzlich nur ein Administrator freigeben - die Anwendungsrolle kann den
Not-Aus nur ausloesen (Trigger in `db/migrations/003_kill_switch.sql`). Ein ausgeloester Not-Aus bleibt ueber Neustarts bestehen.

Der erste Grund bleibt stehen: Ein weiterer Ausloeser aendert Grund und Zeitpunkt nicht (wichtig fuer die Ursachenanalyse).
"""

from __future__ import annotations

import contextlib
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import ContextManager, Optional

from trading.clock import Clock

RELEASE_CONFIRMATION = "HANDEL FREIGEBEN"
_MAX_NOTE = 500


class HaltReason(str, Enum):
    MANUAL = "MANUAL"
    DATA_ERROR = "DATA_ERROR"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    DAILY_LOSS = "DAILY_LOSS"
    DRAWDOWN = "DRAWDOWN"


@dataclass(frozen=True)
class HaltStatus:
    halted: bool
    reason: Optional[HaltReason] = None
    since: Optional[datetime] = None
    note: str = ""


RUNNING = HaltStatus(False)


class KillSwitchError(RuntimeError):
    """Freigabe verweigert (fehlende Bestaetigung) oder Not-Aus nicht lesbar."""


def check_release(operator: str, confirmation: str) -> str:
    """Prueft die Voraussetzungen einer Freigabe; Rueckgabe: bereinigter Name."""
    name = operator.strip() if isinstance(operator, str) else ""
    if not name:
        raise KillSwitchError("Freigabe nur mit Namen der verantwortlichen Person.")
    if confirmation != RELEASE_CONFIRMATION:
        raise KillSwitchError("Freigabe nur mit dem Bestaetigungstext %r." % RELEASE_CONFIRMATION)
    return name[:_MAX_NOTE]


def clean_note(note: str) -> str:
    return str(note)[:_MAX_NOTE]


class KillSwitch(ABC):
    @abstractmethod
    def status(self) -> HaltStatus:
        """Aktueller Zustand. Ist er nicht bestimmbar, gilt der Handel als gestoppt (fail closed)."""

    @abstractmethod
    def trip(self, reason: HaltReason, note: str = "") -> bool:
        """Loest den Not-Aus aus. Rueckgabe: True, wenn er dadurch NEU ausgeloest wurde."""

    @abstractmethod
    def release(self, operator: str, confirmation: str) -> None:
        """Hebt den Not-Aus auf - nur mit Name und Bestaetigungstext."""

    def hold(self) -> ContextManager[object]:
        """Haelt den Zustand fuer die Dauer einer Executor-Operation fest (Ausloesen anderer wartet so lange)."""
        return contextlib.nullcontext()

    @property
    def halted(self) -> bool:
        return self.status().halted


class InMemoryKillSwitch(KillSwitch):
    """Not-Aus im Speicher: gilt fuer alle Executoren, die sich dieses Objekt teilen (ein Prozess)."""

    def __init__(self, clock: Clock, initial: HaltStatus = RUNNING) -> None:
        self._clock = clock
        self._state = initial
        self._lock = threading.RLock()  # wiederbetretbar: ein Executor loest waehrend hold() selbst aus

    def hold(self) -> ContextManager[object]:
        return self._lock

    def status(self) -> HaltStatus:
        with self._lock:
            return self._state

    def trip(self, reason: HaltReason, note: str = "") -> bool:
        if not isinstance(reason, HaltReason):
            reason = HaltReason.MANUAL  # unbekannter Grund: trotzdem stoppen
        with self._lock:
            if self._state.halted:
                return False
            self._state = HaltStatus(True, reason, self._clock.now(), clean_note(note))
            return True

    def release(self, operator: str, confirmation: str) -> None:
        check_release(operator, confirmation)
        with self._lock:
            self._state = RUNNING


__all__ = [
    "HaltReason", "HaltStatus", "InMemoryKillSwitch", "KillSwitch", "KillSwitchError", "RELEASE_CONFIRMATION",
    "RUNNING", "check_release", "clean_note",
]
