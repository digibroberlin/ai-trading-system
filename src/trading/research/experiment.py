"""Forschungsablauf (AP-18): Validierung mit Versuchszaehlung, Kosten-Stresstest, Test genau einmal.

Kriterien fuer "VALIDIERT" (alle muessen gelten, sonst VERWORFEN mit Gruenden):
  1. Netto-Ergebnis auf den Validierungsdaten > 0 - nach Gebuehren, Spread, Slippage und Funding;
  2. mindestens `min_trades` Trades (sonst ist jedes Ergebnis Zufall);
  3. Sharpe je Schritt ueber der Schwelle, die mit der ZAHL DER VERSUCHE der Familie steigt (vereinfachte
     "deflationierte" Sharpe-Ratio nach Bailey/Lopez de Prado: erwartetes Maximum von N Zufallsversuchen
     ~ SE * sqrt(2 ln N), plus Sicherheitsabstand z * SE);
  4. auch bei `cost_stress`-fachen Kosten (Standard 1,5) noch > 0.
Test: nur nach Validierung, genau einmal je Version (Register), gleiche Kriterien 1 und 4, dazu der Vergleich mit
Kaufen-und-Halten. Bestanden heisst NICHT "profitabel" - es heisst "auf ungesehenen Daten nicht widerlegt".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Callable, List, Mapping, Optional, Tuple

from trading.agents.kibo import KiboAgent
from trading.agents.yuzu import YuzuAgent
from trading.audit.performance import EquityPoint
from trading.backtest.data import MarketDataset
from trading.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from trading.backtest.strategy import Strategy
from trading.execution.costs import CostModel
from trading.research.registry import StrategyRegistry, StrategyVersion
from trading.research.splits import DataSplit, Period
from trading.strategies.benchmarks import buy_and_hold

D = Decimal
ZERO = D(0)
StrategyFactory = Callable[[], Strategy]


@dataclass(frozen=True)
class RunMetrics:
    strategy: str
    period: str
    start: datetime
    end: datetime
    trades: int
    net_result: Decimal
    return_pct: Decimal
    max_drawdown_pct: Decimal
    profit_factor: Optional[Decimal]
    fees: Decimal
    funding: Decimal
    steps: int  # Zahl der Renditen der Kapitalkurve
    sharpe_per_step: Optional[Decimal]
    result_hash: str


def step_returns(curve: Tuple[EquityPoint, ...], initial: Decimal) -> List[Decimal]:
    out: List[Decimal] = []
    previous = initial
    for p in curve:
        if previous > 0:
            out.append(p.equity / previous - 1)
        previous = p.equity
    return out


def sharpe_per_step(returns: List[Decimal]) -> Optional[Decimal]:
    if len(returns) < 2:
        return None
    mean = sum(returns, ZERO) / len(returns)
    var = sum(((r - mean) ** 2 for r in returns), ZERO) / (len(returns) - 1)
    return None if var == 0 else mean / var.sqrt()


def metrics_of(result: BacktestResult, period: str) -> RunMetrics:
    rep = result.report
    returns = step_returns(result.equity_curve, rep.initial_equity)
    return RunMetrics(result.strategy, period, result.start, result.end, rep.trades.trade_count, rep.net_result,
                      rep.equity.total_return_pct, rep.equity.max_drawdown_pct, rep.trades.profit_factor,
                      rep.total_fees, rep.total_funding, len(returns), sharpe_per_step(returns), result.result_hash)


def run_period(dataset: MarketDataset, period: Period, factory: StrategyFactory, config: BacktestConfig,
               kibo: Optional[KiboAgent] = None, yuzu: Optional[YuzuAgent] = None) -> Tuple[BacktestResult, RunMetrics]:
    result = BacktestEngine(dataset.window(period.start, period.end), factory(), config, kibo=kibo, yuzu=yuzu).run()
    return result, metrics_of(result, period.name)


def deflated_sharpe(sr: Optional[Decimal], steps: int, trials: int,
                    z: Decimal = D("1.645")) -> Tuple[bool, Optional[Decimal]]:
    """(besteht?, geforderte Sharpe je Schritt). Mehr Versuche -> hoehere Schwelle."""
    if trials < 1:
        raise ValueError("Mindestens ein Versuch.")
    if sr is None or steps < 2:
        return False, None
    se = ((1 + sr * sr / 2) / (steps - 1)).sqrt()
    penalty = D(str(math.sqrt(2 * math.log(trials)))) if trials > 1 else ZERO  # erwartetes Maximum von N Versuchen
    required = se * (z + penalty)
    return sr > required, required


def _fmt(value: Optional[Decimal]) -> str:
    return "-" if value is None else "%.4f" % value


def scaled_costs(costs: CostModel, factor: Decimal) -> CostModel:
    if not factor > 0:
        raise ValueError("Faktor muss positiv sein.")
    return replace(costs, taker_fee=costs.taker_fee * factor, maker_fee=costs.maker_fee * factor,
                   slippage_bps=costs.slippage_bps * factor, stop_slippage_bps=costs.stop_slippage_bps * factor)


@dataclass(frozen=True)
class Criteria:
    min_trades: int = 20
    z: Decimal = D("1.645")
    cost_stress: Decimal = D("1.5")


@dataclass(frozen=True)
class ValidationReport:
    version: str
    trials: int
    train: RunMetrics
    validation: RunMetrics
    stressed: RunMetrics
    required_sharpe: Optional[Decimal]
    passed: bool
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class TestReport:
    version: str
    test: RunMetrics
    stressed: RunMetrics
    buy_and_hold_return_pct: Decimal
    passed: bool
    reasons: Tuple[str, ...]


@dataclass
class Research:
    dataset: MarketDataset
    split: DataSplit
    registry: StrategyRegistry
    config: BacktestConfig = field(default_factory=BacktestConfig)
    criteria: Criteria = field(default_factory=Criteria)

    def _stressed(self) -> BacktestConfig:
        return replace(self.config, costs=scaled_costs(self.config.costs, self.criteria.cost_stress))

    def validate(self, family: str, params: Mapping[str, str], factory: StrategyFactory) -> ValidationReport:
        version = StrategyVersion(family, dict(params))
        self.registry.register(version)
        _, train = run_period(self.dataset, self.split.train, factory, self.config)  # nur zur Information
        _, val = run_period(self.dataset, self.split.validation, factory, self.config)
        _, stressed = run_period(self.dataset, self.split.validation, factory, self._stressed())
        trials = self.registry.trials(family) + 1  # dieser Versuch zaehlt mit
        ok_sharpe, required = deflated_sharpe(val.sharpe_per_step, val.steps, trials, self.criteria.z)
        reasons: List[str] = []
        if val.net_result <= 0:
            reasons.append("nach allen Handelskosten kein Gewinn (%.2f)" % val.net_result)
        if val.trades < self.criteria.min_trades:
            reasons.append("zu wenige Trades (%d < %d)" % (val.trades, self.criteria.min_trades))
        if not ok_sharpe:
            reasons.append("Sharpe je Schritt %s unter der Schwelle %s fuer %d Versuche"
                           % (_fmt(val.sharpe_per_step), _fmt(required), trials))
        if stressed.net_result <= 0:
            reasons.append("bei %s-fachen Kosten Verlust (%.2f)" % (self.criteria.cost_stress, stressed.net_result))
        passed = not reasons
        counted = self.registry.record_validation(version, {"validation": val, "stressed": stressed, "train": train},
                                                  passed, "; ".join(reasons) or "bestanden")
        return ValidationReport(version.key, counted, train, val, stressed, required, passed, tuple(reasons))

    def test(self, family: str, params: Mapping[str, str], factory: StrategyFactory) -> TestReport:
        version = StrategyVersion(family, dict(params))
        self.registry.open_test(version)  # genau einmal, nur nach Validierung
        _, test = run_period(self.dataset, self.split.test, factory, self.config)
        _, stressed = run_period(self.dataset, self.split.test, factory, self._stressed())
        hold = buy_and_hold(self.dataset.window(self.split.test.start, self.split.test.end),
                            self.config.initial_equity, self.config.costs)
        reasons: List[str] = []
        if test.net_result <= 0:
            reasons.append("Test: nach allen Handelskosten kein Gewinn (%.2f)" % test.net_result)
        if stressed.net_result <= 0:
            reasons.append("Test: bei %s-fachen Kosten Verlust" % self.criteria.cost_stress)
        passed = not reasons
        self.registry.record_test(version, {"test": test, "stressed": stressed,
                                            "buy_and_hold_return_pct": hold.return_pct}, passed,
                                  "; ".join(reasons) or "auf ungesehenen Daten nicht widerlegt")
        return TestReport(version.key, test, stressed, hold.return_pct, passed, tuple(reasons))


__all__ = ["Criteria", "Research", "RunMetrics", "StrategyFactory", "TestReport", "ValidationReport",
           "deflated_sharpe", "metrics_of", "run_period", "scaled_costs", "sharpe_per_step", "step_returns"]
