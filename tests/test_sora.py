"""SORA: Positionsgroessen, Risikobudget, Hebel, Liquidation, automatische Blockaden."""

import dataclasses
import random
import threading
from decimal import ROUND_DOWN, Decimal

import pytest
from helpers import D, NO_SLIP, inst, make_setup, portfolio, snapshot

from trading.agents import AgentName, MarketContext, RuleBasedSora, SizingPolicy, SoraConfig
from trading.domain.models import MarketType, Side
from trading.domain.reasons import RejectReason as R
from trading.execution.costs import CostModel
from trading.safety.limits import DEFAULT_LIMITS
from trading.safety.risk_manager import RiskManager

POLICY = SizingPolicy.from_limits(DEFAULT_LIMITS)
FUT = {"market_type": MarketType.FUTURES, "funding": D("0.0001")}
RM = RiskManager(DEFAULT_LIMITS, NO_SLIP)


def make_sora(costs=NO_SLIP, config=None, policy=POLICY):
    return RuleBasedSora(policy, costs, config)


def size(setup=None, snap_kw=None, pf=None, sora=None, snap=None):
    setup = setup or make_setup()
    snap = snap or snapshot(**(snap_kw or {}))
    return (sora or make_sora()).size(setup, MarketContext(snap), pf or portfolio())


def fut_setup(side=Side.LONG, **kw):
    if side is Side.SHORT:
        kw.setdefault("entry", D(100)); kw.setdefault("stop", D(101)); kw.setdefault("tp", D(98))
    return make_setup(market_type=MarketType.FUTURES, side=side, **kw)


def unit_loss(side=Side.LONG, fill=D(100), stop=D(99), funding=None, costs=NO_SLIP):
    return costs.worst_case_loss(side, D(1), fill, stop, funding)


def floor4(x):
    return x.quantize(D("0.0001"), rounding=ROUND_DOWN)


# ------------------------------------------------------------------ Positionsgroesse


def test_spot_long_size_matches_hand_calculation():
    p = size()
    budget = D(10000) * D("0.004")  # 0,4 % = 40
    assert p.approve and p.leverage == D(1)
    assert p.quantity == floor4(budget / unit_loss())  # 35.7334
    assert p.planned_loss <= budget
    assert budget - p.planned_loss < unit_loss() * D("0.0001")  # nur Rundungsrest
    assert p.risk_pct <= D("0.004")
    assert p.notional == p.quantity * D(100)
    assert D(0) == p.liquidation_price or p.liquidation_price is None


def test_size_scales_linearly_with_equity():
    base = size().quantity
    half = size(pf=portfolio(equity=D(5000))).quantity
    double = size(pf=portfolio(equity=D(20000))).quantity
    assert abs(half - base / 2) <= D("0.0002") and abs(double - base * 2) <= D("0.0002")


def test_wider_stop_means_smaller_position_and_equal_money_risk():
    tight = size(make_setup(stop=D("99"), tp=D(110)))
    wide = size(make_setup(stop=D("96"), tp=D(110)))
    assert wide.quantity < tight.quantity
    assert abs(wide.planned_loss - tight.planned_loss) < D("0.2")  # beide ~40


def test_short_futures_mirror_long():
    long_ = size(fut_setup(), FUT)
    short = size(fut_setup(Side.SHORT), FUT)
    assert long_.approve and short.approve
    assert abs(long_.quantity - short.quantity) / long_.quantity < D("0.01")
    assert short.liquidation_price > D(100) > long_.liquidation_price


def test_fees_slippage_and_spread_are_included():
    free = size()
    costly = size(sora=make_sora(CostModel()), snap_kw={"half_spread": D("0.05")})
    assert costly.approve and costly.quantity < free.quantity
    fill = D("100.05") * (1 + D(2) / 10000)
    expected_unit = CostModel().worst_case_loss(Side.LONG, D(1), fill, D(99))
    assert costly.quantity == floor4(D(40) / expected_unit)
    assert costly.planned_loss <= D(40)


def test_funding_buffer_is_included_for_futures():
    no_fund = size(fut_setup(), dict(FUT, funding=D(0)))
    with_fund = size(fut_setup(), dict(FUT, funding=D("0.001")))
    assert with_fund.quantity < no_fund.quantity


def test_quantity_is_rounded_down_to_the_step_never_up():
    coarse = size(sora=make_sora(config=SoraConfig(quantity_step=D(1))))
    assert coarse.quantity == D(35)  # 35.73 -> 35
    assert coarse.planned_loss < D(40)


# ------------------------------------------------------------------ 2-%-Gesamtrisiko


def remaining_after(p, pf):
    """Gesamtrisiko NACH dem Trade wie im Risikomanager: (offen + Hoechstverlust - Einstiegsgebuehr) / Equity danach."""
    fee = NO_SLIP.fee(p.notional)
    return (pf.open_risk + p.planned_loss - fee) / (pf.equity - fee)


def test_remaining_budget_shrinks_position_and_zero_budget_blocks():
    # Je Stueck: Restrisiko 1,0594 (= 1,1194 - 0,06 Einstiegsgebuehr); die Gebuehr mindert zudem die Equity.
    per_unit = D("1.0594") + D("0.02") * D("0.06")
    full = size(pf=portfolio(open_risk=D(160)))  # Rest 40 -> das Ziel-Risiko je Trade (0,4 %) begrenzt
    assert full.quantity == floor4(D(40) / unit_loss())
    half_pf = portfolio(open_risk=D(180))  # Rest 20 -> das Gesamtrisiko begrenzt
    half = size(pf=half_pf)
    assert half.quantity == floor4(D(20) / per_unit)  # 18,8572
    assert D("0.0199999") < remaining_after(half, half_pf) <= D("0.02")  # Budget ausgeschoepft, nie ueberschritten
    for used in (D(200), D(250)):
        blocked = size(pf=portfolio(open_risk=used))
        assert not blocked.approve and R.PORTFOLIO_RISK_TOO_HIGH in blocked.reject_reasons


def test_risk_of_other_strategies_counts_against_the_same_budget():
    # SORA kennt keine einzelnen Strategien - es zaehlt das gemeinsame Gesamtrisiko aller Positionen.
    pf = portfolio(open_risk=D(190))
    own = size(make_setup(strategy="a"), pf=pf)
    other = size(make_setup(strategy="b"), pf=pf)
    assert own.quantity == other.quantity == floor4(D(10) / (D("1.0594") + D("0.02") * D("0.06")))
    assert remaining_after(own, pf) <= D("0.02")


def test_filling_the_portfolio_never_exceeds_two_percent():
    open_risk, approved = D(0), []
    for n in range(8):
        p = size(pf=portfolio(open_risk=open_risk, open_count=n))
        if not p.approve:
            assert R.TOO_MANY_POSITIONS in p.reject_reasons
            break
        approved.append(p)
        open_risk += p.planned_loss
        assert open_risk / D(10000) <= D("0.02")
    assert len(approved) == 5 and all(p.risk_pct <= D("0.004") for p in approved)


def test_stricter_limits_lower_the_budget():
    strict = SizingPolicy.from_limits(DEFAULT_LIMITS.tightened(max_portfolio_risk=D("0.01")))
    pf = portfolio(open_risk=D(80))
    p = size(pf=pf, sora=make_sora(policy=strict))
    assert p.quantity == floor4(D(20) / (D("1.0594") + D("0.01") * D("0.06")))  # 1 % = 100, bereits 80 belegt
    assert remaining_after(p, pf) <= D("0.01")
    assert not size(pf=portfolio(open_risk=D(100)), sora=make_sora(policy=strict)).approve


def test_risk_budget_helper():
    s = make_sora()
    assert s.risk_budget(portfolio()) == D(40)
    assert s.risk_budget(portfolio(open_risk=D(190))) == D(10)
    assert s.risk_budget(portfolio(open_risk=D(500))) == D(0)


def test_config_cannot_exceed_policy_limits():
    with pytest.raises(ValueError):
        make_sora(config=SoraConfig(risk_per_trade=D("0.01")))
    with pytest.raises(ValueError):
        SoraConfig(margin_utilization=D("1.5"))
    with pytest.raises(ValueError):
        SoraConfig(quantity_step=D(0))
    # Hebel-Praeferenz ueber dem Limit wird auf das Limit gekappt (nie darueber):
    s = make_sora(config=SoraConfig(max_leverage=D(50)))
    assert s._max_leverage == DEFAULT_LIMITS.max_leverage


def test_sora_holds_no_safety_limits_object():
    s = make_sora()
    assert not any(type(v).__name__ == "SafetyLimits" for v in vars(s).values())
    with pytest.raises(dataclasses.FrozenInstanceError):
        POLICY.max_portfolio_risk = D("0.5")  # type: ignore[misc]


# ------------------------------------------------------------------ Hebel / Margin


def test_spot_never_uses_leverage_even_when_cash_is_short():
    p = size(pf=portfolio(free_margin=D(50)))
    assert p.approve and p.leverage == D(1) and p.quantity < D("0.4")  # nur was bar bezahlbar ist
    assert p.margin + D("0.06") * p.quantity <= D(50) * D("0.8") + D("0.01")


def test_futures_use_lowest_leverage_that_works():
    assert size(fut_setup(), FUT).leverage == D(1)  # genug Margin -> kein Hebel noetig
    p = size(fut_setup(), FUT, pf=portfolio(free_margin=D(1500)))
    assert p.leverage == D(3) and p.margin <= D(1500) * D("0.8")


def test_position_is_reduced_when_margin_is_short_even_at_max_leverage():
    full = size(fut_setup(), FUT)
    small = size(fut_setup(), FUT, pf=portfolio(free_margin=D(1000)))
    assert small.approve and small.leverage == D(3) and small.quantity < full.quantity
    assert small.margin <= D(1000) * D("0.8")
    assert small.planned_loss < full.planned_loss  # kleiner, nie groesser


def test_leverage_is_limited_by_liquidation_distance():
    wide = fut_setup(stop=D(70), tp=D(160))  # Stop 30 % entfernt
    p = size(wide, FUT, pf=portfolio(free_margin=D(60)))
    # 3x waere margin-seitig besser, aber die Liquidation (67,2) laege zu nah am Stop (70)
    assert p.approve and p.leverage == D(2)
    assert abs(p.liquidation_price - D("50.5")) < D("0.01")


def test_stop_too_far_for_any_leverage_is_blocked_as_liquidation_risk():
    p = size(fut_setup(stop=D(10), tp=D(200)), FUT)
    assert not p.approve and R.LIQUIDATION_TOO_CLOSE in p.reject_reasons


def test_insufficient_margin_blocks():
    for free in (D(0), D(5)):
        p = size(pf=portfolio(free_margin=free))
        assert not p.approve and R.INSUFFICIENT_MARGIN in p.reject_reasons


def test_max_leverage_config_is_respected():
    s = make_sora(config=SoraConfig(max_leverage=D(2)))
    p = size(fut_setup(), FUT, pf=portfolio(free_margin=D(1500)), sora=s)
    assert p.leverage == D(2)


# ------------------------------------------------------------------ automatische Blockaden


def test_spot_short_is_blocked():
    p = size(make_setup(side=Side.SHORT, stop=D(101), tp=D(98)))
    assert not p.approve and R.SPOT_SHORT_NOT_ALLOWED in p.reject_reasons and p.quantity == 0


@pytest.mark.parametrize(
    "setup_kw, reason",
    [
        ({"stop": D(101)}, R.STOP_WRONG_SIDE),
        ({"stop": D(100)}, R.STOP_WRONG_SIDE),
        ({"tp": D(99)}, R.TAKE_PROFIT_WRONG_SIDE),
    ],
)
def test_wrong_geometry_is_blocked(setup_kw, reason):
    p = size(make_setup(**setup_kw))
    assert not p.approve and reason in p.reject_reasons


@pytest.mark.parametrize(
    "field, value, reason",
    [
        ("stop_loss", None, R.MISSING_DATA),
        ("entry", None, R.MISSING_DATA),
        ("stop_loss", D("NaN"), R.NON_FINITE_VALUE),
        ("take_profit", 102.0, R.NON_FINITE_VALUE),
        ("stop_loss", D(0), R.NON_POSITIVE_PRICE),
        ("stop_loss", D(-3), R.NON_POSITIVE_PRICE),
    ],
)
def test_incomplete_or_invalid_prices_are_blocked_not_crashing(field, value, reason):
    p = size(dataclasses.replace(make_setup(), **{field: value}))
    assert not p.approve and reason in p.reject_reasons


def test_invalid_account_data_is_blocked():
    assert R.NO_EQUITY in size(pf=portfolio(equity=D(0))).reject_reasons
    assert R.NO_EQUITY in size(pf=portfolio(equity=D(-100), free_margin=D(0))).reject_reasons
    nan_pf = dataclasses.replace(portfolio(), free_margin=D("NaN"))
    assert R.NON_FINITE_VALUE in size(pf=nan_pf).reject_reasons


def test_market_data_problems_block():
    assert R.SYMBOL_MISMATCH in size(snap_kw={"symbol": "ETH-USD"}).reject_reasons
    assert R.CROSSED_BOOK in size(snap=dataclasses.replace(snapshot(), bid=D(101), ask=D(100))).reject_reasons
    assert R.MISSING_DATA in size(snap=dataclasses.replace(snapshot(), bid=None)).reject_reasons
    assert R.NON_POSITIVE_PRICE in size(snap_kw={"mid": D(0)}).reject_reasons
    assert R.FUNDING_MISSING in size(fut_setup(), {"market_type": MarketType.FUTURES}).reject_reasons
    assert R.NON_FINITE_VALUE in size(fut_setup(), dict(FUT, funding=D("NaN"))).reject_reasons


def test_market_already_beyond_stop_is_blocked():
    p = size(make_setup(entry=D("99.6"), stop=D("99.3"), tp=D(103)), {"mid": D(99)})
    assert R.STOP_WRONG_SIDE in p.reject_reasons


def test_emergency_brakes_and_portfolio_state_block():
    assert R.DAILY_LOSS_LIMIT in size(pf=portfolio(equity=D(9700), day_start=D(10000))).reject_reasons
    assert R.DRAWDOWN_LIMIT in size(pf=portfolio(equity=D(9000), peak=D(10000), day_start=D(9000))).reject_reasons
    assert R.TOO_MANY_POSITIONS in size(pf=portfolio(open_count=5)).reject_reasons
    dup = portfolio(keys=frozenset({inst("BTC-USD", MarketType.SPOT)}), open_count=1)
    assert R.DUPLICATE_POSITION in size(pf=dup).reject_reasons


def test_all_blocking_reasons_are_collected_together():
    p = size(pf=portfolio(open_count=5, equity=D(9000), peak=D(10000), day_start=D(9000), open_risk=D(190)))
    assert {R.TOO_MANY_POSITIONS, R.DRAWDOWN_LIMIT} <= set(p.reject_reasons)
    assert len(p.reasons) >= 2


def test_too_small_positions_are_blocked():
    p = size(pf=portfolio(equity=D(10), free_margin=D(10)))  # Budget 0,04 -> Notional ca. 3,6
    assert not p.approve and R.QUANTITY_TOO_SMALL in p.reject_reasons


def test_proposal_identity():
    assert size().setup_id == "s1"
    assert make_sora().name is AgentName.SORA


# ------------------------------------------------------------------ Gegenprobe mit dem unabhaengigen Risikomanager


def test_every_sora_approval_is_accepted_by_the_independent_risk_manager_random():
    rng = random.Random(20261013)
    approved = blocked = 0
    for case in range(1500):
        price = D(str(round(10 ** rng.uniform(-0.3, 5), 4)))
        if price <= 0:
            continue
        mt = rng.choice([MarketType.SPOT, MarketType.FUTURES])
        side = Side.LONG if mt is MarketType.SPOT else rng.choice([Side.LONG, Side.SHORT])
        stop_pct = D(str(round(rng.uniform(0.002, 0.45), 4)))
        tp_pct = D(str(round(rng.uniform(0.005, 0.60), 4)))
        sign = side.sign
        stop = price * (1 - sign * stop_pct)
        tp = price * (1 + sign * tp_pct)
        equity = D(str(round(rng.uniform(100, 1_000_000), 2)))
        free = equity * D(str(round(rng.uniform(0.02, 1.0), 3)))
        open_risk = equity * D(str(round(rng.uniform(0, 0.022), 5)))
        pf = portfolio(equity=equity, free_margin=free, open_risk=open_risk,
                       open_count=rng.randint(0, 5),
                       gross_exposure=equity * D(str(round(rng.uniform(0, 1.05), 4))),
                       stress_loss=equity * D(str(round(rng.uniform(0, 0.055), 5))))
        funding = D(str(round(rng.uniform(-0.001, 0.001), 6))) if mt is MarketType.FUTURES else None
        spread = D(str(round(rng.uniform(0, 8), 2)))
        costs = rng.choice([NO_SLIP, CostModel()])
        setup = make_setup(market_type=mt, side=side, entry=price, stop=stop, tp=tp)
        snap = snapshot(mid=price, half_spread=price * spread / 20000, market_type=mt, funding=funding)

        proposal = RuleBasedSora(POLICY, costs).size(setup, MarketContext(snap), pf)
        if not proposal.approve:
            blocked += 1
            assert proposal.quantity == 0 and proposal.reject_reasons
            continue
        approved += 1
        rm = RiskManager(DEFAULT_LIMITS, costs)
        decision = rm.evaluate(setup, proposal.quantity, proposal.leverage, costs.quote(side, snap), pf)
        assert decision.approved, (case, decision.reasons, proposal)
        assert decision.worst_case_loss == proposal.planned_loss
        assert decision.risk_pct <= D("0.005") and decision.total_risk_pct_after <= D("0.02")
        assert decision.gross_exposure_pct_after <= D(1) and decision.stress_loss_pct_after <= D("0.05")
        assert proposal.risk_pct <= D("0.004")
        assert proposal.quantity % D("0.0001") == 0 and proposal.quantity > 0
    assert approved > 300 and blocked > 100  # der Test ist nicht trivial


def test_concurrent_sizing_requests_are_deterministic_and_thread_safe():
    sora = make_sora()
    ctx = MarketContext(snapshot())
    pf = portfolio()
    out, errors = [], []

    def work():
        try:
            out.append(sora.size(make_setup(), ctx, pf))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(64)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and len(out) == 64 and len(set(out)) == 1


# ------------------------------------------------------------------ Stufe 1: Exposure, Stress, unsichere Bewertung


def test_stress_limit_caps_positions_with_tight_stops():
    """Enger Stop (0,4 %) erlaubt nach dem Risiko allein 77 Stueck - der 10-%-Schock begrenzt auf rund 50 %."""
    p = size(make_setup(stop=D("99.6"), tp=D(104)))
    stress_unit = D("10.054")  # 10 Kursverlust + 0,06 % x 90
    assert p.approve and p.quantity == floor4(D(500) / (stress_unit + D("0.05") * D("0.06")))  # 49,7166
    assert p.risk_pct < D("0.003")  # das Risiko-Budget wird NICHT ausgeschoepft
    d = RM.evaluate(make_setup(stop=D("99.6"), tp=D(104)), p.quantity, p.leverage,
                    NO_SLIP.quote(Side.LONG, snapshot()), portfolio())
    assert d.approved and D("0.0499") < d.stress_loss_pct_after <= D("0.05")


def test_existing_stress_and_exposure_shrink_the_new_position():
    stressed = size(pf=portfolio(stress_loss=D(300)))  # Rest 200
    assert stressed.quantity == floor4(D(200) / (D("10.054") + D("0.05") * D("0.06")))  # 19,8866
    exposed = size(pf=portfolio(gross_exposure=D(8000)))  # Rest 2 000 von 10 000
    assert exposed.quantity == floor4(D(2000) / (D(100) + D("0.06")))  # 19,9880
    assert stressed.planned_loss < D(40) and exposed.planned_loss < D(40)  # kleiner, nie groesser


def test_exhausted_exposure_or_stress_budget_blocks_with_the_right_reason():
    full_exposure = size(pf=portfolio(gross_exposure=D(10000)))
    assert not full_exposure.approve and R.GROSS_EXPOSURE_TOO_HIGH in full_exposure.reject_reasons
    full_stress = size(pf=portfolio(stress_loss=D(500)))
    assert not full_stress.approve and R.STRESS_LOSS_TOO_HIGH in full_stress.reject_reasons
    assert full_stress.quantity == 0 and full_exposure.reasons  # mit Klartext


def test_uncertain_valuation_blocks_sizing():
    p = size(pf=portfolio(valuation_complete=False))
    assert not p.approve and p.reject_reasons == (R.EQUITY_UNCERTAIN,)


def test_leverage_does_not_help_against_exposure_or_stress():
    spot = size(make_setup(stop=D("99.6"), tp=D(104)))
    fut = size(fut_setup(stop=D("99.6"), tp=D(104)), dict(FUT, funding=D(0)))
    assert abs(fut.quantity - spot.quantity) < D("0.01")  # gleicher Positionswert trotz moeglichem Hebel


def test_liquidation_safety_factor_of_sora_is_stricter_than_the_limit():
    """Stop 14 % entfernt: Bei 5x laege die Liquidation 19,5 % entfernt - genug fuer das Limit (1,25x),
    aber nicht fuer SORAs eigenen Sicherheitsfaktor (1,5x). Also hoechstens 4x."""
    s = make_sora(config=SoraConfig(max_leverage=D(5)))
    p = size(fut_setup(stop=D(86), tp=D(140)), FUT, pf=portfolio(free_margin=D(50)), sora=s)
    assert p.approve and p.leverage == D(4)


def test_spread_costs_shrink_the_position_and_are_checked_against_equity_after_costs():
    p = size(snap_kw={"half_spread": D("0.1")})  # Kauf zu 100,1 bei Mitte 100
    unit = NO_SLIP.worst_case_loss(Side.LONG, D(1), D("100.1"), D(99))
    assert p.quantity == floor4(D(40) / unit) and p.quantity < size().quantity


def test_sora_never_approves_what_the_shared_risk_rules_reject(monkeypatch):
    """Weicht die Endkontrolle von SORAs eigener Mengenformel ab (hier kuenstlich: strengeres Limit je Trade),
    gibt es KEINEN Vorschlag - SORA rundet hoechstens ein paar Schritte nach unten und gibt dann auf."""
    import trading.agents.sora_rules as module

    real = module.evaluate_order
    stricter = dataclasses.replace(POLICY, max_risk_per_trade=D("0.0039"))  # SORA zielt auf 0,4 %
    monkeypatch.setattr(module, "evaluate_order", lambda limits, *args: real(stricter, *args))
    p = size()
    assert not p.approve and p.quantity == 0
    assert R.TRADE_RISK_TOO_HIGH in p.reject_reasons
