"""Prueft Marktdaten, bevor irgendein Trade darauf basieren darf.

Grundsatz "fail closed": Im Zweifel sind Daten ungueltig, und es wird nicht gehandelt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal  # noqa: F401  (wird in Typ-Kommentaren benutzt)
from typing import Dict, List, Optional, Tuple  # noqa: F401

from trading.clock import Clock
from trading.domain.models import Candle, MarketSnapshot, MarketType
from trading.domain.numeric import is_finite_decimal, is_positive_decimal
from trading.domain.reasons import RejectReason
from trading.safety.limits import SafetyLimits

_FUTURE_TOLERANCE_S = 2  # Uhren-Abweichung, die wir tolerieren


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reasons: Tuple[RejectReason, ...] = ()


class SnapshotValidator:
    """Zustandsbehaftet: merkt sich je Symbol den letzten gueltigen Tick (Reihenfolge, Spruenge)."""

    def __init__(self, limits: SafetyLimits, clock: Clock) -> None:
        self._limits = limits
        self._clock = clock
        self._last_ts = {}  # type: Dict[str, datetime]
        self._last_price = {}  # type: Dict[str, Decimal]

    def export_state(self, symbol: str) -> Tuple[Optional[datetime], Optional[Decimal]]:
        return self._last_ts.get(symbol), self._last_price.get(symbol)

    def restore_state(
        self, symbol: str, last_ts: Optional[datetime], last_price: Optional[Decimal]
    ) -> None:
        """Stellt den zuletzt gueltigen Tick eines Symbols wieder her (z. B. aus der Datenbank)."""
        if last_ts is not None:
            self._last_ts[symbol] = last_ts
        if last_price is not None:
            self._last_price[symbol] = last_price

    def reset(self, symbol: str) -> None:
        """Nach einem bewusst geprueften, echten Marktsprung (z. B. Crash) von Hand aufrufen."""
        self._last_ts.pop(symbol, None)
        self._last_price.pop(symbol, None)

    def validate(self, s: MarketSnapshot) -> ValidationResult:
        reasons = []  # type: List[RejectReason]
        lim = self._limits

        if not isinstance(s.timestamp, datetime):
            return ValidationResult(False, (RejectReason.MISSING_DATA,))
        if s.timestamp.tzinfo is None:
            return ValidationResult(False, (RejectReason.NAIVE_TIMESTAMP,))

        age = (self._clock.now() - s.timestamp).total_seconds()
        if age > lim.max_data_age_s:
            reasons.append(RejectReason.STALE_DATA)
        if age < -_FUTURE_TOLERANCE_S:
            reasons.append(RejectReason.FUTURE_TIMESTAMP)

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

            prev = self._last_price.get(s.symbol)
            if prev is not None and abs(s.last - prev) / prev > lim.max_price_jump:
                reasons.append(RejectReason.PRICE_JUMP)

        prev_ts = self._last_ts.get(s.symbol)
        if prev_ts is not None and s.timestamp < prev_ts:
            reasons.append(RejectReason.OUT_OF_ORDER)

        if s.candle is not None and not self._candle_ok(s.candle, s.symbol):
            reasons.append(RejectReason.BAD_CANDLE)

        if s.market_type is MarketType.FUTURES:
            if s.funding_rate is None:
                reasons.append(RejectReason.FUNDING_MISSING)
            elif not is_finite_decimal(s.funding_rate):
                reasons.append(RejectReason.NON_FINITE_VALUE)

        if reasons:
            return ValidationResult(False, tuple(reasons))

        self._last_ts[s.symbol] = s.timestamp
        self._last_price[s.symbol] = s.last
        return ValidationResult(True)

    @staticmethod
    def _candle_ok(c: Candle, symbol: str) -> bool:
        if not isinstance(c.open_time, datetime) or c.open_time.tzinfo is None:
            return False
        if c.symbol != symbol or not isinstance(c.interval_s, int) or c.interval_s <= 0:
            return False
        o, h, lo, cl = c.open, c.high, c.low, c.close
        if not all(is_positive_decimal(x) for x in (o, h, lo, cl)):
            return False
        if not is_finite_decimal(c.volume) or c.volume < 0:
            return False
        return h >= max(o, cl, lo) and lo <= min(o, cl, h)
