"""Kapitalkurve, Kennzahlen und Gesamtbericht (M13) - Erwartungswerte von Hand bzw. unabhaengig nachgerechnet."""

import math
import statistics
from datetime import timedelta
from decimal import Decimal

import pytest
from helpers import D, FUT, Env, T0, inst, make_candle

from trading.agents import AkiAgent
from trading.audit.performance import (
    AccountingError,
    EquityPoint,
    build_report,
    compute_equity_stats,
)
from trading.audit.stats import compute_performance
from trading.domain.models import ClosedTrade, ExitReason, MarketType, Side

HOUR = timedelta(hours=1)


def curve(values, start=T0, step=HOUR, open_positions=None, exposure=None):
    return [
        EquityPoint(start + i * step, D(v), D(0), D(v),
                    D(exposure[i]) if exposure else D(0), D(0),
                    open_positions[i] if open_positions else 0)
        for i, v in enumerate(values)
    ]


def trade(net, risk=D(50), fees=D(1), funding=D(0), reason=ExitReason.TAKE_PROFIT, hold_s=3600, pid="P"):
    return ClosedTrade(pid, "S", "test", inst("BTC-USD", MarketType.SPOT), Side.LONG, D(1), D(100), D(101),
                       T0, T0 + timedelta(seconds=hold_s), reason, net + fees + funding, fees, funding, net, risk)


# ------------------------------------------------------------------ Kapitalkurve


def test_drawdown_return_and_underwater_time_by_hand():
    #              t0     t1     t2     t3     t4     t5
    values = [10000, 10100, 10050, 10080, 10000, 10200]
    s = compute_equity_stats(curve(values), D(10000))
    assert s.points == 6 and s.start_equity == D(10000) and s.end_equity == D(10200)
    assert s.total_return_pct == D(2) and s.peak_equity == D(10200)
    assert s.max_drawdown_abs == D(100)  # von 10 100 (t1) auf 10 000 (t4)
    assert s.max_drawdown_pct == D(100) / D(10100) * 100
    assert s.max_drawdown_peak_time == T0 + HOUR and s.max_drawdown_trough_time == T0 + 4 * HOUR
    assert s.longest_underwater_seconds == D(3 * 3600)  # t1 -> t4 unter Wasser, bei t5 neuer Hoechststand
    assert s.step_seconds == D(3600) and s.regular_steps


def test_drawdown_counts_the_start_capital_as_first_peak():
    s = compute_equity_stats(curve([9900, 9950, 9800]), D(10000))
    assert s.max_drawdown_abs == D(200) and s.max_drawdown_pct == D(2)
    assert s.max_drawdown_peak_time is None  # Hoechststand war das Startkapital (vor dem ersten Punkt)
    assert s.longest_underwater_seconds == D(2 * 3600) and s.total_return_pct == D(-2)


def test_unrecovered_drawdown_keeps_counting_until_the_end():
    s = compute_equity_stats(curve([10000, 10500, 10400, 10300, 10450]), D(10000))
    assert s.longest_underwater_seconds == D(3 * 3600)
    assert s.max_drawdown_abs == D(200) and s.max_drawdown_trough_time == T0 + 3 * HOUR


def test_sharpe_and_sortino_match_an_independent_calculation():
    values = [10000, 10100, 10050, 10080, 10000, 10200, 10150, 10300]
    s = compute_equity_stats(curve(values), D(10000))
    returns = [values[i + 1] / values[i] - 1 for i in range(len(values) - 1)]
    annual = math.sqrt(365 * 24)  # Stundenschritte
    sharpe = statistics.mean(returns) / statistics.stdev(returns) * annual
    downside = math.sqrt(sum(min(r, 0) ** 2 for r in returns) / len(returns))
    sortino = statistics.mean(returns) / downside * annual
    assert float(s.sharpe) == pytest.approx(sharpe, rel=1e-12)
    assert float(s.sortino) == pytest.approx(sortino, rel=1e-12)
    assert float(s.volatility_per_step) == pytest.approx(statistics.stdev(returns), rel=1e-12)


def test_risk_adjusted_figures_are_undefined_rather_than_invented():
    flat = compute_equity_stats(curve([10000, 10000, 10000]), D(10000))
    assert flat.sharpe is None and flat.sortino is None and flat.volatility_per_step == 0
    only_up = compute_equity_stats(curve([10000, 10100, 10300]), D(10000))
    assert only_up.sharpe is not None and only_up.sortino is None  # keine Abwaertsbewegung
    assert compute_equity_stats(curve([10000, 10100]), D(10000)).sharpe is None  # nur eine Rendite
    empty = compute_equity_stats([], D(10000))
    assert (empty.points, empty.end_equity, empty.max_drawdown_pct, empty.sharpe) == (0, D(10000), D(0), None)
    assert empty.time_in_market_pct == 0


def test_irregular_steps_are_flagged():
    pts = curve([10000, 10100, 10050])
    pts[2] = EquityPoint(T0 + 5 * HOUR, D(10050), D(0), D(10050), D(0), D(0), 0)
    s = compute_equity_stats(pts, D(10000))
    assert not s.regular_steps


def test_points_must_be_strictly_increasing_in_time():
    pts = curve([10000, 10100])
    with pytest.raises(ValueError):
        compute_equity_stats([pts[1], pts[0]], D(10000))
    with pytest.raises(ValueError):
        compute_equity_stats([pts[0], pts[0]], D(10000))
    with pytest.raises(ValueError):
        compute_equity_stats(pts, D(0))


def test_exposure_and_time_in_market():
    pts = curve([10000, 10000, 10000, 10000], open_positions=[0, 1, 2, 0], exposure=[0, 4000, 9000, 0])
    s = compute_equity_stats(pts, D(10000))
    assert s.time_in_market_pct == D(50) and s.max_gross_exposure_pct == D(90)
    assert s.avg_gross_exposure_pct == D("32.5")


# ------------------------------------------------------------------ Trade-Kennzahlen


def test_trade_statistics_by_hand():
    trades = [trade(D(100)), trade(D(-50)), trade(D(0)), trade(D(30)), trade(D(-80)), trade(D(-10))]
    s = compute_performance(trades, D(10000))
    assert (s.trade_count, s.wins, s.losses, s.breakeven) == (6, 2, 3, 1)
    assert s.win_rate == D(2) / D(6)  # der Nullsummen-Trade ist KEIN Gewinner
    assert s.gross_profit == D(130) and s.gross_loss == D(140) and s.net_pnl == D(-10)
    assert s.profit_factor == D(130) / D(140)
    assert s.avg_win == D(65) and s.avg_loss == D(140) / 3
    assert s.payoff_ratio == D(65) / (D(140) / 3)
    assert s.largest_win == D(100) and s.largest_loss == D(80)
    assert s.max_consecutive_losses == 2  # die letzten beiden
    assert s.expectancy == D(-10) / 6 and s.total_fees == D(6)
    assert s.exits_by_reason == {"take_profit": 6} and s.avg_holding_seconds == D(3600)


def test_trade_statistics_edge_cases():
    none = compute_performance([], D(1000))
    assert (none.trade_count, none.win_rate, none.profit_factor, none.payoff_ratio) == (0, 0, None, None)
    assert none.avg_win == none.avg_loss == none.largest_loss == 0 and none.exits_by_reason == {}
    wins = compute_performance([trade(D(10)), trade(D(5))], D(1000))
    assert wins.profit_factor is None and wins.payoff_ratio is None and wins.max_consecutive_losses == 0
    mixed = compute_performance([trade(D(-1), reason=ExitReason.STOP_LOSS), trade(D(-1), reason=ExitReason.SIGNAL),
                                 trade(D(-1), reason=ExitReason.STOP_LOSS), trade(D(5))], D(1000))
    assert mixed.max_consecutive_losses == 3
    # Ein Nullsummen-Trade ist kein Verlust: er unterbricht die Verlustserie.
    broken = compute_performance([trade(D(-1)), trade(D(0)), trade(D(-1))], D(1000))
    assert broken.max_consecutive_losses == 1 and (broken.losses, broken.breakeven) == (2, 1)
    assert mixed.exits_by_reason == {"signal": 1, "stop_loss": 2, "take_profit": 1}


# ------------------------------------------------------------------ Gesamtbericht aus einem echten Ablauf


def run_scenario():
    """Ein Gewinn-Trade wird abgeschlossen, danach bleibt eine Futures-Position mit Funding offen."""
    env = Env()
    aki = AkiAgent(env.log)

    def mark():
        aki.record_equity(env.clock.now(), env.executor.portfolio_state())

    mark()
    env.order("a")  # Spot Long 40 zu 100
    aki.register_closed_trades(env.feed(make_candle(T0, D(100), D(101), D(99.5), D(101))))
    mark()  # 12:01 Buchgewinn 40
    aki.register_closed_trades(env.feed(make_candle(env.clock.now(), D(101), D(103), D(101), D(102))))
    mark()  # 12:02 Ziel erreicht: +76,784 realisiert
    env.order("b", qty=D(20), lev=D(2), market_type=MarketType.FUTURES, snap_kw=dict(FUT))
    env.clock.set(T0 + timedelta(hours=4))
    env.fund()  # 20 x 100 x 0,01 % = 0,2
    c = make_candle(env.clock.now() - timedelta(minutes=1), D(100), D(100.5), D(99.5), D("99.5"),
                    market_type=MarketType.FUTURES)
    aki.register_closed_trades(env.feed(c))
    mark()  # 16:00 offene Position 10 im Minus
    return env, aki


def test_report_separates_realized_and_unrealized_after_all_costs():
    env, aki = run_scenario()
    r = aki.report(env.equity0, env.executor.open_positions)
    assert r.realized_pnl == D("76.784")  # 80 - 2,4 - 0,816
    # offen: Kursverlust 20 x 0,5 = 10, bezahlte Einstiegsgebuehr 1,2, Funding 0,2
    assert r.unrealized_pnl == D("-11.4")
    assert r.final_equity == D(10000) + D("76.784") - D("11.4") == env.executor.equity()
    assert r.net_result == D("65.384") and r.open_positions == 1
    assert r.total_fees == D("3.216") + D("1.2") and r.total_funding == D("0.2")
    assert r.trades.trade_count == 1 and r.trades.win_rate == 1
    assert r.equity.points == 4 and r.equity.peak_equity == D("10076.784")
    assert r.equity.max_drawdown_abs == D("11.4")
    assert not r.equity.regular_steps


def test_equity_curve_shows_drawdown_that_closed_trades_alone_hide():
    """Der Trade endet im Gewinn - zwischendurch lag die offene Position aber im Minus."""
    env = Env()
    aki = AkiAgent(env.log)
    aki.record_equity(env.clock.now(), env.executor.portfolio_state())
    env.order("a")
    for o, h, lo, c in ((100, 100, "99.2", "99.25"), ("99.25", 103, "99.25", 102)):
        aki.register_closed_trades(env.feed(make_candle(env.clock.now(), D(o), D(h), D(lo), D(c))))
        aki.record_equity(env.clock.now(), env.executor.portfolio_state())
    r = aki.report(env.equity0, env.executor.open_positions)
    assert r.trades.max_drawdown_pct == 0  # nur abgeschlossene Trades: kein Drawdown sichtbar
    assert r.equity.max_drawdown_abs == D("32.4")  # 2,4 Gebuehr + 30 Buchverlust
    assert r.realized_pnl == D("76.784") and r.unrealized_pnl == 0


def test_report_refuses_figures_that_do_not_add_up():
    env, aki = run_scenario()
    with pytest.raises(AccountingError):  # offene Position "vergessen"
        aki.report(env.equity0, ())
    with pytest.raises(AccountingError):  # falsches Startkapital
        build_report(D(9000), aki.trades, aki.equity_curve, env.executor.open_positions)
    tampered = list(aki.equity_curve)
    tampered[-1] = EquityPoint(tampered[-1].time, tampered[-1].balance, D(0), tampered[-1].equity + 1,
                               D(0), D(0), 1)
    with pytest.raises(AccountingError):
        build_report(env.equity0, aki.trades, tampered, env.executor.open_positions)


def test_equity_points_must_advance_in_time_in_aki():
    env = Env()
    aki = AkiAgent(env.log)
    aki.record_equity(T0, env.executor.portfolio_state())
    with pytest.raises(ValueError):
        aki.record_equity(T0, env.executor.portfolio_state())


def test_empty_report_is_neutral():
    r = build_report(D(5000), [], [], [])
    assert (r.final_equity, r.net_result, r.realized_pnl, r.unrealized_pnl) == (D(5000), 0, 0, 0)
    assert r.trades.trade_count == 0 and r.equity.sharpe is None


def test_report_values_are_decimals_not_floats():
    env, aki = run_scenario()
    r = aki.report(env.equity0, env.executor.open_positions)
    for value in (r.final_equity, r.realized_pnl, r.unrealized_pnl, r.equity.max_drawdown_pct, r.trades.win_rate):
        assert isinstance(value, Decimal)
