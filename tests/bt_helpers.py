"""Bausteine fuer Backtest-Tests: handgebaute Datensaetze und einfache Test-Strategien."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from helpers import T0, inst

from trading.backtest import (
    BacktestConfig,
    EntryProposal,
    ExitProposal,
    InstrumentSeries,
    MarketDataset,
    MarketView,
    Strategy,
    StrategyDecision,
    SyntheticDataSource,
    SyntheticSpec,
)
from trading.domain.instrument import InstrumentId
from trading.domain.models import Candle, FundingEvent, MarketType, Side
from trading.execution.costs import CostModel

D = Decimal
HOUR = timedelta(hours=1)
NO_SLIP = CostModel(slippage_bps=D(0), stop_slippage_bps=D(0))
FREE = CostModel(taker_fee=D(0), maker_fee=D(0), slippage_bps=D(0), stop_slippage_bps=D(0))
BTC = inst("BTC-USD", MarketType.SPOT)
ETHF = inst("ETH-USD", MarketType.FUTURES)


def bars(instrument: InstrumentId, ohlc: Sequence[Sequence[object]], start: datetime = T0,
         interval_s: int = 3600) -> List[Candle]:
    """Kerzen aus (Eroeffnung, Hoch, Tief, Schluss)-Zeilen; lueckenlos ab `start`."""
    return [Candle(instrument, start + i * timedelta(seconds=interval_s), interval_s,
                   D(str(o)), D(str(h)), D(str(lo)), D(str(c)), D(10))
            for i, (o, h, lo, c) in enumerate(ohlc)]


def series(instrument: InstrumentId, ohlc: Sequence[Sequence[object]], funding: Sequence[FundingEvent] = (),
           initial_rate: Optional[Decimal] = None, spread_bps: Decimal = D(0), start: datetime = T0,
           interval_s: int = 3600) -> InstrumentSeries:
    return InstrumentSeries(instrument, interval_s, tuple(bars(instrument, ohlc, start, interval_s)),
                            tuple(funding), initial_rate, spread_bps)


def flat(n: int, price: object = 100) -> List[List[object]]:
    return [[price, price, price, price] for _ in range(n)]


class Scripted(Strategy):
    """Fuehrt einen festen Plan aus: Zeitpunkt -> Entscheidung. Merkt sich jede Sicht, die sie bekam."""

    def __init__(self, plan: Dict[datetime, StrategyDecision], name: str = "scripted") -> None:
        self.plan, self.name = plan, name
        self.views: List[MarketView] = []

    def on_bar(self, view: MarketView) -> StrategyDecision:
        self.views.append(view)
        return self.plan.get(view.now, StrategyDecision())


def long(instrument: InstrumentId = BTC, stop: object = 95, tp: object = 110) -> StrategyDecision:
    return StrategyDecision(entries=(EntryProposal(instrument, Side.LONG, D(str(stop)), D(str(tp))),))


def short(instrument: InstrumentId = ETHF, stop: object = 105, tp: object = 90) -> StrategyDecision:
    return StrategyDecision(entries=(EntryProposal(instrument, Side.SHORT, D(str(stop)), D(str(tp))),))


def leave(instrument: InstrumentId = BTC) -> StrategyDecision:
    return StrategyDecision(exits=(ExitProposal(instrument, "Plan"),))


def synthetic(days: int = 20, seed: int = 5, interval_s: int = 3600, vol: float = 0.006) -> MarketDataset:
    source = SyntheticDataSource(seed=seed, specs={
        BTC: SyntheticSpec(start_price=D(30000), volatility=vol),
        ETHF: SyntheticSpec(start_price=D(2000), volatility=vol * 1.3),
    })
    return MarketDataset.build([source.load(i, interval_s, T0, T0 + timedelta(days=days)) for i in (BTC, ETHF)])


def config(costs: CostModel = NO_SLIP, **kw: object) -> BacktestConfig:
    return BacktestConfig(costs=costs, **kw)  # type: ignore[arg-type]
