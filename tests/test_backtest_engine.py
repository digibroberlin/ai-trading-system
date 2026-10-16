"""Backtesting-Engine: Ablauf, Ausfuehrungskurse, Kosten, Limits, Protokoll - von Hand nachgerechnet.

Grundfall der Handrechnungen (Stundenkerzen ab 12:00, Spread 0, Kostenmodell ohne Slippage):
  Entscheidung um 13:00 (Schluss der ersten Kerze), Ausfuehrung zum Eroeffnungskurs 100 der Kerze 13-14 Uhr.
  Long, Stop 95, Ziel 110, Kapital 10 000, SORA plant 0,4 % = 40.
  Verlust je Stueck 5 + 0,06 + 0,057 = 5,117  ->  Menge 40 / 5,117 = 7,8170 (abgerundet)
  Einstiegsgebuehr 7,817 x 100 x 0,06 % = 0,46902
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from bt_helpers import (
    BTC,
    ETHF,
    FREE,
    HOUR,
    NO_SLIP,
    Scripted,
    config,
    flat,
    leave,
    long,
    series,
    short,
    synthetic,
)
from helpers import D, T0, inst

from trading.audit.events import EventType
from trading.audit.event_log import InMemoryEventLog
from trading.backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestError,
    EntryProposal,
    MarketDataset,
    Strategy,
    StrategyDecision,
    comparison_table,
)
from trading.backtest.reference import SmaCrossStrategy
from trading.config import ConfigError
from trading.domain.models import ExitReason, FundingEvent, MarketType, Side
from trading.execution.costs import CostModel
from trading.safety.limits import DEFAULT_LIMITS

QTY = D("7.8170")
ENTRY_FEE = D("0.46902")
AT_13 = T0 + HOUR


def run(ohlc_or_dataset, plan, cfg=None, **engine_kw):
    data = ohlc_or_dataset if isinstance(ohlc_or_dataset, MarketDataset) else MarketDataset.build([series(BTC, ohlc_or_dataset)])
    strategy = plan if isinstance(plan, Strategy) else Scripted(plan)
    logs = []

    def log_factory(clock):
        logs.append(InMemoryEventLog(clock))
        return logs[0]

    result = BacktestEngine(data, strategy, cfg or config(), log_factory=log_factory, **engine_kw).run()
    return result, strategy, logs[0]


# ------------------------------------------------------------------ Ausfuehrung und Ausstiege


def test_entry_fills_at_the_next_open_and_take_profit_closes_the_trade():
    ohlc = [[100, 100, 100, 100], [100, 101, 99.5, 100.5], [100.5, 111, 100.5, 109], [109, 109, 108, 108.5]]
    r, _, log = run(ohlc, {AT_13: long()})
    (t,) = r.trades
    assert t.quantity == QTY and t.entry_price == D(100) and t.opened_at == AT_13
    assert t.exit_reason is ExitReason.TAKE_PROFIT and t.exit_price == D(110) and t.closed_at == T0 + 3 * HOUR
    assert t.gross_pnl == D("78.17")
    assert t.fees == ENTRY_FEE + D("0.171974")  # Ausstieg maker: 7,817 x 110 x 0,02 %
    assert t.net_pnl == D("77.529006")
    assert r.report.final_equity == D("10077.529006") and r.report.realized_pnl == D("77.529006")
    assert r.report.unrealized_pnl == 0 and r.open_positions == ()
    assert (r.setups_proposed, r.setups_executed, r.blocked_by_reason) == (1, 1, {})
    filled = log.of_type(EventType.ORDER_FILLED)[0].payload
    assert filled["position"]["risk_at_open"] == "39.99958900"  # 7,817 x 5,117: knapp unter den geplanten 40


def test_the_bar_in_which_the_position_was_opened_can_already_stop_it_out():
    ohlc = [[100, 100, 100, 100], [100, 100.5, 94, 96], [96, 97, 95.5, 96]]
    r, _, _ = run(ohlc, {AT_13: long()})
    (t,) = r.trades
    assert t.exit_reason is ExitReason.STOP_LOSS and t.exit_price == D(95) and t.closed_at == T0 + 2 * HOUR
    assert t.net_pnl == -t.risk_at_open == D("-39.999589")  # genau der geplante Hoechstverlust, nicht mehr


def test_stop_wins_when_one_bar_touches_stop_and_target():
    ohlc = [[100, 100, 100, 100], [100, 111, 94, 105], [105, 105, 105, 105]]
    r, _, _ = run(ohlc, {AT_13: long()})
    assert [t.exit_reason for t in r.trades] == [ExitReason.STOP_LOSS]  # keine optimistische Annahme


def test_gap_below_the_stop_fills_at_the_worse_open():
    ohlc = [[100, 100, 100, 100], [100, 100.5, 99, 100], [90, 91, 89, 90], [90, 90, 90, 90]]
    r, _, _ = run(ohlc, {AT_13: long()})
    (t,) = r.trades
    assert t.exit_price == D(90) and t.gross_pnl == D("-78.17") and t.net_pnl < -t.risk_at_open


def test_gap_between_decision_and_execution_is_not_ignored():
    # Entscheidung beim Schlusskurs 100; die naechste Kerze eroeffnet bei 100,4: gekauft wird zu 100,4.
    ohlc = [[100, 100, 100, 100], [100.4, 101, 100, 100.5], [100.5, 100.5, 100.5, 100.5]]
    r, _, _ = run(ohlc, {AT_13: long()})
    assert r.open_positions[0].entry_price == D("100.4")
    # ... und springt der Kurs zu weit weg (ueber 0,5 %), verwirft RIN das Setup:
    far = [[100, 100, 100, 100], [100.6, 101, 100, 100.5], [100.5, 100.5, 100.5, 100.5]]
    r, _, _ = run(far, {AT_13: long()})
    assert r.open_positions == () and r.blocked_by_reason == {"ENTRY_DEVIATES_FROM_MARKET": 1}


def test_exit_signal_closes_at_the_next_open_with_taker_fee_and_spread():
    ohlc = [[100, 100, 100, 100], [100, 101, 99.5, 100.5], [102, 103, 101, 102], [102, 102, 102, 102]]
    data = MarketDataset.build([series(BTC, ohlc, spread_bps=D(10))])  # Bid/Ask 5 bp um den Kurs
    r, _, _ = run(data, {AT_13: long(), T0 + 2 * HOUR: leave()}, config(costs=CostModel(stop_slippage_bps=D(0))))
    (t,) = r.trades
    entry = D(100) * D("1.0005") * D("1.0002")  # Ask + 2 bp Slippage
    exit_ = D(102) * D("0.9995") * D("0.9998")  # Bid - 2 bp Slippage
    assert t.entry_price == entry and t.exit_price == exit_ and t.exit_reason is ExitReason.SIGNAL
    assert t.closed_at == T0 + 2 * HOUR and (r.exits_requested, r.exits_executed) == (1, 1)
    assert t.gross_pnl == ((exit_ - entry) * t.quantity).quantize(D("0.00000001"), rounding="ROUND_FLOOR")
    assert t.fees == NO_SLIP.fee(t.quantity * entry) + NO_SLIP.fee(t.quantity * exit_)  # beide taker


def test_reversal_closes_the_old_position_before_opening_the_new_one():
    """Ausstieg und Gegen-Einstieg zum selben Zeitpunkt: erst schliessen, dann eroeffnen - beides zum selben Kurs."""
    fund = [FundingEvent(ETHF, T0 + 8 * HOUR, D("0.0001"), D(100))]
    data = MarketDataset.build([series(ETHF, flat(8), fund, D("0.0001"))])
    turn = StrategyDecision(entries=long(ETHF).entries, exits=leave(ETHF).exits)
    r, _, log = run(data, {AT_13: short(), T0 + 3 * HOUR: turn})
    (closed,) = r.trades
    (opened,) = r.open_positions
    assert closed.side is Side.SHORT and closed.exit_reason is ExitReason.SIGNAL and closed.closed_at == T0 + 3 * HOUR
    assert opened.side is Side.LONG and opened.opened_at == T0 + 3 * HOUR and r.blocked_by_reason == {}
    order = [e.event_type for e in log.events() if e.event_type in ("POSITION_CLOSED", "ORDER_FILLED")]
    assert order == ["ORDER_FILLED", "POSITION_CLOSED", "ORDER_FILLED"]


def test_equity_point_is_taken_after_candle_close_and_funding_of_that_moment():
    """Um 16:00 schliesst die Kerze UND Funding wird abgerechnet: der Kapitalpunkt von 16:00 enthaelt beides."""
    fund = [FundingEvent(ETHF, T0 + 4 * HOUR, D("0.001"), D(100))]
    data = MarketDataset.build([series(ETHF, flat(6), fund, D("0.001"))])
    r, _, _ = run(data, {AT_13: long(ETHF)})
    (p,) = r.open_positions
    paid = p.quantity * 100 * D("0.001")
    assert p.funding_paid == paid > 0
    by_time = {pt.time: pt for pt in r.equity_curve}
    assert by_time[T0 + 3 * HOUR].equity == D(10000) - p.entry_fee
    assert by_time[T0 + 4 * HOUR].equity == D(10000) - p.entry_fee - paid
    assert r.report.final_equity == D(10000) - p.entry_fee - paid


def test_orders_at_the_very_last_bar_cannot_be_executed():
    r, _, log = run(flat(3), {T0 + 3 * HOUR: long()})
    assert r.open_positions == () and r.blocked_by_reason == {"NO_MARKET_DATA": 1}
    assert log.of_type(EventType.SETUP_BLOCKED)[0].payload["stage"] == "PIPELINE"


def test_open_position_at_the_end_is_reported_as_unrealized_not_closed():
    ohlc = [[100, 100, 100, 100], [100, 102, 100, 102], [102, 103, 101, 103]]
    r, _, _ = run(ohlc, {AT_13: long()})
    assert r.trades == () and len(r.open_positions) == 1
    assert r.report.realized_pnl == 0
    assert r.report.unrealized_pnl == QTY * 3 - ENTRY_FEE  # Kursgewinn 3 je Stueck minus bezahlte Gebuehr
    assert r.report.final_equity == D(10000) + QTY * 3 - ENTRY_FEE


def test_short_futures_with_funding_by_hand():
    # Short 7,8... Stueck ETH-Futures; um 16:00 Funding +0,01 % (Short ERHAELT), danach Ziel erreicht.
    ohlc = flat(4) + [[100, 100, 89, 90], [90, 90, 90, 90]]
    fund = [FundingEvent(ETHF, T0 + 4 * HOUR, D("0.0001"), D(100))]
    data = MarketDataset.build([series(ETHF, ohlc, fund, D("0.0001"))])
    r, _, log = run(data, {AT_13: short()})
    (t,) = r.trades
    unit = D(5) + D("0.06") + D("0.063") + D("0.01")  # Kurs + 2 Gebuehren + Funding-Puffer
    qty = (D(40) / unit).quantize(D("0.0001"), rounding="ROUND_DOWN")
    assert t.side is Side.SHORT and t.quantity == qty == D("7.7927")
    assert t.funding == D("-0.07792700")  # 7,7927 x 100 x 0,01 % erhalten (aufgerundet zu Ungunsten: -0,077927)
    assert t.exit_reason is ExitReason.TAKE_PROFIT and t.gross_pnl == qty * 10
    assert r.report.total_funding == t.funding and len(log.of_type(EventType.FUNDING_APPLIED)) == 1
    assert r.report.final_equity == D(10000) + t.net_pnl


def test_futures_without_a_known_funding_rate_are_not_traded():
    fund = [FundingEvent(ETHF, T0 + 4 * HOUR, D("0.0001"), D(100))]
    data = MarketDataset.build([series(ETHF, flat(8), fund)])  # kein Anfangssatz: bis 16:00 unbekannt
    r, _, _ = run(data, {AT_13: short(), T0 + 5 * HOUR: short()})
    assert r.blocked_by_reason == {"FUNDING_MISSING": 1} and r.setups_executed == 1
    assert r.open_positions[0].opened_at == T0 + 5 * HOUR


# ------------------------------------------------------------------ kein Blick in die Zukunft


def test_strategy_only_ever_sees_closed_candles_up_to_now():
    data = synthetic(days=5)
    _, strategy, _ = run(data, Scripted({}))
    assert len(strategy.views) == 5 * 24
    for view in strategy.views:
        assert view.bars, view.now
        for candles in view.bars.values():
            assert all(c.close_time <= view.now for c in candles)
            assert candles[-1].close_time == view.now  # und die juengste bekannte Kerze ist die gerade beendete


def test_results_up_to_a_point_do_not_depend_on_later_data():
    """Derselbe Lauf auf dem vollen und auf einem bei T abgeschnittenen Datensatz: bis T identisch."""
    full = synthetic(days=20)
    cut_at = T0 + timedelta(days=11)
    a, _, _ = run(full, SmaCrossStrategy(6, 18))
    b, _, _ = run(full.truncated(cut_at), SmaCrossStrategy(6, 18))
    assert len(b.trades) >= 5  # es wurde wirklich gehandelt
    assert [p for p in a.equity_curve if p.time <= cut_at] == list(b.equity_curve)
    assert [t for t in a.trades if t.closed_at < cut_at] == [t for t in b.trades if t.closed_at < cut_at]


def test_changing_future_prices_does_not_change_the_past():
    base = synthetic(days=12)
    cut_at = T0 + timedelta(days=6)
    altered = []
    for s in base.series:
        candles = tuple(c if c.close_time <= cut_at else
                        type(c)(c.instrument, c.open_time, c.interval_s, c.open * 3, c.high * 3, c.low * 3,
                                c.close * 3, c.volume) for c in s.candles)
        funding = tuple(f if f.time <= cut_at else type(f)(f.instrument, f.time, f.rate, f.mark_price * 3)
                        for f in s.funding)
        altered.append(type(s)(s.instrument, s.interval_s, candles, funding, s.initial_funding_rate, s.spread_bps))
    other = MarketDataset.build(altered)
    assert other.fingerprint != base.fingerprint
    a, _, _ = run(base, SmaCrossStrategy(6, 18))
    b, _, _ = run(other, SmaCrossStrategy(6, 18))
    assert [p for p in a.equity_curve if p.time <= cut_at] == [p for p in b.equity_curve if p.time <= cut_at]
    assert [t for t in a.trades if t.closed_at < cut_at] == [t for t in b.trades if t.closed_at < cut_at]
    assert a.result_hash != b.result_hash


def test_entry_price_is_never_the_price_the_strategy_decided_on():
    ohlc = [[100, 100, 100, 100], [100.3, 101, 100, 100.2], [100.2, 100.2, 100.2, 100.2]]
    r, strategy, log = run(ohlc, {AT_13: long()})
    proposed = log.of_type(EventType.SETUP_PROPOSED)[0].payload["setup"]
    assert proposed["entry"] == "100" and proposed["created_at"] == AT_13.isoformat()  # bekannt: Schlusskurs
    assert r.open_positions[0].entry_price == D("100.3")  # gehandelt: der naechste Kurs
    assert strategy.views[0].last_close(BTC) == D(100)


# ------------------------------------------------------------------ Reproduzierbarkeit


def test_same_inputs_give_bit_identical_results():
    data = synthetic(days=15)
    a, _, log_a = run(data, SmaCrossStrategy(6, 18))
    b, _, log_b = run(synthetic(days=15), SmaCrossStrategy(6, 18))
    assert a.result_hash == b.result_hash and a.log_head == b.log_head and a.to_json() == b.to_json()
    assert a.trades == b.trades and a.equity_curve == b.equity_curve and len(a.trades) > 5
    assert [e.hash for e in log_a.events()] == [e.hash for e in log_b.events()]


def test_any_change_in_data_costs_or_strategy_changes_the_fingerprint():
    data = synthetic(days=15)
    base, _, _ = run(data, SmaCrossStrategy(6, 18))
    other_data, _, _ = run(synthetic(days=15, seed=6), SmaCrossStrategy(6, 18))
    other_costs, _, _ = run(data, SmaCrossStrategy(6, 18), config(costs=CostModel(taker_fee=D("0.001"))))
    other_strategy, _, _ = run(data, SmaCrossStrategy(5, 18))
    hashes = {base.result_hash, other_data.result_hash, other_costs.result_hash, other_strategy.result_hash}
    assert len(hashes) == 4
    labelled, _, _ = run(data, SmaCrossStrategy(6, 18), config(label="nur ein Name"))
    assert labelled.result_hash == base.result_hash and labelled.config_fingerprint == base.config_fingerprint


def test_result_is_identical_in_a_separate_process_with_another_hash_seed():
    """Schutz vor versteckter Abhaengigkeit von der Python-internen Reihenfolge (Mengen, Woerterbuecher)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    code = ("from bt_helpers import synthetic, config; from trading.backtest import BacktestEngine;"
            "from trading.backtest.reference import SmaCrossStrategy;"
            "print(BacktestEngine(synthetic(days=15), SmaCrossStrategy(6, 18), config()).run().result_hash)")
    hashes = set()
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=os.pathsep.join([str(root / "src"), str(root / "tests")]))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
        assert out.returncode == 0, out.stderr
        hashes.add(out.stdout.strip())
    here, _, _ = run(synthetic(days=15), SmaCrossStrategy(6, 18))
    assert hashes == {here.result_hash}


# ------------------------------------------------------------------ Kosten


def test_costs_are_configurable_and_always_reduce_the_result():
    data = synthetic(days=20)
    dearer = CostModel(taker_fee=D("0.001"), maker_fee=D("0.0005"), slippage_bps=D(5), stop_slippage_bps=D(10))
    free, _, _ = run(data, SmaCrossStrategy(6, 18), config(costs=FREE))
    normal, _, _ = run(data, SmaCrossStrategy(6, 18), config(costs=CostModel()))
    costly, _, _ = run(data, SmaCrossStrategy(6, 18), config(costs=dearer))
    assert free.setups_executed == normal.setups_executed == costly.setups_executed > 20
    assert free.report.total_fees == 0 and normal.report.total_fees > 0
    assert costly.report.total_fees > normal.report.total_fees
    assert free.report.final_equity > normal.report.final_equity > costly.report.final_equity


def test_when_costs_eat_the_stop_distance_nothing_is_traded():
    """Bei 2 % Stop und rund 0,7 % Kosten je Hin- und Rueckweg lehnt RIN ab: Stop < 3x Kosten."""
    extreme = CostModel(taker_fee=D("0.002"), maker_fee=D("0.001"), slippage_bps=D(10), stop_slippage_bps=D(20))
    r, _, _ = run(synthetic(days=20), SmaCrossStrategy(6, 18), config(costs=extreme))
    assert r.setups_proposed > 20 and r.setups_executed == 0 and r.trades == ()
    assert r.blocked_by_reason["STOP_TOO_TIGHT"] == r.setups_proposed - r.blocked_by_reason.get("NO_MARKET_DATA", 0)
    assert r.report.final_equity == D(10000) and r.report.total_fees == 0


def test_without_any_costs_the_result_is_the_pure_price_difference():
    ohlc = [[100, 100, 100, 100], [100, 101, 99.5, 100.5], [100.5, 111, 100.5, 109], [109, 109, 109, 109]]
    r, _, _ = run(ohlc, {AT_13: long()}, config(costs=FREE))
    (t,) = r.trades
    assert t.quantity == D(8) and t.fees == 0  # 40 / 5 = 8 Stueck
    assert t.net_pnl == t.gross_pnl == D(80) == r.report.net_result


# ------------------------------------------------------------------ Limits und Protokoll


def test_every_fill_respects_all_limits_at_once_and_everything_is_logged():
    data = synthetic(days=30, vol=0.01)
    r, _, log = run(data, SmaCrossStrategy(5, 12))
    fills = log.of_type(EventType.ORDER_FILLED)
    assert len(fills) == r.setups_executed > 20
    for e in fills:
        risk = e.payload["risk"]
        assert D(risk["risk_pct"]) <= D("0.004") + D("0.0000001")  # SORAs Ziel je Trade
        assert D(risk["total_risk_pct_after"]) <= DEFAULT_LIMITS.max_portfolio_risk
        assert D(risk["gross_exposure_pct_after"]) <= DEFAULT_LIMITS.max_gross_exposure
        assert D(risk["stress_loss_pct_after"]) <= DEFAULT_LIMITS.max_stress_loss
        assert e.payload["mode"] == "paper"
    # Jedes vorgeschlagene Setup ist entweder ausgefuehrt oder mit Grund blockiert - nichts verschwindet.
    proposed = {e.payload["setup"]["setup_id"] for e in log.of_type(EventType.SETUP_PROPOSED)}
    done = {e.payload["position"]["setup_id"] for e in fills}
    blocked = {e.payload["setup"]["setup_id"] for e in log.of_type(EventType.SETUP_BLOCKED)}
    rejected = {e.payload["setup"]["setup_id"] for e in log.of_type(EventType.ORDER_REJECTED)}
    assert proposed == done | blocked | rejected and len(proposed) == r.setups_proposed
    assert not (done & (blocked | rejected))
    types = [e.event_type for e in log.events()]
    assert types[0] == "BACKTEST_STARTED" and types[-1] == "BACKTEST_FINISHED"
    assert log.verify().ok and r.events == len(types) and r.log_head == log.events()[-1].hash
    assert len(log.of_type(EventType.POSITION_CLOSED)) == len(r.trades)
    assert r.placeholders == ()  # KIBO und YUZU arbeiten standardmaessig regelbasiert (AP-17)


def test_report_reconciles_and_trades_cover_long_short_spot_and_futures():
    r, _, _ = run(synthetic(days=30, vol=0.01), SmaCrossStrategy(5, 12))
    rep = r.report
    assert rep.final_equity == rep.initial_equity + rep.realized_pnl + rep.unrealized_pnl
    assert rep.realized_pnl == sum(t.net_pnl for t in r.trades)
    assert rep.equity.points == 30 * 24 and rep.equity.regular_steps
    assert rep.equity.max_drawdown_pct > 0 and rep.equity.sharpe is not None
    kinds = {(t.instrument.market_type, t.side) for t in r.trades}
    assert kinds == {(MarketType.SPOT, Side.LONG), (MarketType.FUTURES, Side.LONG), (MarketType.FUTURES, Side.SHORT)}
    assert {t.exit_reason for t in r.trades} >= {ExitReason.STOP_LOSS, ExitReason.SIGNAL}
    assert rep.total_funding != 0 and rep.equity.max_gross_exposure_pct <= 100


def test_position_size_shrinks_with_the_equity():
    ohlc = [[100, 100, 100, 100], [100, 100, 100, 100], [100, 100, 100, 100]]
    small, _, _ = run(ohlc, {AT_13: long()}, config(initial_equity=D(2500)))
    assert small.open_positions[0].quantity == (D(10) / D("5.117")).quantize(D("0.0001"), rounding="ROUND_DOWN")


def test_invalid_proposals_are_blocked_not_executed_and_do_not_crash_the_run():
    bad_stop = StrategyDecision(entries=(EntryProposal(BTC, Side.LONG, D(105), D(110)),))  # Stop ueber dem Kurs
    spot_short = StrategyDecision(entries=(EntryProposal(BTC, Side.SHORT, D(105), D(90)),))
    unknown = StrategyDecision(entries=(EntryProposal(inst("XRP-USD"), Side.LONG, D(95), D(110)),))
    r, _, _ = run(flat(6), {AT_13: bad_stop, T0 + 2 * HOUR: spot_short, T0 + 3 * HOUR: unknown})
    assert r.setups_executed == 0 and r.open_positions == () and r.setups_proposed == 3
    assert r.blocked_by_reason["STOP_WRONG_SIDE"] >= 1 and r.blocked_by_reason["SPOT_SHORT_NOT_ALLOWED"] == 1
    assert r.blocked_by_reason["NO_MARKET_DATA"] == 1  # Instrument nicht im Datensatz


def test_a_crashing_strategy_fails_the_run_instead_of_producing_a_result():
    class Broken(Strategy):
        name = "broken"

        def on_bar(self, view):
            raise RuntimeError("Fehler in der Strategie")

    with pytest.raises(BacktestError, match="abgestuerzt"):
        run(flat(4), Broken())


def test_config_is_validated():
    with pytest.raises(ValueError):
        BacktestConfig(initial_equity=D(0))
    with pytest.raises(ValueError):
        BacktestConfig(initial_equity=10000.0)
    with pytest.raises(ValueError):
        BacktestConfig(history_bars=0)


def test_history_window_limits_what_the_strategy_sees():
    _, strategy, _ = run(synthetic(days=3), Scripted({}), config(history_bars=10))
    assert max(len(c) for v in strategy.views for c in v.bars.values()) == 10


def test_instruments_with_different_intervals_are_processed_in_time_order():
    hourly = series(BTC, flat(8))
    four_hourly = series(ETHF, flat(2), [FundingEvent(ETHF, T0 + 4 * HOUR, D("0.0001"), D(100))], D("0.0001"),
                         interval_s=4 * 3600)
    data = MarketDataset.build([hourly, four_hourly])
    plan = {T0 + 2 * HOUR: short(), T0 + 4 * HOUR: short()}
    r, strategy, _ = run(data, plan)
    assert len(strategy.views) == 8  # nur Zeitpunkte, an denen eine Kerze schliesst
    # ETH-Kerzen beginnen nur um 12:00 und 16:00: um 14:00 gibt es keinen Ausfuehrungskurs
    assert r.blocked_by_reason == {"NO_MARKET_DATA": 1} and r.open_positions[0].opened_at == T0 + 4 * HOUR
    assert ETHF not in strategy.views[0].bars and ETHF in strategy.views[3].bars  # ab 16:00 bekannt


# ------------------------------------------------------------------ Strategien vergleichen


def test_strategies_are_compared_on_the_same_data_and_assumptions():
    data = synthetic(days=20)
    a, _, _ = run(data, SmaCrossStrategy(6, 18))
    b, _, _ = run(data, SmaCrossStrategy(4, 30), config(label="langsam"))
    table = comparison_table([a, b])
    assert [row["strategy"] for row in table] == sorted(
        ["sma6-18", "sma4-30"], key=lambda n: -(a if n == "sma6-18" else b).report.equity.total_return_pct)
    assert table == comparison_table([b, a])  # Reihenfolge der Eingabe spielt keine Rolle
    best = table[0]
    source = a if best["strategy"] == "sma6-18" else b
    assert best["net_result"] == source.report.net_result == source.report.final_equity - D(10000)
    assert best["trades"] == len(source.trades) and best["total_fees"] == source.report.total_fees
    assert best["result_hash"] == source.result_hash
    assert comparison_table([]) == []


def test_results_from_different_data_or_assumptions_are_not_comparable():
    a, _, _ = run(synthetic(days=20), SmaCrossStrategy(6, 18))
    other_data, _, _ = run(synthetic(days=20, seed=9), SmaCrossStrategy(6, 18))
    cheaper, _, _ = run(synthetic(days=20), SmaCrossStrategy(6, 18), config(costs=FREE))
    richer, _, _ = run(synthetic(days=20), SmaCrossStrategy(6, 18), config(initial_equity=D(50000)))
    with pytest.raises(ValueError, match="Datensaetzen"):
        comparison_table([a, other_data])
    with pytest.raises(ValueError, match="Kosten"):
        comparison_table([a, cheaper])
    with pytest.raises(ValueError, match="Startkapital"):
        comparison_table([a, richer])


def test_demo_program_runs_in_paper_mode_and_prints_a_comparison(capsys, monkeypatch):
    import runpy

    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setattr("sys.argv", ["trading.backtest"])
    runpy.run_module("trading.backtest", run_name="__main__")
    out = capsys.readouterr().out
    from trading.strategies import default_strategies

    assert "PAPER" in out.upper() and "buy-and-hold" in out
    assert all(s.name in out for s in default_strategies())  # jede Strategie der Bibliothek im Vergleich


def test_demo_program_refuses_any_mode_other_than_paper(monkeypatch):
    import runpy

    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setattr("sys.argv", ["trading.backtest"])
    with pytest.raises(ConfigError):
        runpy.run_module("trading.backtest", run_name="__main__")


# ------------------------------------------------------------------ Fingerabdruck und Protokoll sind belastbar


def test_result_hash_covers_the_results_themselves_not_only_the_inputs():
    """Gleiche Daten, gleiche Einstellungen, gleiche ANZAHL Trades - aber andere Betraege: anderer Fingerabdruck.

    Dazu rechnet eine (absichtlich falsch verdrahtete) Ausfuehrung ohne Gebuehren. So faellt spaeter auch auf,
    wenn eine Programmaenderung bei gleichen Eingaben andere Ergebnisse liefert.
    """
    from trading.execution.paper_executor import PaperExecutor
    from trading.safety.data_validator import SnapshotValidator
    from trading.safety.risk_manager import RiskManager

    def fee_free_executor(clock, risk, log):
        return PaperExecutor(D(10000), RiskManager(DEFAULT_LIMITS, FREE), SnapshotValidator(DEFAULT_LIMITS, clock), log, clock)

    ohlc = [[100, 100, 100, 100], [100, 101, 99.5, 100.5], [100.5, 111, 100.5, 109], [109, 109, 108, 108.5]]
    normal, _, _ = run(ohlc, {AT_13: long()})
    other, _, _ = run(ohlc, {AT_13: long()}, executor_factory=fee_free_executor)
    assert (len(other.trades), len(other.equity_curve), other.setups_executed) == (1, len(normal.equity_curve), 1)
    assert other.dataset_fingerprint == normal.dataset_fingerprint
    assert other.config_fingerprint == normal.config_fingerprint
    assert other.trades[0].fees == 0 and normal.trades[0].fees > 0
    assert other.result_hash != normal.result_hash


def test_a_damaged_event_log_invalidates_the_whole_run():
    import dataclasses

    class TamperedLog(InMemoryEventLog):
        """Verfaelscht waehrend des Laufs nachtraeglich einen frueheren Eintrag."""

        def _persist(self, event):
            if event.seq == 4:
                self._events[1] = dataclasses.replace(self._events[1], payload_json='{"gefaelscht":true}')

    ohlc = [[100, 100, 100, 100], [100, 101, 99.5, 100.5], [100.5, 111, 100.5, 109], [109, 109, 108, 108.5]]
    engine = BacktestEngine(MarketDataset.build([series(BTC, ohlc)]), Scripted({AT_13: long()}), config(),
                            log_factory=lambda clock: TamperedLog(clock))
    with pytest.raises(BacktestError, match="beschaedigt"):
        engine.run()


def test_the_order_in_which_series_are_supplied_does_not_matter():
    a = series(BTC, flat(30))
    b = series(ETHF, flat(30), [FundingEvent(ETHF, T0 + 8 * HOUR, D("0.0001"), D(100))], D("0.0001"))
    one, two = MarketDataset.build([a, b]), MarketDataset.build([b, a])
    assert one.fingerprint == two.fingerprint and one.instruments == two.instruments
    plan = {AT_13: StrategyDecision(entries=long().entries + short().entries)}
    r1, _, _ = run(one, plan)
    r2, _, _ = run(two, plan)
    assert r1.result_hash == r2.result_hash and r1.log_head == r2.log_head and r1.setups_executed == 2


def test_neutral_placeholders_only_on_request_and_then_reported():
    from trading.agents import NeutralKibo, NeutralYuzu

    ds = synthetic(days=3)
    r = BacktestEngine(ds, Scripted({}), config(), kibo=NeutralKibo(), yuzu=NeutralYuzu()).run()
    assert r.placeholders == ("KIBO", "YUZU") and r.summary()["placeholders"] == ("KIBO", "YUZU")
    assert BacktestEngine(ds, Scripted({}), config(), kibo=NeutralKibo()).run().placeholders == ("KIBO",)
