"""Forschungsrahmen (AP-18) und Bewertung (AP-19): getrennte Zeitraeume, Walk-forward, Versuchszaehlung, Testdaten nur
einmal, Freigabestatus, Kosten-Stresstest, Bewertung nach allen Kosten und je Marktphase, Nutzen von KIBO/YUZU."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from bt_helpers import BTC, ETHF, series, synthetic
from helpers import T0

from trading.audit.ai_value import Verdict
from trading.audit.costs import CostCategory, CostLedger, OperatingCost
from trading.backtest import BacktestConfig, BacktestEngine, MarketDataset, SyntheticDataSource, SyntheticSpec
from trading.domain.models import FundingEvent
from trading.execution.costs import CostModel
from trading.research.evaluation import agent_value, assess, regime_at, walk_forward_stability
from trading.research.experiment import Criteria, Research, deflated_sharpe, scaled_costs, sharpe_per_step
from trading.research.registry import RegistryError, Status, StrategyRegistry, StrategyVersion
from trading.research.splits import split_periods, walk_forward
from trading.strategies import BreakoutStrategy

D = Decimal
DAY = timedelta(days=1)


def trending(drift=0.0015, days=120, seed=3):
    src = SyntheticDataSource(seed=seed, specs={BTC: SyntheticSpec(start_price=D(100), drift=drift, volatility=0.004)})
    return MarketDataset.build([src.load(BTC, 3600, T0, T0 + days * DAY)])


# ------------------------------------------------------------------ Zeitraeume


def test_train_validation_and_test_follow_each_other_with_a_gap():
    s = split_periods(T0, T0 + 100 * DAY, gap=DAY)
    assert s.train.start == T0 and s.train.end < s.validation.start < s.validation.end < s.test.start
    assert s.validation.start - s.train.end == DAY and s.test.start - s.validation.end == DAY
    assert s.test.end == T0 + 100 * DAY and s.train.end - s.train.start == (98 * DAY) * 0.6
    for bad in (dict(train=D("0.8"), validation=D("0.3")), dict(train=D(0)), dict(gap=-DAY), dict(gap=60 * DAY)):
        with pytest.raises(ValueError):
            split_periods(T0, T0 + 100 * DAY, **bad)


def test_walk_forward_tests_always_after_training_and_never_overlapping():
    folds = walk_forward(T0, T0 + 100 * DAY, train=30 * DAY, test=10 * DAY, gap=DAY)
    assert len(folds) == 6
    for f in folds:
        assert f.train.end + DAY == f.test.start and f.test.end <= T0 + 100 * DAY
    assert all(a.test.end <= b.test.start for a, b in zip(folds, folds[1:]))
    with pytest.raises(ValueError):
        walk_forward(T0, T0 + 100 * DAY, train=30 * DAY, test=10 * DAY, step=5 * DAY)  # Tests wuerden ueberlappen
    with pytest.raises(ValueError):
        walk_forward(T0, T0 + 10 * DAY, train=30 * DAY, test=10 * DAY)


def test_dataset_window_contains_only_its_period_and_knows_no_future_funding():
    ds = synthetic(days=10)
    w = ds.window(T0 + 2 * DAY, T0 + 5 * DAY)
    for s in w.series:
        assert s.candles[0].open_time >= T0 + 2 * DAY and s.candles[-1].close_time <= T0 + 5 * DAY
        assert all(T0 + 2 * DAY < f.time <= T0 + 5 * DAY for f in s.funding)
    eth_before = [f for f in next(s for s in ds.series if s.instrument == ETHF).funding if f.time <= T0 + 2 * DAY]
    assert next(s for s in w.series if s.instrument == ETHF).initial_funding_rate == eth_before[-1].rate
    with pytest.raises(ValueError):
        ds.window(T0 + 50 * DAY, T0 + 60 * DAY)
    with pytest.raises(ValueError):
        ds.window(T0 + DAY, T0)


def test_window_starts_with_the_last_funding_rate_before_it_not_one_from_inside():
    hour = timedelta(hours=1)
    rows = [[100, 100, 100, 100]] * 24
    fund = [FundingEvent(ETHF, T0 + 8 * hour, D("0.0001"), D(100)), FundingEvent(ETHF, T0 + 16 * hour, D("0.0009"), D(100))]
    ds = MarketDataset.build([series(ETHF, rows, funding=fund, initial_rate=D("0.0005"))])
    w = ds.window(T0 + 10 * hour, T0 + 20 * hour)
    assert w.series[0].initial_funding_rate == D("0.0001")  # bekannt vor dem Zeitraum - nicht 0,0009 aus ihm
    assert ds.window(T0, T0 + 12 * hour).series[0].initial_funding_rate == D("0.0005")  # davor: Startwert der Reihe


# ------------------------------------------------------------------ Register


def test_registry_counts_every_trial_and_allows_the_test_data_only_once(tmp_path):
    path = tmp_path / "register.json"
    reg = StrategyRegistry(path)
    v1, v2 = StrategyVersion("breakout", {"entry": "20"}), StrategyVersion("breakout", {"entry": "30"})
    for v in (v1, v2):
        reg.register(v)
    assert reg.status(v1) is Status.IDEA and v1.key != v2.key
    assert reg.record_validation(v1, {"net": D(1)}, True, "ok") == 1
    assert reg.record_validation(v2, {"net": D(-1)}, False, "Verlust") == 2  # verworfene Versuche zaehlen mit
    with pytest.raises(RegistryError):
        reg.open_test(v2)  # nur nach bestandener Validierung
    with pytest.raises(RegistryError):
        reg.record_test(v1, {}, True, "")  # nicht ohne open_test
    reg.open_test(v1)
    with pytest.raises(RegistryError, match="schon angesehen"):
        reg.open_test(v1)
    reg.record_test(v1, {"net": D(2)}, True, "ok")
    with pytest.raises(RegistryError):
        reg.record_validation(v1, {}, True, "")  # nach dem Test keine weitere Validierung derselben Version
    with pytest.raises(RegistryError):
        reg.approve_for_paper(v1, " ")
    reg.approve_for_paper(v1, "Eigner")
    reloaded = StrategyRegistry(path)  # Zustand ueberlebt einen Neustart
    assert reloaded.status(v1) is Status.APPROVED_PAPER and reloaded.trials("breakout") == 2
    with pytest.raises(RegistryError):
        reloaded.open_test(v1)
    assert json.loads(path.read_text())["versions"][v1.key]["approved_by"] == "Eigner"


def test_a_failed_or_missing_test_can_never_be_approved():
    reg = StrategyRegistry()
    v = StrategyVersion("x", {})
    reg.register(v)
    reg.record_validation(v, {}, True, "")
    with pytest.raises(RegistryError):
        reg.approve_for_paper(v, "Eigner")
    reg.open_test(v)
    reg.record_test(v, {}, False, "Verlust")
    with pytest.raises(RegistryError):
        reg.approve_for_paper(v, "Eigner")
    with pytest.raises(RegistryError):
        reg.status(StrategyVersion("unbekannt", {}))


# ------------------------------------------------------------------ Kriterien


def test_more_trials_raise_the_bar():
    ok1, req1 = deflated_sharpe(D("0.1"), 1000, 1)
    ok50, req50 = deflated_sharpe(D("0.1"), 1000, 50)
    assert ok1 and not ok50 and req50 > req1
    assert deflated_sharpe(None, 1000, 1) == (False, None) and deflated_sharpe(D(1), 1, 1)[0] is False
    with pytest.raises(ValueError):
        deflated_sharpe(D(1), 10, 0)
    assert sharpe_per_step([D(1)]) is None and sharpe_per_step([D("0.01")] * 5) is None  # keine Streuung


def test_cost_stress_scales_every_cost():
    c = scaled_costs(CostModel(), D(2))
    assert (c.taker_fee, c.maker_fee, c.slippage_bps, c.stop_slippage_bps) == (D("0.0012"), D("0.0004"), D(4), D(10))
    with pytest.raises(ValueError):
        scaled_costs(CostModel(), D(0))


def test_random_data_is_rejected_with_reasons_and_a_real_trend_is_validated_and_tested_once():
    noise = trending(drift=0.0)
    r = Research(noise, split_periods(noise.start, noise.end, gap=DAY), StrategyRegistry(), criteria=Criteria(min_trades=5))
    rep = r.validate("breakout", {"entry": "20"}, BreakoutStrategy)
    assert not rep.passed and any("kein Gewinn" in x for x in rep.reasons) and rep.trials == 1
    with pytest.raises(RegistryError):
        r.test("breakout", {"entry": "20"}, BreakoutStrategy)  # verworfen: keine Testdaten

    trend = trending(drift=0.0015)
    reg = StrategyRegistry()
    research = Research(trend, split_periods(trend.start, trend.end, gap=DAY), reg, criteria=Criteria(min_trades=5))
    rep = research.validate("breakout", {"entry": "20"}, BreakoutStrategy)
    assert rep.passed and rep.validation.net_result > 0 and rep.stressed.net_result > 0
    assert rep.validation.start >= research.split.validation.start and rep.train.end <= research.split.train.end
    test = research.test("breakout", {"entry": "20"}, BreakoutStrategy)
    assert test.passed and reg.status(StrategyVersion("breakout", {"entry": "20"})) is Status.TESTED
    with pytest.raises(RegistryError):
        research.test("breakout", {"entry": "20"}, BreakoutStrategy)  # Testdaten nur einmal


def test_profit_that_does_not_survive_higher_costs_is_rejected():
    trend = trending(drift=0.0015)
    research = Research(trend, split_periods(trend.start, trend.end, gap=DAY), StrategyRegistry(),
                        criteria=Criteria(min_trades=5, cost_stress=D(40)))
    rep = research.validate("breakout", {"entry": "20"}, BreakoutStrategy)
    assert rep.validation.net_result > 0 and rep.stressed.net_result <= 0
    assert not rep.passed and any("fachen Kosten Verlust" in x for x in rep.reasons)


def test_every_variant_counts_and_the_threshold_grows():
    trend = trending(drift=0.0015, days=90)
    research = Research(trend, split_periods(trend.start, trend.end, gap=DAY), StrategyRegistry(),
                        criteria=Criteria(min_trades=5))
    reports = [research.validate("breakout", {"entry": str(n)}, lambda n=n: BreakoutStrategy(entry=n, exit=10))
               for n in (15, 20, 25, 30)]
    assert [r.trials for r in reports] == [1, 2, 3, 4]
    required = [r.required_sharpe for r in reports if r.required_sharpe is not None]
    assert required and required[-1] > required[0] * D("1.5")  # Schwelle steigt mit der Zahl der Versuche


# ------------------------------------------------------------------ Bewertung (AKI)


def test_assessment_after_all_costs_and_per_market_regime():
    ds = trending(drift=0.0015, days=60)
    result = BacktestEngine(ds, BreakoutStrategy(), BacktestConfig()).run()
    ledger = CostLedger()
    ledger.add(OperatingCost(CostCategory.SERVER, D("60"), ds.start, ds.end, "Server"))
    a = assess(result, ds, costs=ledger, min_trades_per_regime=2)
    assert a.trades == result.report.trades.trade_count > 0
    assert a.operating_cost > D("59") and a.net_after_all_costs == a.trading_net - a.ai_cost - a.operating_cost
    assert sum(r.trades for r in a.by_regime) == a.trades
    assert any(r.regime.startswith("UP/") for r in a.by_regime)  # Aufwaertstrend erkannt
    assert a.regime_stability is not None and D(0) <= a.regime_stability <= D(1)


def test_regime_at_entry_uses_only_the_past():
    ds = trending(drift=0.0015, days=30)
    early = regime_at(ds, BTC, T0 + 2 * DAY)  # zu wenig Historie
    assert early == "unbekannt" and regime_at(ds, ETHF, T0 + 20 * DAY) == "unbekannt"
    assert regime_at(ds, BTC, T0 + 20 * DAY).startswith("UP/")


def test_walk_forward_stability_and_agent_value_are_honest_about_too_little_data():
    ds = synthetic(days=60, seed=8)
    folds = walk_forward(ds.start, ds.end, train=20 * DAY, test=10 * DAY)
    results, positive = walk_forward_stability(ds, folds, BreakoutStrategy, BacktestConfig())
    assert len(results) == len(folds) == 4 and D(0) <= positive <= D(1)
    verdict = agent_value(ds, folds, BreakoutStrategy, BacktestConfig())
    assert verdict.samples == 4 and verdict.verdict is Verdict.TOO_LITTLE_DATA  # 4 Fenster beweisen nichts


def test_research_demo_refuses_any_mode_other_than_paper(monkeypatch):
    from trading.config import ConfigError
    from trading.research.__main__ import main

    monkeypatch.setenv("TRADING_MODE", "live")
    with pytest.raises(ConfigError):
        main()
