"""Ereignisbasierte Aktivierung der KI-Agenten.

Statt alle Agenten staendig aufzurufen, darf ein KI-Agent nur bei Ereignissen laufen, fuer die er zustaendig ist
(z. B. KIBO bei einem neuen, bereits regelgeprueften Setup), und je Agent und Gegenstand (Instrument, Setup) hoechstens
einmal je Abkuehlzeit. Alles andere erledigen die deterministischen Regeln ohne KI-Aufruf.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, FrozenSet, Mapping, Tuple

from trading.clock import Clock


class TriggerEvent(str, Enum):
    SETUP_CANDIDATE = "SETUP_CANDIDATE"  # neues Setup hat RIN und SORA bestanden
    REGIME_CHANGE = "REGIME_CHANGE"  # Marktphase hat gewechselt (Trend/Seitwaerts, Volatilitaet)
    VOLATILITY_SPIKE = "VOLATILITY_SPIKE"
    POSITION_AT_RISK = "POSITION_AT_RISK"  # offene Position nahe am Stop / ungewoehnliche Bewegung
    SCHEDULED_REVIEW = "SCHEDULED_REVIEW"  # seltene, geplante Gesamtbewertung


@dataclass(frozen=True)
class AgentActivation:
    events: FrozenSet[TriggerEvent]
    cooldown_s: int  # Mindestabstand zwischen zwei KI-Laeufen fuer denselben Gegenstand


@dataclass(frozen=True)
class ActivationRules:
    per_agent: Mapping[str, AgentActivation] = field(default_factory=dict)


# Vorgabe: KI nur fuer die Bewertung neuer Setup-Kandidaten und bei Marktphasen-Wechseln; jeder Gegenstand hoechstens
# alle 15 Minuten. Agenten ohne Eintrag laufen nie ueber KI (z. B. RIN, SORA: rein regelbasiert).
DEFAULT_ACTIVATION = ActivationRules({
    "KIBO": AgentActivation(frozenset({TriggerEvent.SETUP_CANDIDATE}), 900),
    "YUZU": AgentActivation(frozenset({TriggerEvent.SETUP_CANDIDATE, TriggerEvent.REGIME_CHANGE}), 900),
    "MOMO": AgentActivation(frozenset({TriggerEvent.REGIME_CHANGE, TriggerEvent.SCHEDULED_REVIEW}), 3600),
})


class ActivationPolicy:
    """Entscheidet, ob ein KI-Agent fuer ein Ereignis laufen darf (und merkt sich den Lauf)."""

    def __init__(self, rules: ActivationRules, clock: Clock) -> None:
        self._rules = rules
        self._clock = clock
        self._last: Dict[Tuple[str, str], datetime] = {}
        self._lock = threading.RLock()

    def ready(self, agent: str, event: TriggerEvent, subject: str) -> bool:
        """Darf der Agent fuer dieses Ereignis jetzt laufen? (Merkt sich nichts - siehe `mark`.)"""
        rule = self._rules.per_agent.get(agent)
        if rule is None or not isinstance(event, TriggerEvent) or event not in rule.events:
            return False
        with self._lock:
            last = self._last.get((agent, subject))
            # Auch eine rueckwaerts gehende Uhr (negativer Abstand) blockiert - im Zweifel kein Aufruf.
            return last is None or (self._clock.now() - last).total_seconds() >= rule.cooldown_s

    def mark(self, agent: str, subject: str) -> None:
        """Ein KI-Aufruf hat stattgefunden: ab jetzt laeuft die Abkuehlzeit."""
        with self._lock:
            self._last[(agent, subject)] = self._clock.now()

    def should_run(self, agent: str, event: TriggerEvent, subject: str) -> bool:
        """`ready` und, falls ja, sofort `mark` (fuer Aufrufer, die danach sicher laufen)."""
        with self._lock:
            if not self.ready(agent, event, subject):
                return False
            self.mark(agent, subject)
            return True


__all__ = ["ActivationPolicy", "ActivationRules", "AgentActivation", "DEFAULT_ACTIVATION", "TriggerEvent"]
