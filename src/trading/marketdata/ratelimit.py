"""Ratenbegrenzung fuer oeffentliche Marktdaten-Schnittstellen (AP-14): gleichmaessiger Abstand, threadsicher.

Die Boersen erlauben deutlich mehr (Coinbase ~10, Bitget ~20 Anfragen je Sekunde und IP); wir bleiben mit Absicht weit
darunter - historische Daten eilen nicht, und eine Sperre der IP wuerde spaeter auch die Marktueberwachung treffen.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

Clock = Callable[[], float]
Sleep = Callable[[float], None]


class RateLimiter:
    def __init__(self, per_second: float, monotonic: Clock = time.monotonic, sleep: Sleep = time.sleep) -> None:
        if not 0 < per_second <= 50:
            raise ValueError("per_second muss zwischen 0 und 50 liegen.")
        self._interval = 1.0 / per_second
        self._monotonic = monotonic
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> float:
        """Wartet bis zum naechsten freien Zeitpunkt. Rueckgabe: gewartete Sekunden."""
        with self._lock:
            now = self._monotonic()
            start = max(now, self._next)
            self._next = start + self._interval
        delay = start - now
        if delay > 0:
            self._sleep(delay)
        return delay


__all__ = ["RateLimiter"]
