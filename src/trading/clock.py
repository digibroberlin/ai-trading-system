"""Zeitquelle. Wird eingespeist, damit Tests die Zeit kontrollieren koennen."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        """Aktuelle Zeit, immer timezone-aware in UTC."""


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock(Clock):
    """Manuell steuerbare Uhr fuer Tests und Simulationen."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FixedClock braucht eine timezone-aware Zeit (UTC).")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("Zeit muss timezone-aware sein.")
        self._now = value

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)
