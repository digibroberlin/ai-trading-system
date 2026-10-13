"""Der Risikomanager ist rein regelbasiert und unabhaengig."""

from decimal import Decimal

import pytest
from helpers import D, NO_SLIP, make_setup, portfolio

from trading.domain.models import MarketType, Side
from trading.domain.reasons import RejectReason as R
from trading.safety.limits import DEFAULT_LIMITS
from trading.safety.risk_manager import RiskManager, liquidation_price

rm = RiskManager(DEFAULT_LIMITS, NO_SLIP)

# Basis: Kurs 100, Stop 99, Menge 40 -> Hoechstverlust 40 + Gebuehren 2,4 + 2,376 = 44,776 (0,448 %)
BASE_LOSS = D("44.776")


def evaluate(setup=None, qty=D(40), lev=D(1), entry=D(100), funding=None, pf=None):
    return rm.evaluate(setup or make_setup(), qty, lev, entry, funding, pf or portfolio())


def test_baseline_trade_is_approved_and_risk_is_exact():
    d = evaluate()
    assert d.approved and d.reasons == ()
    assert d.worst_case_loss == BASE_LOSS
    assert d.risk_pct == BASE_LOSS / D(10000)


def test_single_trade_risk_above_half_percent_is_blocked():
    d = evaluate(qty=D(60))  # ca. 67 -> 0,67 %
    assert R.TRADE_RISK_TOO_HIGH in d.reasons and not d.approved


def test_total_portfolio_risk_cap_of_two_percent():
    # 150 bereits eingeplant + 44,776 = 194,776 (1,95 %) -> ok
    assert evaluate(pf=portfolio(open_risk=D(150))).approved
    # 160 + 44,776 = 204,776 (2,05 %) -> blockiert
    d = evaluate(pf=portfolio(open_risk=D(160)))
    assert R.PORTFOLIO_RISK_TOO_HIGH in d.reasons and not d.approved


def test_portfolio_risk_cap_cannot_be_exceeded_by_stacking_small_trades():
    open_risk = D(0)
    approved = 0
    for _ in range(10):
        d = evaluate(pf=portfolio(open_risk=open_risk))
        if not d.approved:
            break
        approved += 1
        open_risk += d.worst_case_loss
    assert approved == 4  # 4 x 0,448 % = 1,79 %; der 5. wuerde 2,24 % ergeben
    assert open_risk / D(10000) <= D("0.02")


def test_spot_short_is_not_allowed():
    d = evaluate(make_setup(side=Side.SHORT, entry=D(100), stop=D(101), tp=D(98)))
    assert R.SPOT_SHORT_NOT_ALLOWED in d.reasons


def test_spot_leverage_is_not_allowed():
    assert R.SPOT_LEVERAGE_NOT_ALLOWED in evaluate(lev=D(2)).reasons


def futures_setup(**kw):
    return make_setup(market_type=MarketType.FUTURES, **kw)


def test_leverage_above_limit_is_blocked():
    d = evaluate(futures_setup(), qty=D(10), lev=D(6), funding=D("0.0001"))
    assert R.LEVERAGE_TOO_HIGH in d.reasons


def test_leverage_below_one_is_blocked():
    assert R.LEVERAGE_TOO_LOW in evaluate(futures_setup(), lev=D("0.5")).reasons


def test_futures_long_and_short_within_limits_are_approved():
    assert evaluate(futures_setup(), lev=D(3), funding=D("0.0001")).approved
    short = futures_setup(side=Side.SHORT, entry=D(100), stop=D(101), tp=D(98))
    assert evaluate(short, lev=D(3), funding=D("0.0001")).approved


def test_liquidation_before_stop_is_blocked():
    # 5x Hebel: Liquidation bei 80,5. Ein Stop bei 80 liegt dahinter -> verboten.
    d = evaluate(futures_setup(stop=D(80)), qty=D(2), lev=D(5), funding=D("0.0001"))
    assert R.LIQUIDATION_TOO_CLOSE in d.reasons
    # Stop bei 90 liegt komfortabel vor der Liquidation -> ok
    assert evaluate(futures_setup(stop=D(90)), qty=D(4), lev=D(5), funding=D("0.0001")).approved


def test_liquidation_price_formula():
    assert liquidation_price(Side.LONG, D(100), D(5), D("0.005")) == D("80.5")
    assert liquidation_price(Side.SHORT, D(100), D(5), D("0.005")) == D("119.5")


def test_stop_and_take_profit_on_wrong_side_are_blocked():
    assert R.STOP_WRONG_SIDE in evaluate(make_setup(stop=D(101))).reasons
    assert R.TAKE_PROFIT_WRONG_SIDE in evaluate(make_setup(tp=D(99))).reasons
    short = make_setup(side=Side.SHORT, market_type=MarketType.FUTURES, stop=D(99), tp=D(98))
    assert R.STOP_WRONG_SIDE in evaluate(short, funding=D(0)).reasons


def test_duplicate_position_is_blocked():
    pf = portfolio(keys=frozenset({("BTC-USD", MarketType.SPOT)}), open_count=1)
    assert R.DUPLICATE_POSITION in evaluate(pf=pf).reasons


def test_max_open_positions():
    assert R.TOO_MANY_POSITIONS in evaluate(pf=portfolio(open_count=5)).reasons
    assert evaluate(pf=portfolio(open_count=4)).approved


def test_insufficient_margin_is_blocked():
    assert R.INSUFFICIENT_MARGIN in evaluate(pf=portfolio(free_margin=D(100))).reasons


def test_daily_loss_kill_switch():
    assert evaluate(pf=portfolio(equity=D(9701), day_start=D(10000))).approved
    d = evaluate(pf=portfolio(equity=D(9700), day_start=D(10000)))
    assert R.DAILY_LOSS_LIMIT in d.reasons


def test_drawdown_kill_switch():
    pf = portfolio(equity=D(9000), peak=D(10000), day_start=D(9000))
    assert R.DRAWDOWN_LIMIT in evaluate(pf=pf).reasons


def test_no_equity_or_bad_quantity():
    assert evaluate(pf=portfolio(equity=D(0))).reasons == (R.NO_EQUITY,)
    assert evaluate(qty=D(0)).reasons == (R.INVALID_QUANTITY,)
    assert evaluate(qty=D(-1)).reasons == (R.INVALID_QUANTITY,)


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


def test_funding_adds_to_worst_case():
    base = evaluate(futures_setup(), lev=D(2), funding=D(0)).worst_case_loss
    with_funding = evaluate(futures_setup(), lev=D(2), funding=D("0.001")).worst_case_loss
    assert with_funding - base == D("0.001") * D(40) * D(100)


def test_tighter_limits_are_respected():
    strict = RiskManager(DEFAULT_LIMITS.tightened(max_leverage=D(2)), NO_SLIP)
    d = strict.evaluate(futures_setup(), D(10), D(3), D(100), D(0), portfolio())
    assert R.LEVERAGE_TOO_HIGH in d.reasons


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_worst_case_loss_is_never_negative_for_valid_setups(side):
    if side is Side.LONG:
        s = futures_setup()
    else:
        s = futures_setup(side=Side.SHORT, entry=D(100), stop=D(101), tp=D(98))
    assert evaluate(s, lev=D(2), funding=Decimal(0)).worst_case_loss > 0


# ------------------------------------------------------------------ Haertung


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), 40.0, None])
def test_nonfinite_or_non_decimal_inputs_are_blocked_without_exception(bad):
    assert evaluate(qty=bad).reasons == (R.NON_FINITE_VALUE,)
    assert evaluate(entry=bad).reasons == (R.NON_FINITE_VALUE,)
    assert evaluate(lev=bad).reasons == (R.NON_FINITE_VALUE,)
    assert evaluate(make_setup(stop=bad)).reasons == (R.NON_FINITE_VALUE,)
    assert evaluate(pf=portfolio(equity=bad, free_margin=D(1))).reasons == (R.NON_FINITE_VALUE,)


def test_nonfinite_funding_rate_is_blocked():
    assert evaluate(futures_setup(), lev=D(2), funding=Decimal("NaN")).reasons == (R.NON_FINITE_VALUE,)
