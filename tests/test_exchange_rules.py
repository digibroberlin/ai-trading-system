"""Boersenregeln je Instrument (AP-13): Schritte, Mindestgroessen, Hoechsthebel, Gebuehrenstufen - in SORA, im
Risikomanager und in den Strategie-Vorschlaegen."""

from __future__ import annotations

from decimal import Decimal

import pytest
from bt_helpers import BTC, ETHF, config, synthetic
from helpers import NO_SLIP, inst, make_setup, portfolio, quote, snapshot

from trading.agents import MarketContext, RuleBasedSora, SizingPolicy
from trading.audit.events import EventType
from trading.backtest import BacktestEngine
from trading.backtest.strategy import EntryProposal, on_ticks
from trading.domain.exchange_rules import SYNTHETIC_RULES, ExchangeRules, InstrumentRules
from trading.domain.instrument import Exchange
from trading.domain.models import MarketType, Side
from trading.domain.reasons import RejectReason as R
from trading.execution.costs import CostModel
from trading.execution.fees import FeeSchedule, FeeTier
from trading.safety.limits import DEFAULT_LIMITS
from trading.safety.risk_manager import RiskManager
from trading.strategies import build_strategy

D = Decimal
COARSE = InstrumentRules(price_tick=D("0.5"), quantity_step=D("0.01"), min_quantity=D("0.05"), min_notional=D(20),
                         max_leverage=D(2))
RULES = ExchangeRules(defaults={(Exchange.SYNTHETIC, MarketType.SPOT): COARSE,
                                (Exchange.SYNTHETIC, MarketType.FUTURES): COARSE})


# ------------------------------------------------------------------ Regeln selbst


def test_quantities_and_prices_are_rounded_against_us():
    assert COARSE.floor_quantity(D("1.239")) == D("1.23") and COARSE.ceil_quantity(D("1.231")) == D("1.24")
    assert COARSE.minimum_quantity(D(100)) == D("0.2")  # Mindestwert 20 bei Kurs 100
    assert COARSE.minimum_quantity(D(10000)) == D("0.05")  # dann gilt die Mindestmenge
    assert COARSE.on_quantity_step(D("0.07")) and not COARSE.on_quantity_step(D("0.075"))
    assert COARSE.protective_stop(Side.LONG, D("98.7")) == D("98.5")  # weiter weg
    assert COARSE.protective_stop(Side.SHORT, D("101.2")) == D("101.5")
    assert COARSE.conservative_target(Side.LONG, D("104.9")) == D("104.5")  # naeher dran
    assert COARSE.conservative_target(Side.SHORT, D("95.2")) == D("95.5")
    assert COARSE.on_price_tick(D("98.5")) and not COARSE.on_price_tick(D("98.7"))


@pytest.mark.parametrize("bad", [dict(price_tick=D(0)), dict(quantity_step=D(-1)), dict(min_notional=D(-1)),
                                 dict(min_quantity=D("0.015")), dict(max_leverage=D("NaN"))])
def test_invalid_rules_are_rejected(bad):
    base = dict(price_tick=D("0.5"), quantity_step=D("0.01"), min_quantity=D("0.05"), min_notional=D(20),
                max_leverage=D(2))
    base.update(bad)
    with pytest.raises(ValueError):
        InstrumentRules(**base)


def test_explicit_rules_win_over_defaults_and_unknown_instruments_get_none():
    special = InstrumentRules(D("0.1"), D("0.001"), D("0.001"), D(5), D(3))
    book = ExchangeRules({BTC: special}, {(Exchange.SYNTHETIC, MarketType.SPOT): COARSE})
    assert book.for_instrument(BTC) is special
    assert book.for_instrument(inst("SOL-USD")) is COARSE
    assert book.for_instrument(inst("BTC-USD", exchange=Exchange.BITGET)) is None


def test_fee_tiers_by_volume():
    schedule = FeeSchedule((FeeTier(D(0), D("0.001"), D("0.0015")), FeeTier(D(100000), D("0.0008"), D("0.001")),
                            FeeTier(D(1000000), D("0.0005"), D("0.0008"))))
    assert schedule.tier_for(D(0)).taker_fee == D("0.0015") and schedule.tier_for(D(99999)).taker_fee == D("0.0015")
    assert schedule.tier_for(D(100000)).taker_fee == D("0.001") and schedule.tier_for(D(5000000)).maker_fee == D("0.0005")
    costs = schedule.costs_for(D(200000), NO_SLIP)
    assert (costs.maker_fee, costs.taker_fee, costs.slippage_bps) == (D("0.0008"), D("0.001"), NO_SLIP.slippage_bps)
    for tiers in ((), (FeeTier(D(1), D(0), D(0)),), (FeeTier(D(0), D("0.001"), D("0.001")), FeeTier(D(0), D(0), D(0))),
                  (FeeTier(D(0), D("0.001"), D("0.001")), FeeTier(D(10), D("0.002"), D("0.001"))),
                  (FeeTier(D(0), D("-0.0001"), D("0.001")),)):
        with pytest.raises(ValueError):
            FeeSchedule(tiers)
    with pytest.raises(ValueError):
        schedule.tier_for(D(-1))


# ------------------------------------------------------------------ Risikomanager


def evaluate(qty, setup=None, lev=D(1), manager=RiskManager(DEFAULT_LIMITS, NO_SLIP, RULES), funding=None):
    return manager.evaluate(setup or make_setup(stop=D("98.5"), tp=D("104.5")), qty, lev, quote(D(100), None, funding),
                            portfolio())


def test_risk_manager_rejects_what_the_exchange_would_reject():
    assert evaluate(D("0.5")).approved
    assert R.QUANTITY_NOT_ON_STEP in evaluate(D("0.505")).reasons
    assert R.NOTIONAL_TOO_SMALL in evaluate(D("0.1")).reasons  # 10 < Mindestwert 20
    assert R.QUANTITY_TOO_SMALL in evaluate(D("0.04")).reasons
    off_tick = make_setup(stop=D("98.7"), tp=D("104.5"))
    assert R.PRICE_NOT_ON_TICK in evaluate(D("0.5"), off_tick).reasons
    fut = make_setup(market_type=MarketType.FUTURES, stop=D("98.5"), tp=D("104.5"))
    assert evaluate(D("0.5"), fut, D(2), funding=D("0.0001")).approved
    assert R.LEVERAGE_TOO_HIGH in evaluate(D("0.5"), fut, D(3), funding=D("0.0001")).reasons  # Boerse: max 2


def test_missing_rules_are_never_guessed():
    manager = RiskManager(DEFAULT_LIMITS, NO_SLIP, ExchangeRules())
    assert evaluate(D("0.5"), manager=manager).reasons == (R.EXCHANGE_RULES_MISSING,)
    assert RiskManager(DEFAULT_LIMITS, NO_SLIP).evaluate(make_setup(), D("0.5"), D(1), quote(D(100)),
                                                        portfolio()).approved  # ohne Regelwerk: wie bisher


# ------------------------------------------------------------------ SORA


def sora(rules=RULES):
    return RuleBasedSora(SizingPolicy.from_limits(DEFAULT_LIMITS), NO_SLIP, rules=rules)


def test_sora_sizes_on_the_exchange_step_and_respects_its_leverage():
    p = sora().size(make_setup(stop=D("98.5"), tp=D("104.5")), MarketContext(snapshot()), portfolio())
    assert p.approve and p.quantity % D("0.01") == 0 and p.quantity >= D("0.2")
    fut = make_setup(market_type=MarketType.FUTURES, stop=D("99.5"), tp=D(102))
    p = sora().size(fut, MarketContext(snapshot(market_type=MarketType.FUTURES, funding=D("0.0001"))), portfolio())
    assert p.approve and p.leverage <= D(2) and p.quantity % D("0.01") == 0
    decision = RiskManager(DEFAULT_LIMITS, NO_SLIP, RULES).evaluate(
        fut, p.quantity, p.leverage, NO_SLIP.quote(Side.LONG, snapshot(market_type=MarketType.FUTURES,
                                                                         funding=D("0.0001"))), portfolio())
    assert decision.approved  # was SORA vorschlaegt, besteht auch beim Risikomanager
    tight = sora().size(fut, MarketContext(snapshot(market_type=MarketType.FUTURES, funding=D("0.0001"))),
                        portfolio(free_margin=D(500)))  # knappe Margin: SORA wuerde gern mehr Hebel nehmen
    assert tight.approve and tight.leverage == D(2)  # ... aber nie mehr, als die Boerse erlaubt


def test_sora_blocks_unknown_instruments_off_tick_prices_and_too_small_orders():
    p = sora(ExchangeRules()).size(make_setup(), MarketContext(snapshot()), portfolio())
    assert not p.approve and p.reject_reasons == (R.EXCHANGE_RULES_MISSING,)
    p = sora().size(make_setup(stop=D("98.7"), tp=D("104.5")), MarketContext(snapshot()), portfolio())
    assert not p.approve and R.PRICE_NOT_ON_TICK in p.reject_reasons
    huge = ExchangeRules(defaults={(Exchange.SYNTHETIC, MarketType.SPOT): InstrumentRules(
        D("0.5"), D("0.01"), D("0.05"), D(1000000), D(1))})
    p = sora(huge).size(make_setup(stop=D("98.5"), tp=D("104.5")), MarketContext(snapshot()), portfolio())
    assert not p.approve and R.QUANTITY_TOO_SMALL in p.reject_reasons


# ------------------------------------------------------------------ Vorschlaege und Backtest


def test_proposals_are_moved_onto_the_price_tick_against_us():
    p = EntryProposal(BTC, Side.LONG, D("98.7"), D("104.9"))
    assert on_ticks(RULES, p) == (D("98.5"), D("104.5"))
    assert on_ticks(None, p) == (D("98.7"), D("104.9")) and on_ticks(ExchangeRules(), p) == (D("98.7"), D("104.9"))


def test_backtest_with_exchange_rules_only_makes_compliant_orders():
    from trading.audit.event_log import InMemoryEventLog

    logs = []

    def factory(clock):
        logs.append(InMemoryEventLog(clock))
        return logs[-1]

    r = BacktestEngine(synthetic(days=30, seed=4), build_strategy("breakout"), config(rules=SYNTHETIC_RULES),
                       log_factory=factory).run()
    assert r.setups_executed > 0
    for e in logs[0].of_type(EventType.ORDER_FILLED):
        pos = e.payload["position"]
        assert D(pos["quantity"]) % D("0.0001") == 0
        assert D(pos["stop_loss"]) % D("0.01") == 0 and D(pos["take_profit"]) % D("0.01") == 0
    none = BacktestEngine(synthetic(days=10, seed=4), build_strategy("breakout"), config(rules=ExchangeRules())).run()
    assert none.setups_executed == 0 and set(none.blocked_by_reason) == {R.EXCHANGE_RULES_MISSING.value}
    assert ETHF in synthetic(days=1).instruments  # beide Marktarten der Test-Boerse haben Regeln
