"""Paper Executor - ausschliesslich synthetische Daten, exakt nachrechenbare Erwartungswerte."""

from datetime import timedelta
from decimal import Decimal

import pytest
from helpers import D, Env, T0, flat_candle, make_candle, minutes

from trading.audit.events import EventType
from trading.domain.models import ExitReason, MarketType, OrderStatus, Side
from trading.domain.reasons import RejectReason as R
from trading.execution.costs import CostModel, funding_times_between

FUT = {"market_type": MarketType.FUTURES, "funding": D("0.0001")}


def futures_order(env, oid="f1", qty=D(40), lev=D(2), **setup_kw):
    return env.order(oid, qty, lev, snap_kw=FUT, market_type=MarketType.FUTURES, **setup_kw)


# ------------------------------------------------------------------ Einstieg


def test_long_fill_charges_entry_fee_and_reserves_margin():
    env = Env()
    r = env.order()
    assert r.status is OrderStatus.FILLED
    assert r.position.entry_price == D(100)
    assert r.position.entry_fee == D("2.4")  # 40 * 100 * 0,06 %
    assert env.executor.balance == D("9997.6")
    state = env.executor.portfolio_state()
    assert state.open_count == 1
    assert state.free_margin == D("9997.6") - D(4000)  # Spot: voller Kaufpreis gebunden
    # Restrisiko = geplanter Hoechstverlust minus die bereits bezahlte Einstiegsgebuehr
    assert state.open_risk == r.position.risk_at_open - r.position.entry_fee


def test_entry_slippage_is_applied_against_the_trader():
    env = Env(costs=CostModel())
    long_fill = env.order("a", snap_kw={"half_spread": D("0.01")}).position.entry_price
    assert long_fill == D("100.01") * (1 + D(2) / 10000)  # Ask plus Slippage
    short = env.order(
        "b",
        lev=D(1),
        snap_kw={"symbol": "ETH-USD", "market_type": MarketType.FUTURES,
                 "funding": D(0), "half_spread": D("0.01")},
        symbol="ETH-USD", market_type=MarketType.FUTURES,
        side=Side.SHORT, stop=D(101), tp=D(98),
    )
    assert short.position.entry_price == D("99.99") * (1 - D(2) / 10000)  # Bid minus Slippage


def test_position_is_not_filled_on_rejection_and_balance_is_untouched():
    env = Env()
    r = env.order(qty=D(100))  # viel zu gross
    assert r.status is OrderStatus.REJECTED and R.TRADE_RISK_TOO_HIGH in r.reasons
    assert env.executor.balance == D(10000)
    assert env.executor.open_positions == ()


# ------------------------------------------------------------------ Ausstiege


def test_long_take_profit_exact_numbers():
    env = Env()
    env.order()
    closed = env.executor.on_candle(make_candle(T0, D(100), D("102.5"), D("99.5"), D(102)))
    assert len(closed) == 1
    t = closed[0]
    assert t.exit_reason is ExitReason.TAKE_PROFIT and t.exit_price == D(102)
    assert t.gross_pnl == D(80)
    assert t.fees == D("2.4") + D("0.816")  # Einstieg taker, Ausstieg maker
    assert t.net_pnl == D("76.784")
    assert env.executor.balance == D("10076.784")
    assert env.executor.open_positions == ()


def test_long_stop_loss_loses_exactly_the_planned_risk():
    env = Env()
    r = env.order()
    t = env.executor.on_candle(make_candle(T0, D(100), D("100.5"), D("98.5"), D(99)))[0]
    assert t.exit_reason is ExitReason.STOP_LOSS and t.exit_price == D(99)
    assert t.net_pnl == D("-44.776")
    assert t.net_pnl == -r.position.risk_at_open  # ohne Luecke: geplanter == tatsaechlicher Verlust


def test_stop_loss_with_slippage_still_equals_planned_risk():
    env = Env(costs=CostModel())
    r = env.order()
    t = env.executor.on_candle(make_candle(T0, D(100), D(101), D(98), D(98)))[0]
    assert t.exit_price == D(99) * (1 - D(5) / 10000)
    assert t.net_pnl == -r.position.risk_at_open


def test_stop_wins_when_stop_and_take_profit_hit_in_same_candle():
    env = Env()
    env.order()
    t = env.executor.on_candle(make_candle(T0, D(100), D(103), D(98), D(102)))[0]
    assert t.exit_reason is ExitReason.STOP_LOSS


def test_gap_through_stop_fills_at_worse_open_price():
    env = Env()
    r = env.order()
    t = env.executor.on_candle(make_candle(T0, D(95), D(96), D(94), D(95)))[0]
    assert t.exit_price == D(95)  # nicht 99!
    assert t.net_pnl < -r.position.risk_at_open  # Luecken-Risiko ist real


def test_short_take_profit_and_stop():
    env = Env()
    kw = dict(
        qty=D(40), lev=D(2), snap_kw=FUT, market_type=MarketType.FUTURES,
        side=Side.SHORT, stop=D(101), tp=D(98),
    )
    env.order("tp", **kw)
    t = env.executor.on_candle(make_candle(T0, D(100), D("100.5"), D("97.9"), D(98)), D("0.0001"))[0]
    assert t.exit_reason is ExitReason.TAKE_PROFIT
    assert t.gross_pnl == D(80)
    assert t.net_pnl == D(80) - D("2.4") - D("0.784")

    env.clock.advance(60)
    env.order("sl", **kw)
    t2 = env.executor.on_candle(
        make_candle(env.clock.now(), D(100), D("101.5"), D("99.5"), D(101)), D("0.0001")
    )[0]
    assert t2.exit_reason is ExitReason.STOP_LOSS and t2.exit_price == D(101)
    assert t2.gross_pnl == D(-40)


def test_candles_before_entry_are_ignored_no_lookahead():
    env = Env()
    env.order()
    old = make_candle(T0 - minutes(5), D(100), D(100), D(90), D(95))  # haette den Stop gerissen
    assert env.executor.on_candle(old) == []
    assert len(env.executor.open_positions) == 1
    later = make_candle(T0, D(100), D(100), D(90), D(95))
    assert len(env.executor.on_candle(later)) == 1


def test_unrealized_pnl_is_part_of_equity_but_not_of_free_margin():
    env = Env()
    env.order()
    env.executor.on_candle(make_candle(T0, D(100), D("101.5"), D("99.5"), D(101)))
    assert env.executor.equity() == D("9997.6") + D(40)
    assert env.executor.portfolio_state().free_margin == D("9997.6") - D(4000)


def test_liquidation_is_capped_at_margin_and_flagged():
    env = Env()
    r = futures_order(env, qty=D(4), lev=D(5), stop=D(90), tp=D(110))
    assert r.status is OrderStatus.FILLED and r.position.liquidation_price == D("80.5")
    t = env.executor.on_candle(make_candle(T0, D(70), D(71), D(69), D(70)), D("0.0001"))[0]
    assert t.exit_reason is ExitReason.LIQUIDATION
    assert t.exit_price == D("80.5")
    assert -t.gross_pnl <= r.position.margin  # nie mehr als die Margin verloren


# ------------------------------------------------------------------ Funding


def test_funding_times():
    start = T0  # 12:00
    assert funding_times_between(start, start + timedelta(hours=4)) == [T0 + timedelta(hours=4)]
    assert len(funding_times_between(start, start + timedelta(hours=12))) == 2  # 16:00 und 00:00
    assert funding_times_between(start, start + timedelta(hours=3)) == []


def test_long_pays_positive_funding():
    env = Env()
    futures_order(env)
    candle = flat_candle(T0, interval_s=4 * 3600)  # endet 16:00 -> eine Funding-Zahlung
    env.executor.on_candle(candle, D("0.0001"))
    pos = env.executor.open_positions[0]
    assert pos.funding_paid == D("0.4")  # 40 * 100 * 0,01 %
    assert env.executor.balance == D(10000) - D("2.4") - D("0.4")
    assert len(env.log.of_type(EventType.FUNDING_APPLIED)) == 1


def test_two_funding_boundaries_charge_twice():
    env = Env()
    futures_order(env)
    env.executor.on_candle(flat_candle(T0, interval_s=12 * 3600), D("0.0001"))  # 16:00 + 00:00
    assert env.executor.open_positions[0].funding_paid == D("0.8")


def test_short_receives_positive_funding_if_it_survives():
    env = Env()
    futures_order(env, side=Side.SHORT, stop=D(101), tp=D(98))
    env.executor.on_candle(flat_candle(T0, interval_s=4 * 3600), D("0.0001"))
    assert env.executor.open_positions[0].funding_paid == D("-0.4")
    assert env.executor.balance == D(10000) - D("2.4") + D("0.4")


def test_funding_receipt_is_ignored_when_position_exits_in_that_candle():
    env = Env()
    futures_order(env, side=Side.SHORT, stop=D(101), tp=D(98))
    candle = make_candle(T0, D(100), D(100), D(97), D(98), interval_s=4 * 3600)
    t = env.executor.on_candle(candle, D("0.0001"))[0]
    assert t.exit_reason is ExitReason.TAKE_PROFIT and t.funding == D(0)


def test_funding_payment_is_charged_even_when_exiting_in_that_candle():
    env = Env()
    futures_order(env)  # Long zahlt bei positivem Funding
    candle = make_candle(T0, D(100), D(103), D(100), D(102), interval_s=4 * 3600)
    t = env.executor.on_candle(candle, D("0.0001"))[0]
    # Funding wird auf den Kerzen-Schlusskurs (102) gerechnet: 40 * 102 * 0,01 %
    assert t.funding == D("0.408") and t.exit_reason is ExitReason.TAKE_PROFIT


def test_spot_has_no_funding():
    env = Env()
    env.order()
    env.executor.on_candle(flat_candle(T0, interval_s=12 * 3600), D("0.01"))
    assert env.executor.open_positions[0].funding_paid == D(0)


# ------------------------------------------------------------------ Doppel-Orders


def test_same_order_id_is_executed_only_once():
    env = Env()
    assert env.order("dup").status is OrderStatus.FILLED
    again = env.order("dup")
    assert again.status is OrderStatus.REJECTED and again.reasons == (R.DUPLICATE_ORDER_ID,)
    assert len(env.executor.open_positions) == 1
    assert len(env.log.of_type(EventType.ORDER_FILLED)) == 1


def test_rejected_order_id_cannot_be_retried_with_same_id():
    env = Env()
    assert env.order("x", snap_kw={"ts": T0 - timedelta(seconds=99)}).status is OrderStatus.REJECTED
    again = env.order("x")  # jetzt mit frischen Daten, aber gleiche ID
    assert again.reasons == (R.DUPLICATE_ORDER_ID,)


def test_second_position_in_same_market_is_blocked():
    env = Env()
    env.order("a")
    r = env.order("b")
    assert R.DUPLICATE_POSITION in r.reasons and len(env.executor.open_positions) == 1


# ------------------------------------------------------------------ Blockaden werden protokolliert


def test_stale_data_blocks_trade_and_is_logged_with_reasons_and_setup():
    env = Env()
    r = env.order("stale1", snap_kw={"ts": T0 - timedelta(seconds=60)})
    assert r.status is OrderStatus.REJECTED and R.STALE_DATA in r.reasons
    assert env.executor.open_positions == ()
    ev = env.log.of_type(EventType.ORDER_REJECTED)[0].payload
    assert ev["reasons"] == ["STALE_DATA"]
    assert ev["setup"]["setup_id"] == "stale1"
    assert ev["stage"] == "EXECUTOR"


@pytest.mark.parametrize(
    "snap_kw, reason",
    [
        ({"mid": D(0)}, R.NON_POSITIVE_PRICE),
        ({"half_spread": D(10)}, R.SPREAD_TOO_WIDE),
        ({"ts": T0 + timedelta(seconds=60)}, R.FUTURE_TIMESTAMP),
    ],
)
def test_bad_data_blocks_trade(snap_kw, reason):
    env = Env()
    r = env.order(snap_kw=snap_kw)
    assert reason in r.reasons and env.executor.open_positions == ()


def test_snapshot_for_other_symbol_is_refused():
    env = Env()
    r = env.order(snap_kw={"symbol": "ETH-USD"})
    assert r.reasons == (R.SYMBOL_MISMATCH,)


def test_risk_manager_is_enforced_inside_the_executor_cumulative_cap():
    env = Env()
    results = []
    for i in range(5):  # Futures mit 2x Hebel, damit die Margin nicht vorher ausgeht
        sym = "S%d" % i
        results.append(
            env.order(
                "o%d" % i, qty=D(40), lev=D(2), symbol=sym, market_type=MarketType.FUTURES,
                snap_kw=dict(FUT, symbol=sym),
            )
        )
    statuses = [r.status for r in results]
    assert statuses == [OrderStatus.FILLED] * 4 + [OrderStatus.REJECTED]
    assert R.PORTFOLIO_RISK_TOO_HIGH in results[4].reasons
    state = env.executor.portfolio_state()
    assert state.open_risk / state.equity <= D("0.02")


def test_spot_positions_are_limited_by_cash_before_the_risk_cap():
    env = Env()
    results = [
        env.order("o%d" % i, snap_kw={"symbol": "S%d" % i}, symbol="S%d" % i) for i in range(3)
    ]
    assert [r.status for r in results[:2]] == [OrderStatus.FILLED] * 2
    assert R.INSUFFICIENT_MARGIN in results[2].reasons  # Spot bindet den vollen Kaufpreis


def test_daily_loss_kill_switch_stops_trading_then_resets_next_day():
    env = Env()
    blocked_at = None
    for i in range(10):
        r = env.order("t%d" % i)
        if r.status is OrderStatus.REJECTED:
            blocked_at = i
            assert R.DAILY_LOSS_LIMIT in r.reasons
            break
        env.executor.on_candle(
            make_candle(env.clock.now(), D(100), D(100), D(98), D(98.5))
        )  # Stop wird ausgeloest
        env.clock.advance(120)
    assert blocked_at == 7  # 7 Verluste a 0,448 % = 3,13 % >= 3 %
    # Am naechsten Tag beginnt ein neuer Tag (Drawdown 3,1 % liegt unter der 10-%-Grenze).
    env.clock.set(T0 + timedelta(days=1))
    assert env.order("next-day").status is OrderStatus.FILLED


# ------------------------------------------------------------------ Buchhaltung


def test_balance_always_equals_start_plus_net_pnl_after_all_trades_closed():
    env = Env()
    env.order("a")  # Take-Profit
    env.executor.on_candle(make_candle(T0, D(100), D(103), D(100), D(102)))
    env.clock.advance(60)
    futures_order(env, "b")  # Stop mit Funding
    env.executor.on_candle(
        make_candle(env.clock.now(), D(100), D(100), D(98), D(98), interval_s=4 * 3600), D("0.0001")
    )
    env.clock.advance(4 * 3600)
    futures_order(env, "c", side=Side.SHORT, stop=D(101), tp=D(98))  # Short-Gewinn
    env.executor.on_candle(
        make_candle(env.clock.now(), D(100), D(100), D(97), D(98), interval_s=60), D("0.0001")
    )
    trades = env.executor.closed_trades
    assert len(trades) == 3 and env.executor.open_positions == ()
    assert env.executor.balance == env.equity0 + sum(t.net_pnl for t in trades)
    assert len(env.log.of_type(EventType.POSITION_CLOSED)) == 3
    assert env.log.verify().ok


def test_r_multiple_of_a_stopped_out_trade_is_minus_one():
    env = Env()
    env.order()
    t = env.executor.on_candle(make_candle(T0, D(100), D(100), D(98), D(98)))[0]
    assert t.r_multiple == D(-1)


def test_executor_requires_positive_equity():
    with pytest.raises(ValueError):
        Env(equity=D(0))


# ------------------------------------------------------------------ Regression: Einstiegsgebuehr nicht doppelt als Risiko zaehlen


def test_cap_still_holds_immediately_after_each_fill_entry_fee_is_not_double_counted():
    env = Env()
    for i in range(4):
        sym = "R%d" % i
        before = env.executor.portfolio_state()
        r = env.order("r%d" % i, qty=D(40), lev=D(2), symbol=sym,
                      market_type=MarketType.FUTURES, snap_kw=dict(FUT, symbol=sym))
        assert r.status is OrderStatus.FILLED
        after = env.executor.portfolio_state()
        # Unmittelbar nach dem Fill darf das Verhaeltnis nie ueber dem geprueften Wert liegen.
        assert after.open_risk / after.equity <= (before.open_risk + r.risk.worst_case_loss) / before.equity
        assert after.open_risk / after.equity <= D("0.02")
