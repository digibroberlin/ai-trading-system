"""Gemeinsame Bausteine fuer Tests. Ausschliesslich synthetische Daten."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import FrozenSet, List, Optional

from trading.audit.event_log import InMemoryEventLog
from trading.clock import FixedClock
from trading.domain.instrument import Exchange, InstrumentId
from trading.domain.models import (
    Candle,
    ClosedTrade,
    FundingEvent,
    Horizon,
    MarketSnapshot,
    MarketType,
    OrderIntent,
    PortfolioState,
    Side,
    TradeSetup,
)
from trading.execution.costs import CostModel, FillQuote
from trading.execution.paper_executor import PaperExecutor
from trading.safety.data_validator import SnapshotValidator
from trading.safety.limits import DEFAULT_LIMITS, SafetyLimits
from trading.safety.risk_manager import RiskManager

D = Decimal
T0 = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

# Kostenmodell ohne Slippage: macht Erwartungswerte exakt nachrechenbar.
NO_SLIP = CostModel(slippage_bps=D(0), stop_slippage_bps=D(0))
FUT = {"market_type": MarketType.FUTURES, "funding": D("0.0001")}


def inst(
    symbol: str = "BTC-USD",
    market_type: MarketType = MarketType.SPOT,
    exchange: Exchange = Exchange.SYNTHETIC,
) -> InstrumentId:
    return InstrumentId(exchange, market_type, symbol)


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

    def feed(self, candle: Candle) -> List[ClosedTrade]:
        """Verarbeitet eine Kerze so, wie es in Wirklichkeit geschieht: erst wenn sie abgeschlossen ist.
        (Die Uhr wird auf ihren Schlusszeitpunkt vorgestellt - der Executor lehnt Kerzen aus der Zukunft ab.)"""
        if candle.close_time > self.clock.now():
            self.clock.set(candle.close_time)
        return self.executor.on_candle(candle)

    def fund(  # type: ignore[no-untyped-def]
        self, symbol="BTC-USD", rate=D("0.0001"), mark=D(100), time=None, exchange=Exchange.SYNTHETIC
    ) -> Decimal:
        """Bucht eine Funding-Abrechnung zum aktuellen Zeitpunkt der Test-Uhr (oder zu `time`)."""
        event = FundingEvent(inst(symbol, MarketType.FUTURES, exchange), time or self.clock.now(), rate, mark)
        return self.executor.apply_funding(event)


def snapshot(
    symbol: str = "BTC-USD",
    mid: Decimal = D(100),
    half_spread: Decimal = D(0),
    ts: Optional[datetime] = None,
    market_type: MarketType = MarketType.SPOT,
    funding: Optional[Decimal] = None,
    candle: Optional[Candle] = None,
    exchange: Exchange = Exchange.SYNTHETIC,
) -> MarketSnapshot:
    return MarketSnapshot(
        instrument=inst(symbol, market_type, exchange),
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
    exchange: Exchange = Exchange.SYNTHETIC,
) -> TradeSetup:
    return TradeSetup(
        setup_id=setup_id,
        instrument=inst(symbol, market_type, exchange),
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
    market_type: MarketType = MarketType.SPOT,
    exchange: Exchange = Exchange.SYNTHETIC,
) -> Candle:
    return Candle(inst(symbol, market_type, exchange), open_time, interval_s, o, h, lo, c, D(10))


def flat_candle(open_time: datetime, price: Decimal = D(100), **kw) -> Candle:  # type: ignore[no-untyped-def]
    return make_candle(open_time, price, price, price, price, **kw)


def quote(fill: Decimal = D(100), mark: Optional[Decimal] = None, funding: Optional[Decimal] = None) -> FillQuote:
    return FillQuote(fill, fill if mark is None else mark, funding)


def portfolio(
    equity: Decimal = D(10000),
    free_margin: Optional[Decimal] = None,
    open_risk: Decimal = D(0),
    open_count: int = 0,
    keys: FrozenSet[InstrumentId] = frozenset(),
    day_start: Optional[Decimal] = None,
    peak: Optional[Decimal] = None,
    gross_exposure: Decimal = D(0),
    stress_loss: Decimal = D(0),
    valuation_complete: bool = True,
) -> PortfolioState:
    return PortfolioState(
        balance=equity,
        unrealized_pnl=D(0),
        equity=equity,
        free_margin=equity if free_margin is None else free_margin,
        day_start_equity=equity if day_start is None else day_start,
        peak_equity=equity if peak is None else peak,
        open_risk=open_risk,
        open_count=open_count,
        open_keys=keys,
        gross_exposure=gross_exposure,
        stress_loss=stress_loss,
        valuation_complete=valuation_complete,
    )


def minutes(n: int) -> timedelta:
    return timedelta(minutes=n)
