"""Ereignistypen des Protokolls und Umwandlung beliebiger Daten in kanonisches JSON."""

from __future__ import annotations

import math
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping


class EventType(str, Enum):
    SETUP_PROPOSED = "SETUP_PROPOSED"
    AGENT_OPINION = "AGENT_OPINION"
    SIZING_PRECHECK = "SIZING_PRECHECK"  # SORA-Vorpruefung nach RIN, vor KIBO/YUZU (nicht verbindlich, AP-12)
    SIZING_PROPOSED = "SIZING_PROPOSED"  # endgueltige Groesse unter der Sperre des Executors
    SETUP_BLOCKED = "SETUP_BLOCKED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_REPLAYED = "ORDER_REPLAYED"  # Order-ID wiederholt: urspruengliches Ergebnis erneut geliefert, nichts gehandelt
    FUNDING_APPLIED = "FUNDING_APPLIED"
    POSITION_CLOSED = "POSITION_CLOSED"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    CLOSE_REJECTED = "CLOSE_REJECTED"
    EXIT_SIGNAL = "EXIT_SIGNAL"
    DATA_REJECTED = "DATA_REJECTED"  # fehlerhafte/doppelte/aeltere Marktdaten wurden verworfen
    DATA_GAP = "DATA_GAP"  # fehlende Kerze(n) erkannt
    DATA_ANOMALY = "DATA_ANOMALY"  # extreme, aber formal gueltige Kerze (verarbeitet)
    BACKTEST_STARTED = "BACKTEST_STARTED"
    BACKTEST_FINISHED = "BACKTEST_FINISHED"
    HALT_ACTIVATED = "HALT_ACTIVATED"  # globaler Not-Aus ausgeloest (Grund, Zeitpunkt, Hinweis)
    HALT_RELEASED = "HALT_RELEASED"  # Not-Aus bewusst aufgehoben (wer)
    ACCOUNT_CREATED = "ACCOUNT_CREATED"  # Konto von einem Administrator angelegt (Name, Startkapital)


def to_jsonable(obj: Any) -> Any:
    """Wandelt Dataclasses, Decimal, datetime, Enum usw. in reines JSON um.

    Unbekannte Typen und NaN/Infinity fuehren zu einem Fehler: Ein Protokoll, das
    stillschweigend Daten verfaelscht, waere wertlos.
    """
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("NaN/Infinity darf nicht protokolliert werden.")
        return obj
    if isinstance(obj, Decimal):
        if not obj.is_finite():
            raise ValueError("NaN/Infinity darf nicht protokolliert werden.")
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted((to_jsonable(v) for v in obj), key=str)
    raise TypeError("Nicht protokollierbarer Typ: %s" % type(obj).__name__)
