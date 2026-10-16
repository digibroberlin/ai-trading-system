"""Bewertung von Strategien und Agenten durch AKI (AP-19) - nach ALLEN Kosten, ueber Marktphasen und Zeitfenster.

  * `assess`: Nettoergebnis nach Handels-, KI- und Betriebskosten (D-016), Erwartungswert, Profit-Faktor, Drawdown und
    das Ergebnis je Marktphase beim Einstieg (Trend/Seitwaerts x Volatilitaet). Stabilitaet = Anteil der Phasen (mit
    genug Trades), in denen der Erwartungswert nicht negativ ist.
  * `walk_forward_stability`: Ergebnis je Walk-forward-Testfenster; wie oft war es positiv?
  * `agent_value`: Lohnen KIBO/YUZU? Gepaarter Vergleich je Fenster: neutrale Platzhalter (kostenlos, ohne Filter)
    gegen die regelbasierten Agenten - im Zweifel die einfachere Variante (D-016).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from trading.agents.neutral import NeutralKibo, NeutralYuzu
from trading.ai.orchestrator import AICallRecord
from trading.analysis.regime import DEFAULT_REGIME, RegimeConfig, detect_regime
from trading.audit.ai_value import DecisionOutcome, MethodComparison, compare_methods
from trading.audit.costs import CostLedger, build_net_profit_report
from trading.backtest.data import MarketDataset
from trading.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from trading.domain.instrument import InstrumentId
from trading.research.experiment import StrategyFactory
from trading.research.splits import Fold

D = Decimal
ZERO = D(0)
UNKNOWN = "unbekannt"


@dataclass(frozen=True)
class RegimeResult:
    regime: str  # "UP/NORMAL", "SIDEWAYS/HIGH" ... oder "unbekannt" (zu wenig Historie beim Einstieg)
    trades: int
    net: Decimal
    expectancy: Decimal


@dataclass(frozen=True)
class Assessment:
    strategy: str
    start: datetime
    end: datetime
    trades: int
    trading_net: Decimal  # nach Gebuehren, Spread, Slippage, Funding
    ai_cost: Decimal
    operating_cost: Decimal  # Server, Marktdaten, Sonstiges, nicht gemessene KI-Kosten (anteilig)
    net_after_all_costs: Decimal
    expectancy: Decimal
    profit_factor: Optional[Decimal]
    max_drawdown_pct: Decimal
    by_regime: Tuple[RegimeResult, ...]
    regime_stability: Optional[Decimal]  # None: keine Phase mit genug Trades


def regime_at(dataset: MarketDataset, instrument: InstrumentId, time: datetime,
              config: RegimeConfig = DEFAULT_REGIME, history: int = 200) -> str:
    """Marktphase aus den Kerzen, die bis `time` geschlossen hatten (nichts aus der Zukunft)."""
    series = next((s for s in dataset.series if s.instrument == instrument), None)
    if series is None:
        return UNKNOWN
    candles = [c for c in series.candles if c.close_time <= time][-history:]
    regime = detect_regime(candles, config)
    return UNKNOWN if regime is None else "%s/%s" % (regime.trend.value, regime.volatility.value)


def assess(result: BacktestResult, dataset: MarketDataset, ai_records: Sequence[AICallRecord] = (),
           costs: Optional[CostLedger] = None, regime: RegimeConfig = DEFAULT_REGIME,
           min_trades_per_regime: int = 5) -> Assessment:
    report = build_net_profit_report(result.trades, ai_records, costs or CostLedger(), result.start, result.end)
    groups: Dict[str, List[Decimal]] = defaultdict(list)
    for t in result.trades:
        groups[regime_at(dataset, t.instrument, t.opened_at, regime)].append(t.net_pnl)
    rows = tuple(RegimeResult(name, len(pnls), sum(pnls, ZERO), sum(pnls, ZERO) / len(pnls))
                 for name, pnls in sorted(groups.items()))
    judged = [r for r in rows if r.regime != UNKNOWN and r.trades >= min_trades_per_regime]
    stability = D(sum(1 for r in judged if r.expectancy >= 0)) / D(len(judged)) if judged else None
    stats = result.report.trades
    return Assessment(result.strategy, result.start, result.end, stats.trade_count, report.trading_net_pnl,
                      report.ai_cost, report.operating_cost - report.ai_cost, report.net_after_all_costs,
                      stats.expectancy, stats.profit_factor, result.report.equity.max_drawdown_pct, rows, stability)


@dataclass(frozen=True)
class FoldResult:
    index: int
    trades: int
    net: Decimal


def _fold_runs(dataset: MarketDataset, folds: Sequence[Fold], factory: StrategyFactory, config: BacktestConfig,
               neutral: bool = False) -> List[BacktestResult]:
    out = []
    for fold in folds:
        data = dataset.window(fold.test.start, fold.test.end)
        engine = BacktestEngine(data, factory(), config, kibo=NeutralKibo() if neutral else None,
                                yuzu=NeutralYuzu() if neutral else None)
        out.append(engine.run())
    return out


def walk_forward_stability(dataset: MarketDataset, folds: Sequence[Fold], factory: StrategyFactory,
                           config: BacktestConfig) -> Tuple[Tuple[FoldResult, ...], Decimal]:
    """Ergebnis je Testfenster (Strategien ohne Optimierung: das Trainingsfenster dient nur der Reihenfolge)."""
    results = tuple(FoldResult(f.index, r.report.trades.trade_count, r.report.net_result)
                    for f, r in zip(folds, _fold_runs(dataset, folds, factory, config)))
    positive = D(sum(1 for r in results if r.net > 0)) / D(len(results)) if results else ZERO
    return results, positive


def agent_value(dataset: MarketDataset, folds: Sequence[Fold], factory: StrategyFactory, config: BacktestConfig,
                min_samples: int = 30) -> MethodComparison:
    """Gepaart je Fenster: ohne Filter (neutrale Platzhalter) gegen regelbasierte KIBO/YUZU. Wenige Fenster ->
    ZU_WENIG_DATEN (ehrlich), nicht 'besser'."""
    plain = _fold_runs(dataset, folds, factory, config, neutral=True)
    filtered = _fold_runs(dataset, folds, factory, config)
    cheap = {"fold-%03d" % f.index: DecisionOutcome(r.report.net_result, ZERO) for f, r in zip(folds, plain)}
    rich = {"fold-%03d" % f.index: DecisionOutcome(r.report.net_result, ZERO) for f, r in zip(folds, filtered)}
    return compare_methods(cheap, rich, min_samples=min_samples)


__all__ = ["Assessment", "FoldResult", "RegimeResult", "agent_value", "assess", "regime_at",
           "walk_forward_stability"]
