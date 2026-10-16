"""Strategie-Bibliothek (AP-16) und Vergleichsmassstaebe - nur Vorschlaege, nie Handelserlaubnis.

    from trading.strategies import build_strategy, default_strategies, buy_and_hold
"""

from __future__ import annotations

from typing import Callable, Dict, List

from trading.backtest.reference import SmaCrossStrategy
from trading.backtest.strategy import Strategy
from trading.strategies.benchmarks import BenchmarkResult, NoTradeStrategy, buy_and_hold
from trading.strategies.library import (
    DEFAULT_STOPS,
    BreakoutStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    RuleStrategy,
    ScalpingStrategy,
    Signal,
    StopRules,
    TrendFollowingStrategy,
    VolatilitySqueezeStrategy,
)

# Name -> Bauplan mit Standardeinstellungen (fuer Kommandozeile, Forschungsrahmen und Dashboard)
STRATEGY_TYPES: Dict[str, Callable[[], Strategy]] = {
    "trend": TrendFollowingStrategy,
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
    "breakout": BreakoutStrategy,
    "volatility": VolatilitySqueezeStrategy,
    "scalping": ScalpingStrategy,
    "sma_cross": SmaCrossStrategy,
    "no_trade": NoTradeStrategy,
}


def build_strategy(kind: str) -> Strategy:
    """Strategie mit Standardeinstellungen; unbekannter Name -> ValueError (nie stillschweigend eine andere)."""
    try:
        factory = STRATEGY_TYPES[kind]
    except KeyError:
        raise ValueError("Unbekannte Strategie %r (bekannt: %s)." % (kind, ", ".join(sorted(STRATEGY_TYPES)))) from None
    return factory()


def default_strategies() -> List[Strategy]:
    """Je eine Strategie jeder Art mit Standardeinstellungen, in fester Reihenfolge."""
    return [build_strategy(kind) for kind in STRATEGY_TYPES]


__all__ = [
    "DEFAULT_STOPS", "STRATEGY_TYPES", "BenchmarkResult", "BreakoutStrategy", "MeanReversionStrategy",
    "MomentumStrategy", "NoTradeStrategy", "RuleStrategy", "ScalpingStrategy", "Signal", "StopRules",
    "TrendFollowingStrategy", "VolatilitySqueezeStrategy", "build_strategy", "buy_and_hold", "default_strategies",
]
