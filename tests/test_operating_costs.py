"""Betriebskosten, KI-Kostenauswertung und Nettoergebnis nach ALLEN Kosten (AKI, D-016); Wirtschaftlichkeitsvergleich."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from helpers import T0, inst

from trading.ai.activation import TriggerEvent
from trading.ai.orchestrator import AICallRecord, Source, Tier
from trading.ai.pricing import TokenUsage
from trading.audit.ai_value import DecisionOutcome, Verdict, compare_methods
from trading.audit.costs import (
    CostCategory,
    CostLedger,
    OperatingCost,
    attribute_ai_costs,
    build_net_profit_report,
    summarize_ai_costs,
)
from trading.domain.models import ClosedTrade, ExitReason, Side

D = Decimal
DAY = timedelta(days=1)


def rec(aid, agent="KIBO", cost="0", source=Source.AI, setup=None, model="claude-haiku-5-5", t=T0, reason="KI",
        tokens=(100, 20)):
    return AICallRecord(aid, t, agent, "k", source, reason, Tier.CHEAP if source is Source.AI else None, "anthropic",
                        model if source is Source.AI else "", TokenUsage(*tokens) if source is Source.AI else TokenUsage(),
                        D(cost), D(cost), setup, "BTC", TriggerEvent.SETUP_CANDIDATE)


def trade(setup_id, net, fees="1", funding="0", closed=T0 + timedelta(hours=1)):
    gross = D(net) + D(fees) + D(funding)
    return ClosedTrade("P-" + setup_id, setup_id, "test", inst(), Side.LONG, D(1), D(100), D(101), T0, closed,
                       ExitReason.TAKE_PROFIT, gross, D(fees), D(funding), D(net), D(1))


# ------------------------------------------------------------------ Betriebskosten


def test_period_costs_are_prorated_and_one_off_costs_counted_once():
    server = OperatingCost(CostCategory.SERVER, D("30"), T0, T0 + 30 * DAY, "VPS Oktober")
    assert server.share(T0, T0 + DAY) == D("1")
    assert server.share(T0 + 29 * DAY, T0 + 40 * DAY) == D("1")
    assert server.share(T0 - DAY, T0) == 0
    third = OperatingCost(CostCategory.SERVER, D("10"), T0, T0 + 3 * DAY, "x")
    assert third.share(T0, T0 + DAY) == D("3.33333334")  # aufgerundet
    once = OperatingCost(CostCategory.MARKET_DATA, D("5"), T0 + DAY, T0 + DAY, "Datensatz")
    assert once.share(T0, T0 + DAY) == 0 and once.share(T0 + DAY, T0 + 2 * DAY) == D("5")


@pytest.mark.parametrize("bad", [dict(amount=D(-1)), dict(amount=D("NaN")), dict(end=T0 - DAY),
                                 dict(start=T0.replace(tzinfo=None))])
def test_invalid_costs_are_rejected(bad):
    base = dict(category=CostCategory.OTHER, amount=D(1), start=T0, end=T0 + DAY, description="x")
    base.update(bad)
    with pytest.raises(ValueError):
        OperatingCost(**base)


# ------------------------------------------------------------------ KI-Kosten


def test_ai_cost_summary_per_agent_model_day_analysis_and_signal():
    records = [
        rec("A1", cost="0.002", setup="s1"), rec("A1", cost="0.010", setup="s1", model="claude-sonnet-5-5"),
        rec("A2", agent="YUZU", cost="0.003", setup="s2", t=T0 + DAY),
        rec("A3", agent="YUZU", cost="0.001", t=T0 + DAY),  # Marktphase, ohne Setup
        rec("A4", source=Source.CACHE, reason="CACHE_TREFFER", setup="s1"),
        rec("A5", source=Source.RULES, reason="REGELN_SICHER", setup="s3"),
        rec("A6", source=Source.SKIPPED, reason="BUDGET", setup="s4"),
    ]
    s = summarize_ai_costs(records)
    assert s.total_cost == D("0.016")
    assert s.paid_calls_by_agent == {"KIBO": 2, "YUZU": 2}
    assert s.analyses == 6 and s.paid_analyses == 3 and s.analyses_by_agent == {"KIBO": 4, "YUZU": 2}
    assert s.input_tokens_by_agent["KIBO"] == 200 and s.output_tokens_by_agent["YUZU"] == 40
    assert s.cost_by_model == {"claude-haiku-5-5": D("0.006"), "claude-sonnet-5-5": D("0.010")}
    assert s.cost_by_day == {T0.date(): D("0.012"), (T0 + DAY).date(): D("0.004")}
    assert s.cost_by_month == {"2025-01": D("0.016")}
    assert s.cost_per_analysis == D("0.00266667") and s.cost_per_paid_analysis == D("0.00533334")
    assert s.signals == 4 and s.cost_per_signal == D("0.00375")  # 0,015 auf s1..s4
    assert s.cost_without_signal == D("0.001")
    assert s.cache_hits == 1 and s.rule_decisions == 1 and s.skipped_by_reason == {"BUDGET": 1}


def test_several_analyses_per_trade_and_analyses_without_trade_are_both_counted():
    records = [rec("A1", cost="0.01", setup="s1"), rec("A2", cost="0.02", setup="s1"),  # zwei Analysen, ein Trade
               rec("A3", cost="0.03", setup="s2"),  # Setup abgelehnt -> kein Trade
               rec("A4", cost="0.04")]  # keine Zuordnung
    a = attribute_ai_costs(records, [trade("s1", "5")])
    assert a.trades == 1 and a.ai_cost_of_traded_setups == D("0.03")
    assert a.ai_cost_without_trade_in_period == D("0.07")
    assert a.direct_cost_per_trade == D("0.03") and a.fully_loaded_cost_per_trade == D("0.10")
    assert attribute_ai_costs(records, []).fully_loaded_cost_per_trade is None


def test_net_profit_after_all_costs():
    ledger = CostLedger()
    ledger.add(OperatingCost(CostCategory.SERVER, D("30"), T0, T0 + 30 * DAY, "VPS"))
    ledger.add(OperatingCost(CostCategory.MARKET_DATA, D("2"), T0 + DAY, T0 + DAY, "Kerzen"))
    ledger.add(OperatingCost(CostCategory.AI_API, D("0.5"), T0, T0 + 10 * DAY, "Grundgebuehr"))
    ledger.add(OperatingCost(CostCategory.OTHER, D("1"), T0 + 20 * DAY, T0 + 20 * DAY, "ausserhalb"))
    trades = [trade("s1", "10", fees="0.5", funding="0.2"), trade("s2", "-4", fees="0.5"),
              trade("s3", "100", closed=T0 + 5 * DAY)]  # ausserhalb des Zeitraums
    records = [rec("A1", cost="0.25", setup="s1"), rec("A2", cost="0.75", setup="x"),
               rec("A3", cost="9", t=T0 + 5 * DAY)]  # ausserhalb
    r = build_net_profit_report(trades, records, ledger, T0, T0 + 2 * DAY)
    assert r.trades == 2 and r.trading_net_pnl == D("6") and r.trading_fees == D("1") and r.funding == D("0.2")
    assert r.gross_pnl == D("7.2")
    assert r.ai_cost == D("1.1")  # 1,00 gemessen + 0,10 Grundgebuehr anteilig
    assert r.server_cost == D("2") and r.market_data_cost == D("2") and r.other_cost == 0
    assert r.operating_cost == D("5.1") and r.net_after_all_costs == D("0.9")
    assert r.attribution.fully_loaded_cost_per_trade == D("0.5")
    with pytest.raises(ValueError):
        build_net_profit_report(trades, records, ledger, T0, T0)


# ------------------------------------------------------------------ Lohnt sich die teurere Analyse?


def outcomes(pnls, cost):
    return {"s%03d" % i: DecisionOutcome(D(p), D(cost)) for i, p in enumerate(pnls)}


def test_expensive_method_wins_only_with_a_clear_net_advantage():
    pnls = [((i * 7) % 11) - 5 for i in range(80)]
    cheap = outcomes(pnls, "0")
    better = outcomes([p + 2 for p in pnls], "0.5")  # +2 je Setup, kostet 0,5
    res = compare_methods(cheap, better)
    assert res.verdict is Verdict.EXPENSIVE_PAYS_OFF and res.mean_difference == D("1.5") and res.ci_low > 0
    assert res.extra_analysis_cost == D("40")


def test_cheaper_method_is_preferred_when_results_are_comparable_or_costs_eat_the_gain():
    pnls = [((i * 7) % 11) - 5 for i in range(80)]
    cheap = outcomes(pnls, "0")
    assert compare_methods(cheap, outcomes(pnls, "0")).verdict is Verdict.PREFER_CHEAPER  # gleich gut
    eaten = outcomes([p + 1 for p in pnls], "1.2")  # besser, aber teurer als der Vorteil
    res = compare_methods(cheap, eaten)
    assert res.verdict is Verdict.PREFER_CHEAPER and res.mean_difference == D("-0.2")
    noisy = outcomes([p + (8 if i % 2 else -7) for i, p in enumerate(pnls)], "0")  # im Mittel +0,5, sehr unsicher
    assert compare_methods(cheap, noisy).verdict is Verdict.PREFER_CHEAPER


def test_too_little_data_never_justifies_the_expensive_method():
    res = compare_methods(outcomes([1, 2, 3], "0"), outcomes([10, 20, 30], "0"))
    assert res.verdict is Verdict.TOO_LITTLE_DATA


def test_comparison_needs_the_same_setups_and_is_reproducible():
    with pytest.raises(ValueError):
        compare_methods(outcomes([1, 2], "0"), outcomes([1], "0"))
    a, b = outcomes(range(50), "0"), outcomes([x + (x % 3) for x in range(50)], "0.1")
    assert compare_methods(a, b) == compare_methods(a, b)
