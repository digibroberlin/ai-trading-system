"""Werkzeuge der Untersuchungsreihe AP-20: Vorlauf ohne Blick in die Zukunft, Schliessen am Ende mit Kosten, Ergebnis je
Setup und Stufe, Not-Aus-Zeitpunkt, Latenz, Schatten-Agenten, Kostenszenarien, KI-Kostenszenario, Kennzahlen-Karte,
Testschutz, Statistik und Bericht - mit handgebauten bzw. erfundenen Daten (kein Netz, kein Handel)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from bt_helpers import BTC, NO_SLIP, Scripted, config, flat, long, series
from helpers import T0

from trading.agents.base import AgentName
from trading.agents.contracts import AgentOpinion
from trading.agents.kibo import KiboAgent
from trading.backtest import BacktestEngine, BacktestError, MarketDataset, StrategyDecision
from trading.backtest.data import SyntheticDataSource, SyntheticSpec
from trading.backtest.instrumentation import ShadowKibo, Stopwatch
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.domain.models import ExitReason
from trading.domain.reasons import RejectReason
from trading.marketdata.localfile import LocalSeries, Provenance
from trading.research import robust
from trading.research.campaign import (
    SCENARIOS,
    VARIANTS,
    Bundle,
    Freeze,
    GuardViolation,
    cell_key,
    guard_test,
    run_cell,
    save_cell,
    standard_strategies,
    stressed,
    would_ask_ai,
)
from trading.research.splits import Period
from trading.safety.limits import DEFAULT_LIMITS

D = Decimal
HOUR = timedelta(hours=1)
UTC = timezone.utc


def engine_run(ohlc, plan, cfg=None, **kw):
    data = MarketDataset.build([series(BTC, ohlc)])
    strategy = Scripted(plan)
    return BacktestEngine(data, strategy, cfg or config(), **kw).run(), strategy


# ---------------------------------------------------------------------------------------------- Vorlauf

def test_warm_up_bars_are_history_only_no_decision_no_curve_point():
    ohlc = flat(10)
    start = T0 + 5 * HOUR
    plan = {T0 + 2 * HOUR: long(), start + HOUR: long()}
    r, strategy = engine_run(ohlc, plan, config(trade_from=start))
    assert [v.now for v in strategy.views][0] == start  # erste Entscheidung erst ab trade_from
    assert len(strategy.views[0].bars[BTC]) == 5  # ... aber mit der ganzen Vorgeschichte
    assert r.start == start and r.equity_curve[0].time == start
    assert [t.opened_at for t in r.trades] + [p.opened_at for p in r.open_positions] == [start + HOUR]


def test_warm_up_never_changes_decisions_after_the_start():
    """Gleiche Daten ab dem Start -> gleiche Entscheidungen, egal wie viel Vorlauf: der Vorlauf ist Vergangenheit."""
    data = SyntheticDataSource(seed=11, specs={BTC: SyntheticSpec(start_price=D(30000), volatility=0.01)})
    full = MarketDataset.build([data.load(BTC, 3600, T0, T0 + timedelta(days=30))])
    start = T0 + timedelta(days=12)
    breakout = standard_strategies(["breakout"])[0]
    runs = []
    for warm in (timedelta(days=10), timedelta(days=11)):
        window = full.window(start - warm, full.end)
        runs.append(BacktestEngine(window, breakout.factory(), config(trade_from=start)).run())
    a, b = runs
    assert a.trades == b.trades and a.equity_curve == b.equity_curve and a.setup_log == b.setup_log


def test_trade_from_must_lie_inside_the_data():
    with pytest.raises(BacktestError):
        engine_run(flat(5), {}, config(trade_from=T0 + 10 * HOUR))
    with pytest.raises(ValueError):
        config(trade_from=datetime(2024, 1, 1))  # noqa: DTZ001 - ohne Zeitzone ist hier der Fehlerfall


# ---------------------------------------------------------------------------------------------- Ende, Stufen, Not-Aus

def test_open_positions_are_closed_at_the_end_with_all_exit_costs():
    ohlc = [[100, 100, 100, 100], [100, 101, 99.5, 100.5], [101, 102, 100.5, 101]]
    kept, _ = engine_run(ohlc, {T0 + HOUR: long()})
    closed, _ = engine_run(ohlc, {T0 + HOUR: long()}, config(close_at_end=True))
    assert kept.open_positions and not closed.open_positions
    (t,) = closed.trades
    assert t.exit_reason is ExitReason.MANUAL and t.exit_price == D(101) and t.fees > kept.open_positions[0].entry_fee
    assert closed.report.final_equity == closed.report.initial_equity + t.net_pnl
    assert closed.report.net_result < kept.report.net_result  # die Ausstiegsgebuehr wird jetzt bezahlt
    assert closed.equity_curve[-1].equity == closed.report.final_equity


def test_every_setup_is_logged_with_the_stage_where_it_ended():
    ohlc = [[100, 100, 100, 100], [100, 101, 99.5, 100.5], [100.6, 101, 100, 100.5], [101, 101, 101, 101]]
    plan = {T0 + HOUR: long(), T0 + 2 * HOUR: long(stop=95, tp=110)}
    r, _ = engine_run(ohlc, plan)
    stages = [(s.time, s.stage) for s in r.setup_log]
    assert stages[0] == (T0 + HOUR, "FILLED")
    assert sum(r.blocked_by_stage.values()) == r.setups_proposed == len(r.setup_log)
    assert r.blocked_by_stage["FILLED"] == r.setups_executed


def test_the_first_emergency_stop_is_reported_with_its_time():
    # Kurssturz durch den Stop: weit mehr als 3 % Tagesverlust -> Not-Aus
    ohlc = [[100, 100, 100, 100], [100, 100.5, 99, 100], [50, 51, 49, 50], [50, 50, 50, 50], [50, 50, 50, 50]]
    r, _ = engine_run(ohlc, {T0 + HOUR: long(), T0 + 3 * HOUR: long(stop=45, tp=60)})
    assert r.halted_at is not None and r.halted_at >= T0 + 2 * HOUR and r.halt_reason
    after = [s for s in r.setup_log if s.time >= r.halted_at]
    assert after and all(s.stage != "FILLED" for s in after)  # danach keine neue Position mehr


def test_measuring_latency_changes_no_result():
    data = SyntheticDataSource(seed=3, specs={BTC: SyntheticSpec(start_price=D(30000), volatility=0.01)})
    dataset = MarketDataset.build([data.load(BTC, 3600, T0, T0 + timedelta(days=20))])
    spec = standard_strategies(["breakout"])[0]
    plain = BacktestEngine(dataset, spec.factory(), config()).run()
    timed = BacktestEngine(dataset, spec.factory(), config(), measure=True).run()
    assert plain.result_hash == timed.result_hash and plain.to_json() == timed.to_json() and plain == timed
    assert {"MOMO", "RIN", "SORA", "KIBO", "YUZU", "ENTSCHEIDUNG_GESAMT"} <= set(timed.timings) and not plain.timings
    assert timed.timings["RIN"]["calls"] >= 1 and timed.timings["RIN"]["mean_us"] > 0


def test_stopwatch_summary():
    w = Stopwatch()
    for s in (0.001, 0.002, 0.003):
        w.add("X", s)
    s = w.summary()["X"]
    assert s["calls"] == 3 and abs(s["mean_us"] - 2000) < 1e-6 and s["max_us"] == pytest.approx(3000)


# ---------------------------------------------------------------------------------------------- Schatten

class AlwaysNo(KiboAgent):
    def evaluate(self, setup, context):
        return AgentOpinion(AgentName.KIBO, setup.setup_id, False, D(0), ("nein",), (RejectReason.AGAINST_TREND,))


def test_a_shadow_agent_never_blocks_but_remembers_its_verdict():
    ohlc = [[100, 100, 100, 100], [100, 101, 99.5, 100.5], [100.5, 101, 100, 100.5]]
    data = MarketDataset.build([series(BTC, ohlc)])
    blocked = BacktestEngine(data, Scripted({T0 + HOUR: long()}), config(), kibo=AlwaysNo()).run()
    shadow = ShadowKibo(AlwaysNo())
    passed = BacktestEngine(data, Scripted({T0 + HOUR: long()}), config(), kibo=shadow).run()
    assert blocked.setups_executed == 0 and blocked.blocked_by_stage == {"KIBO": 1}
    assert passed.setups_executed == 1
    (verdict,) = shadow.verdicts.values()
    assert verdict.approve is False and verdict.codes == ("AGAINST_TREND",)


# ---------------------------------------------------------------------------------------------- Kampagne

def synthetic_bundle(days=60, seed=21):
    inst = InstrumentId(Exchange.SYNTHETIC, MarketType.SPOT, "BTC-USD")
    source = SyntheticDataSource(seed=seed, specs={inst: SyntheticSpec(start_price=D(30000), volatility=0.012)})
    s = source.load(inst, 3600, T0, T0 + timedelta(days=days))
    provenance = Provenance("erfunden (Test)", "-", (), "keine Verdichtung", "keine (Spot)")
    return Bundle(LocalSeries(s, provenance, 0, 0))


def test_stress_scales_every_cost_including_spread_and_funding():
    f = SCENARIOS["F"]
    x = stressed(f, D("2"))
    assert x.costs.taker_fee == 2 * f.costs.taker_fee and x.costs.stop_slippage_bps == 2 * f.costs.stop_slippage_bps
    assert x.spread_bps == 2 * f.spread_bps and x.funding_per_8h == 2 * f.funding_per_8h
    assert SCENARIOS["Fx"].spread_bps == D("3.0") and SCENARIOS["Sx"].funding_per_8h is None


def test_variants_wire_neutral_rule_and_shadow_agents():
    from trading.agents.kibo_rules import RuleBasedKibo
    from trading.agents.neutral import NeutralKibo, NeutralYuzu
    from trading.agents.yuzu_rules import RuleBasedYuzu
    from trading.backtest.instrumentation import ShadowYuzu

    kinds = {k: tuple(type(a) for a in v.agents()) for k, v in VARIANTS.items()}
    assert kinds["V0"] == (NeutralKibo, NeutralYuzu) and kinds["A"] == (RuleBasedKibo, NeutralYuzu)
    assert kinds["B"] == (NeutralKibo, RuleBasedYuzu) and kinds["C"] == kinds["D"] == (RuleBasedKibo, RuleBasedYuzu)
    assert kinds["S"] == (ShadowKibo, ShadowYuzu) and VARIANTS["D"].ai_overlay and not VARIANTS["C"].ai_overlay


def test_ai_would_only_be_asked_for_uncertain_soft_rule_verdicts():
    assert would_ask_ai(True, D("0.5"), ("ok",), ())  # Sicherheit 0,65 < 0,80
    assert would_ask_ai(False, D("0.25"), ("schwach",), ("TECHNICALS_WEAK",))  # 0,60
    assert not would_ask_ai(True, D("0.7"), ("ok",), ())  # 0,85: Regeln allein
    assert not would_ask_ai(False, D("0"), ("x",), ("AGAINST_TREND",))  # hart: nie
    assert not would_ask_ai(True, D("0.5"), ("KIBO: zu wenig Kerzen fuer ein Urteil (5)",), ())


def test_a_cell_reports_consistent_costs_counts_and_both_net_results():
    bundle = synthetic_bundle()
    period = Period("probe", T0 + timedelta(days=20), T0 + timedelta(days=60))
    spec = standard_strategies(["breakout"])[0]
    cells = {v: run_cell(bundle, period, spec, VARIANTS[v], SCENARIOS["F"]) for v in ("V0", "D", "S")}
    for cell in cells.values():
        c = cell.card
        assert c.trades > 5 and c.days == 40
        assert c.gross_before_costs == c.trading_net + c.fees + c.funding + c.spread_slippage
        assert c.economic_net == c.trading_net - c.operating_cost - c.ai_cost_typical
        assert sum(c.by_stage.values()) == c.signals and c.by_stage.get("FILLED", 0) == c.executed
        assert c.long_trades + c.short_trades == c.trades and c.long_net + c.short_net == c.trading_net
        assert sum(n for n, _ in c.by_regime.values()) == c.trades and c.operating_cost == D("7.89")
        assert cell.daily[0][0] >= period.start.date().isoformat()
    d = cells["D"].card
    price = D("0.000090")  # Haiku 5.5: 600 x 0,10 + 60 x 0,50 je Mio. Tokens
    assert d.ai_reviews > 0 and d.ai_cost_typical == d.ai_reviews * price and d.ai_cost_ceiling > d.ai_cost_typical
    assert cells["V0"].card.ai_reviews == 0 and cells["V0"].card.trading_net == cells["S"].card.trading_net
    shadow = cells["S"].trades
    assert all(t.kibo_would_approve is not None and t.yuzu_would_approve is not None for t in shadow)
    assert all(t.kibo_would_approve is None for t in cells["V0"].trades)


def test_cells_need_enough_warm_up_and_the_right_interval():
    bundle = synthetic_bundle(days=20)
    spec = standard_strategies(["breakout"])[0]
    with pytest.raises(ValueError, match="Vorlauf"):
        run_cell(bundle, Period("p", T0 + timedelta(days=2), T0 + timedelta(days=20)), spec, VARIANTS["V0"],
                 SCENARIOS["S"])
    with pytest.raises(ValueError, match="Kerzen"):
        run_cell(bundle, Period("p", T0 + timedelta(days=10), T0 + timedelta(days=20)), replace(spec, interval_s=900),
                 VARIANTS["V0"], SCENARIOS["S"])


def test_test_data_only_for_frozen_cells_and_only_once(tmp_path):
    results, freeze_path = tmp_path / "res", tmp_path / "freeze.json"
    key = cell_key("test", "F", "breakout", "C")
    guard_test(freeze_path, results, cell_key("validierung", "F", "breakout", "C"))  # ausserhalb des Tests frei
    with pytest.raises(GuardViolation, match="Einfrieren"):
        guard_test(freeze_path, results, key)
    Freeze(datetime(2026, 10, 17, tzinfo=UTC), ("F|breakout|C",), "Test").save(freeze_path)
    with pytest.raises(GuardViolation, match="einmal"):
        Freeze(datetime(2026, 10, 17, tzinfo=UTC), ("F|trend|C",), "zweiter Versuch").save(freeze_path)
    guard_test(freeze_path, results, key)
    with pytest.raises(GuardViolation, match="nicht eingefroren"):
        guard_test(freeze_path, results, cell_key("test", "F", "trend", "C"))
    bundle = synthetic_bundle()
    cell = run_cell(bundle, Period("test", T0 + timedelta(days=20), T0 + timedelta(days=40)),
                    standard_strategies(["breakout"])[0], VARIANTS["C"], SCENARIOS["F"])
    save_cell(results, cell)
    with pytest.raises(GuardViolation, match="schon getestet"):
        guard_test(freeze_path, results, key)
    data = json.loads(freeze_path.read_text())
    data["cells"].append("F|trend|C")  # nachtraeglich erweitert -> Pruefsumme passt nicht
    freeze_path.write_text(json.dumps(data))
    with pytest.raises(GuardViolation, match="veraendert"):
        Freeze.load(freeze_path)


# ---------------------------------------------------------------------------------------------- Statistik

def test_risk_figures_by_hand():
    days = [(date(2024, 1, d), e) for d, e in ((1, 110.0), (2, 99.0), (3, 120.0))]
    assert robust.max_drawdown(days, 100.0) == pytest.approx(10.0)
    assert robust.returns_of(days, 100.0) == pytest.approx([0.1, -0.1, 120 / 99 - 1])
    assert robust.cagr(100.0, 121.0, 730) == pytest.approx(10.0)
    assert robust.sharpe([0.01, 0.01]) is None and robust.sharpe([0.01, -0.01, 0.02]) > 0
    assert robust.daily_pnl(days, 100.0) == [(date(2024, 1, 1), 10.0), (date(2024, 1, 2), -11.0),
                                             (date(2024, 1, 3), 21.0)]


def test_block_bootstrap_is_reproducible_and_brackets_the_mean():
    values = [((i * 37) % 11 - 5) / 10 for i in range(200)]
    a = robust.block_bootstrap_mean(values, block=10)
    assert a == robust.block_bootstrap_mean(values, block=10)
    assert a.low <= a.estimate <= a.high and a.samples == 200
    assert robust.block_bootstrap_mean(values[:15], block=10) is None
    shifted = robust.block_bootstrap_mean([v + 1 for v in values], block=10)
    assert shifted.excludes_zero_from_above


def test_paired_difference_uses_only_common_days():
    day = [date(2024, 1, 1) + timedelta(days=i) for i in range(60)]
    a = [(d, float(i)) for i, d in enumerate(day)]
    b = [(d, float(i) - 1) for i, d in enumerate(day) if i > 0]
    diff = robust.paired_difference(a, b, block=5)
    assert diff.samples == 59 and diff.estimate == pytest.approx(1.0) and diff.low == pytest.approx(1.0)


def test_deflated_sharpe_needs_more_with_more_trials():
    rets = [0.002 + (0.01 if i % 3 else -0.012) for i in range(400)]
    ok1, sr, req1 = robust.deflated_sharpe_ok(rets, 1)
    _, _, req50 = robust.deflated_sharpe_ok(rets, 50)
    assert req50 > req1 and sr is not None


def test_report_lists_every_cell_and_the_validation_criteria(tmp_path):
    from trading.research.campaign_report import write_report

    bundle = synthetic_bundle(days=60)
    period = Period("validierung", T0 + timedelta(days=20), T0 + timedelta(days=60))
    spec = standard_strategies(["breakout"])[0]
    for scenario in ("F", "Fx"):
        for v in ("V0", "C", "S"):
            save_cell(tmp_path / "res", run_cell(bundle, period, spec, VARIANTS[v], SCENARIOS[scenario]))
    # Kaufen-und-Halten rechnet der Bericht ueber den festen Validierungsabschnitt - hier nicht vorhanden; daher eigener
    # kleiner Abschnitt ueber PERIODS ist nicht moeglich: nur die Tabellen ohne Vergleichsmassstab pruefen.
    import trading.research.campaign_report as rep

    original = rep.benchmark_table
    rep.benchmark_table = lambda *a, **k: ["(Kaufen-und-Halten im Test ausgelassen)"]
    try:
        out = write_report(tmp_path / "res", tmp_path / "bericht.md", bundle)
    finally:
        rep.benchmark_table = original
    text = out.read_text()
    assert "Abschnitt validierung" in text and "Validierungskriterien" in text and "Schatten" in text
    assert text.count("| breakout | V0 |") >= 3 and "C − V0" in text


def test_simulated_monthly_review_releases_a_halt_on_the_first_of_next_month_with_the_same_limits():
    from trading.backtest.engine import next_month

    # T0 = 2025-01-01 12:00; Sturz durch den Stop am ersten Tag -> Not-Aus; Plan: am 2. Januar und am 1. Februar kaufen
    n = 36 * 24
    ohlc = [[100, 100, 100, 100], [100, 100.5, 99, 100], [50, 51, 49, 50]] + flat(n - 3, 50)
    jan2 = T0 + timedelta(days=1)
    feb1 = datetime(2025, 2, 1, 1, tzinfo=UTC)
    plan = {T0 + HOUR: long(), jan2: long(stop=45, tp=60), feb1: long(stop=45, tp=60)}
    strict, _ = engine_run(ohlc, plan)
    review, _ = engine_run(ohlc, plan, config(halt_review="monatlich"))
    assert len(strict.halts) == 1 and strict.halts[0][2] is None  # streng: nie freigegeben
    assert [s.stage for s in strict.setup_log] == ["FILLED", "EXECUTOR", "EXECUTOR"]
    halt_time, reason, released = review.halts[0]
    assert reason and released == datetime(2025, 2, 1, tzinfo=UTC) == next_month(halt_time)
    assert [s.stage for s in review.setup_log] == ["FILLED", "EXECUTOR", "FILLED"]  # Januar gesperrt, Februar frei
    with pytest.raises(ValueError):
        config(halt_review="sofort")
    assert next_month(datetime(2025, 12, 31, 23, tzinfo=UTC)) == datetime(2026, 1, 1, tzinfo=UTC)
