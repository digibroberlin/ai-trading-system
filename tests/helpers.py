"""Gemeinsame Bausteine fuer Tests. Ausschliesslich synthetische Daten."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import FrozenSet, Optional, Tuple

from trading.audit.event_log import InMemoryEventLog
from trading.clock import FixedClock
from trading.domain.models import (
    Candle,
    Horizon,
    MarketSnapshot,
    MarketType,
    OrderIntent,
    PortfolioState,
    Side,
    TradeSetup,
)
from trading.execution.costs import CostModel
from trading.execution.paper_executor import PaperExecutor
from trading.safety.data_validator import SnapshotValidator
from trading.safety.limits import DEFAULT_LIMITS, SafetyLimits
from trading.safety.risk_manager import RiskManager

D = Decimal
T0 = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

# Kostenmodell ohne Slippage: macht Erwartungswerte exakt nachrechenbar.
NO_SLIP = CostModel(slippage_bps=D(0), stop_slippage_bps=D(0))


class Env:
    def __init__(
        self,
        equity: Decimal = D(10000),
        limits: SafetyLimits = DEFAULT_LIMITS,
        costs: Optional[CostModel] = NO_SLIP,
    ) -> None:
        self.clock = FixedClock(T0)
        self.log = InMemoryEventLog(self.clock)
        self.risk = RiskManager(limits, costs)
        self.validator = SnapshotValidator(limits, self.clock)
        self.equity0 = equity
        self.executor = PaperExecutor(equity, self.risk, self.validator, self.log, self.clock)

    def snap(self, **kw) -> MarketSnapshot:  # type: ignore[no-untyped-def]
        kw.setdefault("ts", self.clock.now())
        return snapshot(**kw)

    def order(  # type: ignore[no-untyped-def]
        self, oid="o1", qty=D(40), lev=D(1), snap_kw=None, **setup_kw
    ):
        setup_kw.setdefault("setup_id", oid)
        s = make_setup(**setup_kw)
        return self.executor.submit(OrderIntent(oid, s, qty, lev), self.snap(**(snap_kw or {})))


def snapshot(
    symbol: str = "BTC-USD",
    mid: Decimal = D(100),
    half_spread: Decimal = D(0),
    ts: Optional[datetime] = None,
    market_type: MarketType = MarketType.SPOT,
    funding: Optional[Decimal] = None,
    candle: Optional[Candle] = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        market_type=market_type,
        timestamp=ts or T0,
        bid=mid - half_spread,
        ask=mid + half_spread,
        last=mid,
        candle=candle,
        funding_rate=funding,
    )


def make_setup(
    setup_id: str = "s1",
    symbol: str = "BTC-USD",
    market_type: MarketType = MarketType.SPOT,
    side: Side = Side.LONG,
    entry: Decimal = D(100),
    stop: Decimal = D(99),
    tp: Decimal = D(102),
    horizon: Horizon = Horizon.INTRADAY,
    strategy: str = "test",
) -> TradeSetup:
    return TradeSetup(
        setup_id=setup_id,
        symbol=symbol,
        market_type=market_type,
        side=side,
        entry=entry,
        stop_loss=stop,
        take_profit=tp,
        horizon=horizon,
        strategy=strategy,
        created_at=T0,
    )


def make_candle(
    open_time: datetime,
    o: Decimal,
    h: Decimal,
    lo: Decimal,
    c: Decimal,
    symbol: str = "BTC-USD",
    interval_s: int = 60,
) -> Candle:
    return Candle(symbol, open_time, interval_s, o, h, lo, c, D(10))


def flat_candle(open_time: datetime, price: Decimal = D(100), **kw) -> Candle:  # type: ignore[no-untyped-def]
    return make_candle(open_time, price, price, price, price, **kw)


def portfolio(
    equity: Decimal = D(10000),
    free_margin: Optional[Decimal] = None,
    open_risk: Decimal = D(0),
    open_count: int = 0,
    keys: FrozenSet[Tuple[str, MarketType]] = frozenset(),
    day_start: Optional[Decimal] = None,
    peak: Optional[Decimal] = None,
) -> PortfolioState:
    return PortfolioState(
        equity=equity,
        free_margin=equity if free_margin is None else free_margin,
        day_start_equity=equity if day_start is None else day_start,
        peak_equity=equity if peak is None else peak,
        open_risk=open_risk,
        open_count=open_count,
        open_keys=keys,
    )


def minutes(n: int) -> timedelta:
    return timedelta(minutes=n)
