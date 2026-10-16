"""Der Risikomanager ist rein regelbasiert und unabhaengig. Alle Grenzen gelten gleichzeitig."""

from decimal import Decimal

import pytest
from helpers import D, NO_SLIP, inst, make_setup, portfolio, quote

from trading.domain.models import MarketType, Side
from trading.domain.reasons import RejectReason as R
from trading.execution.costs import CostModel
from trading.safety.limits import DEFAULT_LIMITS
from trading.safety.order_risk import position_stress_loss
from trading.safety.risk_manager import RiskManager, liquidation_price

rm = RiskManager(DEFAULT_LIMITS, NO_SLIP)

# Basis: Spot Long, 40 Stueck zu 100, Stop 99, Kapital 10 000.
#   Hoechstverlust   = 40 (Kurs) + 2,4 (Einstieg 0,06 %) + 2,376 (Ausstieg 0,06 % auf 3 960) = 44,776
#   Equity danach    = 10 000 - 2,4 = 9 997,6
#   Restrisiko       = 44,776 - 2,4 = 42,376
#   Exposure         = 4 000
#   Stress (10 %)    = 40 x 10 + 0,06 % x 3 600 = 402,16
BASE_LOSS, BASE_REMAINING, BASE_STRESS, EQUITY_AFTER = D("44.776"), D("42.376"), D("402.16"), D("9997.6")


def evaluate(setup=None, qty=D(40), lev=D(1), entry=D(100), funding=None, pf=None, mark=None, manager=rm):
    return manager.evaluate(setup or make_setup(), qty, lev, quote(entry, mark, funding), pf or portfolio())


def futures_setup(**kw):
    return make_setup(market_type=MarketType.FUTURES, **kw)


def short_setup(**kw):
    kw.setdefault("stop", D(101))
    kw.setdefault("tp", D(98))
    return futures_setup(side=Side.SHORT, **kw)


# ------------------------------------------------------------------ Grundfall


def test_baseline_trade_is_approved_and_all_figures_are_exact():
    d = evaluate()
    assert d.approved and d.reasons == ()
    assert d.worst_case_loss == BASE_LOSS and d.entry_fee == D("2.4")
    assert d.risk_pct == BASE_LOSS / D(10000)
    assert d.total_risk_pct_after == BASE_REMAINING / EQUITY_AFTER
    assert d.gross_exposure_pct_after == D(4000) / EQUITY_AFTER
    assert d.stress_loss_pct_after == BASE_STRESS / EQUITY_AFTER
    assert d.margin_required == D(4000) and d.liquidation_price is None


def test_spread_and_slippage_paid_at_entry_reduce_the_equity_basis():
    # Kauf zu 100,10 bei Mitte 100: 4 sofortiger Buchverlust + Gebuehr 0,06 % x 4 004 = 2,4024
    d = evaluate(entry=D("100.1"), mark=D(100))
    after = D(10000) - D("2.4024") - D(4)
    assert d.gross_exposure_pct_after == D(4000) / after
    assert d.total_risk_pct_after == (d.worst_case_loss - D("2.4024")) / after


# ------------------------------------------------------------------ Risiko je Trade und Gesamtrisiko (2 %)


def test_single_trade_risk_above_half_percent_is_blocked():
    d = evaluate(qty=D(60))  # ca. 67 -> 0,67 %
    assert R.TRADE_RISK_TOO_HIGH in d.reasons and not d.approved


def test_trade_risk_boundary_is_inclusive():
    # Grenze ueber das Kapital steuern: 44,776 ist genau 0,5 % von 8 955,2.
    exact = portfolio(equity=BASE_LOSS / D("0.005"))  # 44,776 ist genau 0,5 %
    assert R.TRADE_RISK_TOO_HIGH not in evaluate(pf=exact).reasons
    below = portfolio(equity=BASE_LOSS / D("0.005") - D("0.01"))
    assert R.TRADE_RISK_TOO_HIGH in evaluate(pf=below).reasons


def test_total_portfolio_risk_cap_of_two_percent():
    assert evaluate(pf=portfolio(open_risk=D(150))).approved  # (150 + 42,376) / 9 997,6 = 1,92 %
    d = evaluate(pf=portfolio(open_risk=D(160)))  # (160 + 42,376) / 9 997,6 = 2,02 %
    assert d.reasons == (R.PORTFOLIO_RISK_TOO_HIGH,)


def test_total_risk_boundary_is_exact_against_equity_after_costs():
    limit = D("0.02") * EQUITY_AFTER  # 199,952
    at_limit = portfolio(open_risk=limit - BASE_REMAINING)  # genau 2,0000 %
    assert evaluate(pf=at_limit).approved
    over = portfolio(open_risk=limit - BASE_REMAINING + D("0.001"))
    assert evaluate(pf=over).reasons == (R.PORTFOLIO_RISK_TOO_HIGH,)


def test_portfolio_risk_cap_cannot_be_exceeded_by_stacking_small_trades():
    open_risk, approved = D(0), 0
    for _ in range(10):
        d = evaluate(pf=portfolio(open_risk=open_risk))
        if not d.approved:
            assert d.reasons == (R.PORTFOLIO_RISK_TOO_HIGH,)
            break
        approved += 1
        open_risk += d.worst_case_loss - d.entry_fee
    assert approved == 4  # 4 x 0,42 % = 1,70 %; der 5. ergaebe 2,12 %
    assert open_risk / D(10000) <= D("0.02")


# ------------------------------------------------------------------ Brutto-Exposure (100 %)


def test_gross_exposure_boundary_is_exact():
    ok = portfolio(gross_exposure=EQUITY_AFTER - D(4000))  # danach genau 100 %
    assert evaluate(pf=ok).approved
    over = portfolio(gross_exposure=EQUITY_AFTER - D(4000) + D("0.01"))
    assert evaluate(pf=over).reasons == (R.GROSS_EXPOSURE_TOO_HIGH,)


def test_exposure_counts_position_value_at_the_mark_for_longs_and_shorts():
    # 60 Stueck Futures zu 100 = 6 000 Exposure; mit 4 100 Bestand -> 101 % -> blockiert (Long wie Short)
    pf = portfolio(gross_exposure=D(4100))
    for setup in (futures_setup(stop=D(95), tp=D(110)), short_setup(stop=D(105), tp=D(90))):
        d = evaluate(setup, qty=D(60), lev=D(3), funding=D(0), pf=pf)
        assert R.GROSS_EXPOSURE_TOO_HIGH in d.reasons


def test_leverage_does_not_shrink_exposure():
    # Hebel senkt nur die Margin, nicht den Positionswert: 110 % Exposure bleiben 110 %.
    d = evaluate(futures_setup(stop=D(95), tp=D(110)), qty=D(110), lev=D(5), funding=D(0))
    assert R.GROSS_EXPOSURE_TOO_HIGH in d.reasons


# ------------------------------------------------------------------ Stress-Verlust (10 % Schock, hoechstens 5 %)


def test_stress_loss_boundary_is_exact():
    limit = D("0.05") * EQUITY_AFTER  # 499,88
    assert evaluate(pf=portfolio(stress_loss=limit - BASE_STRESS)).approved
    over = portfolio(stress_loss=limit - BASE_STRESS + D("0.01"))
    assert evaluate(pf=over).reasons == (R.STRESS_LOSS_TOO_HIGH,)


def test_stress_loss_of_a_single_position_alone_can_block():
    # 55 Stueck zu 100: Schock kostet 550 + Gebuehr = 5,5 % > 5 %, obwohl der Stop nur 1 entfernt ist.
    d = evaluate(make_setup(stop=D("99.5"), tp=D(103)), qty=D(55))
    assert R.STRESS_LOSS_TOO_HIGH in d.reasons and R.TRADE_RISK_TOO_HIGH not in d.reasons


def test_stress_loss_figures_by_hand():
    args = dict(quantity=D(40), mark=D(100), entry_price=D(100), margin=D(4000), liquidation=None,
                shock=D("0.10"), costs=NO_SLIP)
    assert position_stress_loss(Side.LONG, **args) == D("402.16")  # 40 x 10 + 0,06 % x 3 600
    assert position_stress_loss(Side.SHORT, **args) == D("402.64")  # 40 x 10 + 0,06 % x 4 400
    slip = CostModel()  # Stop-Slippage 5 bp: Ausstieg 89,955 statt 90
    assert position_stress_loss(Side.LONG, **dict(args, costs=slip)) == \
        D(40) * (D(100) - D("89.955")) + slip.fee(D(40) * D("89.955"))


def test_stress_loss_when_the_shock_reaches_the_liquidation_price():
    # 5x Hebel, Liquidation bei 80,5. Schock 25 % -> Kurs 75 -> liquidiert: die GANZE Margin (800) ist weg.
    args = dict(quantity=D(40), entry_price=D(100), margin=D(800), liquidation=D("80.5"),
                shock=D("0.25"), costs=NO_SLIP)
    assert position_stress_loss(Side.LONG, mark=D(100), **args) == D(800)
    # Liegt die Position schon 200 im Minus (Kurs 95), sind ab JETZT noch 600 zu verlieren.
    assert position_stress_loss(Side.LONG, mark=D(95), **args) == D(600)
    # Bei 10 % Schock wird die Liquidation nicht erreicht -> normaler Kursverlust.
    assert position_stress_loss(Side.LONG, mark=D(100), **dict(args, shock=D("0.10"))) == D("402.16")
    # Short spiegelbildlich: Liquidation bei 119,5.
    short = dict(args, liquidation=D("119.5"))
    assert position_stress_loss(Side.SHORT, mark=D(100), **short) == D(800)
    # Nie negativ: ist mehr als die Margin schon verloren, gibt es nichts mehr zu verlieren.
    assert position_stress_loss(Side.LONG, mark=D(79), **args) == D(0)


def test_stricter_shock_makes_leveraged_positions_fail_the_stress_test():
    strict = RiskManager(DEFAULT_LIMITS.tightened(stress_shock=D("0.25")), NO_SLIP)
    d = evaluate(futures_setup(stop=D(90), tp=D(110)), qty=D(4), lev=D(5), funding=D(0), manager=strict)
    assert d.approved  # 4 Stueck: Margin 80 = 0,8 %
    d = evaluate(futures_setup(stop=D(98), tp=D(105)), qty=D(40), lev=D(5), funding=D(0), manager=strict)
    assert R.STRESS_LOSS_TOO_HIGH in d.reasons  # Margin 800 = 8 %


def test_all_limits_must_hold_at_the_same_time():
    pf = portfolio(open_risk=D(190), gross_exposure=D(7000), stress_loss=D(300))
    d = evaluate(pf=pf)
    assert set(d.reasons) == {R.PORTFOLIO_RISK_TOO_HIGH, R.GROSS_EXPOSURE_TOO_HIGH, R.STRESS_LOSS_TOO_HIGH}
    # Jede Grenze fuer sich genuegt zum Blockieren:
    assert evaluate(pf=portfolio(open_risk=D(190))).reasons == (R.PORTFOLIO_RISK_TOO_HIGH,)
    assert evaluate(pf=portfolio(gross_exposure=D(7000))).reasons == (R.GROSS_EXPOSURE_TOO_HIGH,)
    assert evaluate(pf=portfolio(stress_loss=D(300))).reasons == (R.STRESS_LOSS_TOO_HIGH,)


# ------------------------------------------------------------------ nicht bestimmbares Risiko


def test_stale_or_missing_valuation_of_open_positions_blocks_new_trades():
    d = evaluate(pf=portfolio(valuation_complete=False))
    assert d.reasons == (R.EQUITY_UNCERTAIN,) and not d.approved


def test_futures_without_funding_rate_are_blocked():
    assert evaluate(futures_setup(), lev=D(2)).reasons == (R.FUNDING_MISSING,)


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), 40.0, None])
def test_nonfinite_or_non_decimal_inputs_are_blocked_without_exception(bad):
    assert evaluate(qty=bad).reasons == (R.NON_FINITE_VALUE,)
    assert evaluate(entry=bad).reasons == (R.NON_FINITE_VALUE,)
    assert evaluate(lev=bad).reasons == (R.NON_FINITE_VALUE,)
    assert evaluate(make_setup(stop=bad)).reasons == (R.NON_FINITE_VALUE,)
    assert evaluate(pf=portfolio(equity=bad, free_margin=D(1))).reasons == (R.NON_FINITE_VALUE,)
    assert evaluate(pf=portfolio(gross_exposure=bad)).reasons == (R.NON_FINITE_VALUE,)
    assert evaluate(pf=portfolio(stress_loss=bad)).reasons == (R.NON_FINITE_VALUE,)


def test_nonfinite_mark_or_funding_is_blocked():
    assert evaluate(mark=Decimal("NaN")).reasons == (R.NON_FINITE_VALUE,)
    assert evaluate(futures_setup(), lev=D(2), funding=Decimal("NaN")).reasons == (R.NON_FINITE_VALUE,)
    assert evaluate(mark=D(0)).reasons == (R.INVALID_QUANTITY,)


@pytest.mark.parametrize("symbol", ["S0", "btc-usd", "", None])
def test_malformed_instrument_is_blocked(symbol):
    assert evaluate(make_setup(symbol=symbol)).reasons == (R.INVALID_SYMBOL,)


def test_no_equity_or_bad_quantity():
    assert evaluate(pf=portfolio(equity=D(0))).reasons == (R.NO_EQUITY,)
    assert evaluate(qty=D(0)).reasons == (R.INVALID_QUANTITY,)
    assert evaluate(qty=D(-1)).reasons == (R.INVALID_QUANTITY,)


def test_order_whose_costs_would_wipe_out_the_equity_is_blocked():
    d = evaluate(pf=portfolio(equity=D(2), free_margin=D(5000)))  # Einstiegsgebuehr 2,4 > Kapital 2
    assert R.NO_EQUITY in d.reasons


# ------------------------------------------------------------------ Markt- und Hebelregeln


def test_spot_short_is_not_allowed():
    d = evaluate(make_setup(side=Side.SHORT, entry=D(100), stop=D(101), tp=D(98)))
    assert R.SPOT_SHORT_NOT_ALLOWED in d.reasons


def test_spot_leverage_is_not_allowed():
    assert R.SPOT_LEVERAGE_NOT_ALLOWED in evaluate(lev=D(2)).reasons


def test_leverage_above_limit_is_blocked():
    d = evaluate(futures_setup(), qty=D(10), lev=D(6), funding=D("0.0001"))
    assert R.LEVERAGE_TOO_HIGH in d.reasons
    assert evaluate(futures_setup(), qty=D(10), lev=D(5), funding=D("0.0001")).approved


def test_leverage_below_one_is_blocked():
    assert R.LEVERAGE_TOO_LOW in evaluate(futures_setup(), lev=D("0.5"), funding=D(0)).reasons


def test_futures_long_and_short_within_limits_are_approved():
    assert evaluate(futures_setup(), lev=D(3), funding=D("0.0001")).approved
    assert evaluate(short_setup(), lev=D(3), funding=D("0.0001")).approved


def test_liquidation_before_stop_is_blocked():
    # 5x Hebel: Liquidation bei 80,5. Ein Stop bei 80 liegt dahinter -> verboten.
    d = evaluate(futures_setup(stop=D(80)), qty=D(2), lev=D(5), funding=D("0.0001"))
    assert R.LIQUIDATION_TOO_CLOSE in d.reasons
    # Stop bei 90 liegt komfortabel vor der Liquidation -> ok
    assert evaluate(futures_setup(stop=D(90)), qty=D(4), lev=D(5), funding=D("0.0001")).approved


def test_liquidation_buffer_boundary_factor_1_25():
    # Abstand zur Liquidation: 19,5. Erlaubter Stop-Abstand: 19,5 / 1,25 = 15,6.
    ok = evaluate(futures_setup(stop=D("84.4"), tp=D(130)), qty=D(2), lev=D(5), funding=D(0))
    assert ok.approved and ok.liquidation_price == D("80.5")
    close = evaluate(futures_setup(stop=D("84.3"), tp=D(130)), qty=D(2), lev=D(5), funding=D(0))
    assert close.reasons == (R.LIQUIDATION_TOO_CLOSE,)  # Stop VOR der Liquidation, aber Puffer zu klein
    short = evaluate(short_setup(stop=D("115.7"), tp=D(70)), qty=D(2), lev=D(5), funding=D(0))
    assert short.reasons == (R.LIQUIDATION_TOO_CLOSE,) and short.liquidation_price == D("119.5")


def test_liquidation_price_formula():
    assert liquidation_price(Side.LONG, D(100), D(5), D("0.005")) == D("80.5")
    assert liquidation_price(Side.SHORT, D(100), D(5), D("0.005")) == D("119.5")


def test_stop_and_take_profit_on_wrong_side_are_blocked():
    assert R.STOP_WRONG_SIDE in evaluate(make_setup(stop=D(101))).reasons
    assert R.STOP_WRONG_SIDE in evaluate(make_setup(stop=D(100))).reasons  # Stop == Einstieg
    assert R.TAKE_PROFIT_WRONG_SIDE in evaluate(make_setup(tp=D(99))).reasons
    assert R.STOP_WRONG_SIDE in evaluate(short_setup(stop=D(99)), funding=D(0)).reasons


# ------------------------------------------------------------------ Bestand, Margin, Notbremsen


def test_duplicate_position_is_blocked_per_instrument_not_per_symbol():
    pf = portfolio(keys=frozenset({inst("BTC-USD", MarketType.SPOT)}), open_count=1)
    assert R.DUPLICATE_POSITION in evaluate(pf=pf).reasons
    # H1: dasselbe Paar als Futures ist ein ANDERES Instrument -> kein Duplikat
    assert R.DUPLICATE_POSITION not in evaluate(futures_setup(), lev=D(2), funding=D(0), pf=pf).reasons


def test_max_open_positions():
    assert R.TOO_MANY_POSITIONS in evaluate(pf=portfolio(open_count=5)).reasons
    assert evaluate(pf=portfolio(open_count=4)).approved


def test_insufficient_margin_is_blocked():
    assert R.INSUFFICIENT_MARGIN in evaluate(pf=portfolio(free_margin=D(100))).reasons


def test_margin_check_includes_the_entry_fee():
    assert evaluate(pf=portfolio(free_margin=D("4002.4"))).approved  # Margin 4 000 + Gebuehr 2,4
    assert evaluate(pf=portfolio(free_margin=D("4002.39"))).reasons == (R.INSUFFICIENT_MARGIN,)


def test_daily_loss_kill_switch():
    # Gemessen am Zustand NACH der Order (AGENTS §2.4): Die Gebuehr dieser Order (2,40) zaehlt mit.
    assert evaluate(pf=portfolio(equity=D(9703), day_start=D(10000))).approved  # danach 9 700,60 = 2,994 %
    d = evaluate(pf=portfolio(equity=D(9701), day_start=D(10000)))  # vorher 2,99 %, danach 3,014 %
    assert R.DAILY_LOSS_LIMIT in d.reasons
    assert R.DAILY_LOSS_LIMIT in evaluate(pf=portfolio(equity=D(9700), day_start=D(10000))).reasons
    # Grenzfall: nach der Order GENAU 3 % (9 702,40 - 2,40 Gebuehr = 9 700) -> "erreicht" heisst gestoppt
    assert R.DAILY_LOSS_LIMIT in evaluate(pf=portfolio(equity=D("9702.40"), day_start=D(10000))).reasons


def test_drawdown_kill_switch():
    pf = portfolio(equity=D(9000), peak=D(10000), day_start=D(9000))
    assert R.DRAWDOWN_LIMIT in evaluate(pf=pf).reasons
    assert R.DRAWDOWN_LIMIT not in evaluate(pf=portfolio(equity=D(9003), peak=D(10000), day_start=D(9003))).reasons
    # 9 001 waeren vorher 9,99 % - die Gebuehr der Order (2,40) fuehrt auf 10,01 %: abgelehnt
    assert R.DRAWDOWN_LIMIT in evaluate(pf=portfolio(equity=D(9001), peak=D(10000), day_start=D(9001))).reasons
    # Grenzfall: nach der Order GENAU 10 % (9 002,40 - 2,40 Gebuehr = 9 000)
    assert R.DRAWDOWN_LIMIT in evaluate(pf=portfolio(equity=D("9002.40"), peak=D(10000), day_start=D("9002.40"))).reasons


def test_all_violations_are_collected_not_just_the_first():
    d = evaluate(
        make_setup(side=Side.SHORT, entry=D(100), stop=D(101), tp=D(98)),
        qty=D(100),
        lev=D(2),
        pf=portfolio(open_count=5),
    )
    assert {R.SPOT_SHORT_NOT_ALLOWED, R.SPOT_LEVERAGE_NOT_ALLOWED, R.TOO_MANY_POSITIONS} <= set(
        d.reasons
    )


# ------------------------------------------------------------------ Funding im geplanten Risiko


def test_funding_adds_to_worst_case():
    base = evaluate(futures_setup(), lev=D(2), funding=D(0)).worst_case_loss
    with_funding = evaluate(futures_setup(), lev=D(2), funding=D("0.001")).worst_case_loss
    assert with_funding - base == D("0.001") * D(40) * D(100)


def test_negative_funding_never_reduces_the_planned_risk():
    plus = evaluate(futures_setup(), lev=D(2), funding=D("0.001")).worst_case_loss
    minus = evaluate(futures_setup(), lev=D(2), funding=D("-0.001")).worst_case_loss
    zero = evaluate(futures_setup(), lev=D(2), funding=D(0)).worst_case_loss
    assert minus == plus > zero  # der Satz kann bis zur naechsten Abrechnung drehen


def test_funding_buffer_can_cover_several_intervals():
    three = RiskManager(DEFAULT_LIMITS, CostModel(slippage_bps=D(0), stop_slippage_bps=D(0),
                                                  funding_buffer_intervals=3))
    one = evaluate(futures_setup(), lev=D(2), funding=D("0.001")).worst_case_loss
    assert evaluate(futures_setup(), lev=D(2), funding=D("0.001"), manager=three).worst_case_loss == one + D(8)


def test_tighter_limits_are_respected():
    strict = RiskManager(DEFAULT_LIMITS.tightened(max_leverage=D(2)), NO_SLIP)
    d = evaluate(futures_setup(), qty=D(10), lev=D(3), funding=D(0), manager=strict)
    assert R.LEVERAGE_TOO_HIGH in d.reasons


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_worst_case_loss_is_never_negative_for_valid_setups(side):
    s = futures_setup() if side is Side.LONG else short_setup()
    assert evaluate(s, lev=D(2), funding=Decimal(0)).worst_case_loss > 0
