"""Synthetische (erfundene) Marktdaten fuer Tests und Demos.

Reproduzierbar: gleicher `seed` ergibt exakt die gleichen Kerzen. Es werden keine echten
Marktdaten verwendet. Ergebnisse darauf sagen NICHTS ueber echte Profitabilitaet aus - sie
pruefen nur, ob die Technik korrekt funktioniert.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterator, List, Optional

from trading.domain.models import Candle, MarketSnapshot, MarketType
from trading.marketdata.feed import MarketDataFeed


class SyntheticMarket(MarketDataFeed):
    def __init__(
        self,
        symbol: str = "BTC-USD",
        start_price: Decimal = Decimal("50000"),
        seed: int = 42,
        start_time: Optional[datetime] = None,
        interval_s: int = 60,
        drift: float = 0.0,
        volatility: float = 0.001,
        spread_bps: Decimal = Decimal("2"),
        market_type: MarketType = MarketType.SPOT,
        funding_rate: Optional[Decimal] = None,
        tick: Decimal = Decimal("0.01"),
    ) -> None:
        if start_time is None or start_time.tzinfo is None:
            raise ValueError("start_time (UTC, timezone-aware) ist Pflicht.")
        self.symbol = symbol
        self.market_type = market_type
        self.interval_s = interval_s
        self.spread_bps = spread_bps
        self.funding_rate = funding_rate
        self.drift = drift
        self.volatility = volatility
        self._rng = random.Random(seed)
        self._tick = tick
        self._price = start_price
        self._next_open = start_time
        self._pending_shock = 0.0

    def shock(self, pct: float) -> None:
        """Einmaliger Kurssprung in der naechsten Kerze (z. B. -0.10 = -10 %)."""
        self._pending_shock += pct

    def _q(self, value: float) -> Decimal:
        return Decimal(repr(value)).quantize(self._tick, rounding=ROUND_HALF_UP)

    def next_candle(self) -> Candle:
        o = self._price
        ret = self.drift + self.volatility * self._rng.gauss(0, 1) + self._pending_shock
        self._pending_shock = 0.0
        c = self._q(float(o) * math.exp(ret))
        wick_up = abs(self._rng.gauss(0, 1)) * self.volatility * 0.5
        wick_dn = abs(self._rng.gauss(0, 1)) * self.volatility * 0.5
        high = max(self._q(float(max(o, c)) * (1 + wick_up)), o, c)
        low = min(self._q(float(min(o, c)) * (1 - wick_dn)), o, c)
        volume = self._q(10 + self._rng.random() * 90)
        candle = Candle(self.symbol, self._next_open, self.interval_s, o, high, low, c, volume)
        self._price = c
        self._next_open = self._next_open + timedelta(seconds=self.interval_s)
        return candle

    def candles(self, n: int) -> List[Candle]:
        return [self.next_candle() for _ in range(n)]

    def snapshot_for(self, candle: Candle) -> MarketSnapshot:
        """Marktzustand zum Schlusszeitpunkt der Kerze."""
        half_spread = candle.close * self.spread_bps / 20000
        return MarketSnapshot(
            symbol=self.symbol,
            market_type=self.market_type,
            timestamp=candle.close_time,
            bid=candle.close - half_spread,
            ask=candle.close + half_spread,
            last=candle.close,
            candle=candle,
            funding_rate=self.funding_rate,
        )

    def snapshots(self) -> Iterator[MarketSnapshot]:
        while True:
            yield self.snapshot_for(self.next_candle())
