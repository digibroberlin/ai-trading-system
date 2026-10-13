"""Deterministische Backtesting-Engine (ausschliesslich Simulation, keine Boersenanbindung).

    from trading.backtest import BacktestConfig, BacktestEngine, MarketDataset, Strategy

Siehe docs/BACKTESTING.md fuer Ablauf, Annahmen und Grenzen.
"""

from trading.backtest.compare import comparison_table
from trading.backtest.data import (
    DataQualityError,
    HistoricalDataSource,
    InstrumentSeries,
    MarketDataset,
    SyntheticDataSource,
    SyntheticSpec,
)
from trading.backtest.engine import BacktestConfig, BacktestEngine, BacktestError, BacktestResult
from trading.backtest.strategy import (
    EntryProposal,
    ExitProposal,
    MarketView,
    Strategy,
    StrategyDecision,
)

__all__ = [
    "BacktestConfig", "BacktestEngine", "BacktestError", "BacktestResult", "DataQualityError",
    "EntryProposal", "ExitProposal", "HistoricalDataSource", "InstrumentSeries", "MarketDataset",
    "MarketView", "Strategy", "StrategyDecision", "SyntheticDataSource", "SyntheticSpec",
    "comparison_table",
]
