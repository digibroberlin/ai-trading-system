"""Prueft Momentaufnahmen des Marktes, bevor irgendein Trade darauf basieren darf.

Grundsatz "fail closed": Im Zweifel sind Daten ungueltig, und es wird nicht gehandelt.

Der Kurssprung-Filter vergleicht mit dem letzten AKTUELLEN Kurs des Instruments (aus Snapshots und
aus verarbeiteten Kerzen). Ein alter Referenzkurs verfaellt: Nach laengerer Pause wird nicht gegen
einen Stunden alten Kurs geprueft - sonst bliebe ein Instrument nach einer normalen Kursbewegung
dauerhaft blockiert.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from trading.clock import Clock
from trading.domain.instrument import InstrumentId, MarketType, is_instrument
from trading.domain.models import MarketSnapshot
from trading.domain.numeric import is_finite_decimal
from trading.domain.reasons import RejectReason
from trading.marketdata.candles import candle_problems, is_aware
from trading.safety.limits import SafetyLimits

FUTURE_TOLERANCE_S = 2  # Uhren-Abweichung, die wir tolerieren
DEFAULT_REFERENCE_MAX_AGE_S = 900  # aelterer Referenzkurs wird fuer den Sprung-Filter nicht mehr benutzt

Reference = Tuple[datetime, Decimal]


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reasons: Tuple[RejectReason, ...] = ()


class SnapshotValidator:
    """Zustandsbehaftet: merkt sich je Instrument den letzten gueltigen Kurs (Reihenfolge, Spruenge)."""

    def __init__(
        self,
        limits: SafetyLimits,
        clock: Clock,
        reference_max_age_s: int = DEFAULT_REFERENCE_MAX_AGE_S,
    ) -> None:
        self._limits = limits
        self._clock = clock
        self._reference_max_age_s = reference_max_age_s
        self._ref: Dict[InstrumentId, Reference] = {}

    # ------------------------------------------------------------------ Zustand

    def export_state(self, instrument: InstrumentId) -> Optional[Reference]:
        return self._ref.get(instrument)

    def restore_state(self, instrument: InstrumentId, reference: Optional[Reference]) -> None:
        """Stellt den zuletzt gueltigen Kurs eines Instruments wieder her (z. B. aus der Datenbank)."""
        if reference is not None:
            self._ref[instrument] = reference

    def snapshot_state(self) -> Dict[InstrumentId, Reference]:
        return dict(self._ref)

    def replace_state(self, state: Dict[InstrumentId, Reference]) -> None:
        self._ref = dict(state)

    def observe(self, instrument: InstrumentId, time: datetime, price: Decimal) -> None:
        """Uebernimmt einen bereits geprueften Kurs (Schlusskurs einer verarbeiteten Kerze) als Referenz."""
        current = self._ref.get(instrument)
        if current is None or time >= current[0]:
            self._ref[instrument] = (time, price)

    def reset(self, instrument: InstrumentId) -> None:
        self._ref.pop(instrument, None)

    # ------------------------------------------------------------------ Pruefung

    def validate(self, s: MarketSnapshot) -> ValidationResult:
        reasons: List[RejectReason] = []
        lim = self._limits

        if not is_instrument(s.instrument):
            return ValidationResult(False, (RejectReason.INVALID_SYMBOL,))
        if not isinstance(s.timestamp, datetime):
            return ValidationResult(False, (RejectReason.MISSING_DATA,))
        if not is_aware(s.timestamp):
            return ValidationResult(False, (RejectReason.NAIVE_TIMESTAMP,))

        age = (self._clock.now() - s.timestamp).total_seconds()
        if age > lim.max_data_age_s:
            reasons.append(RejectReason.STALE_DATA)
        if age < -FUTURE_TOLERANCE_S:
            reasons.append(RejectReason.FUTURE_TIMESTAMP)

        ref = self._ref.get(s.instrument)
        prices = (s.bid, s.ask, s.last)
        if any(p is None for p in prices):
            reasons.append(RejectReason.MISSING_DATA)  # unvollstaendige Daten
        elif not all(is_finite_decimal(p) for p in prices):
            reasons.append(RejectReason.NON_FINITE_VALUE)  # NaN, Infinity, float ...
        elif not all(p > 0 for p in prices):
            reasons.append(RejectReason.NON_POSITIVE_PRICE)
        else:
            if s.bid > s.ask:
                reasons.append(RejectReason.CROSSED_BOOK)
            elif s.spread_bps > lim.max_spread_bps:
                reasons.append(RejectReason.SPREAD_TOO_WIDE)
            if ref is not None:
                ref_age = (s.timestamp - ref[0]).total_seconds()
                if 0 <= ref_age <= self._reference_max_age_s \
                        and abs(s.last - ref[1]) / ref[1] > lim.max_price_jump:
                    reasons.append(RejectReason.PRICE_JUMP)

        if ref is not None and s.timestamp < ref[0]:
            reasons.append(RejectReason.OUT_OF_ORDER)

        if s.candle is not None and candle_problems(s.candle, s.instrument):
            reasons.append(RejectReason.BAD_CANDLE)

        if s.instrument.market_type is MarketType.FUTURES:
            if s.funding_rate is None:
                reasons.append(RejectReason.FUNDING_MISSING)
            elif not is_finite_decimal(s.funding_rate):
                reasons.append(RejectReason.NON_FINITE_VALUE)

        if reasons:
            return ValidationResult(False, tuple(reasons))

        self._ref[s.instrument] = (s.timestamp, s.last)
        return ValidationResult(True)
