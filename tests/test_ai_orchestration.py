"""KI-Orchestrierung mit Kostenkontrolle (D-016): Preise, harte Budgets, Aktivierung, Modellstufen, Cache, Messung.

Ausschliesslich mit einem Attrappen-Anbieter - es gibt keinen einzigen echten (bezahlten) KI-Aufruf.
"""

from __future__ import annotations

import ast
import json
import threading
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from helpers import T0

from trading.ai.activation import DEFAULT_ACTIVATION, ActivationPolicy, ActivationRules, AgentActivation, TriggerEvent
from trading.ai.budget import NO_BUDGET, AIBudget, BudgetError, InMemoryBudgetLedger
from trading.ai.orchestrator import (
    DEFAULT_ROUTING,
    MAX_PROMPT_BYTES,
    AIOrchestrator,
    AnalysisRequest,
    RoutingPolicy,
    RuleAnswer,
    Source,
    Tier,
    canonical_payload,
    parse_answer,
)
from trading.ai.pricing import DEFAULT_PRICES, REQUEST_OVERHEAD_TOKENS, ModelPrice, PricingError, TokenUsage
from trading.ai.provider import AIProvider, AIRequest, AIResponse, ProviderRegistry
from trading.clock import FixedClock

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
HAIKU = DEFAULT_PRICES.get("anthropic", "claude-haiku-5-5")
OPUS = DEFAULT_PRICES.get("anthropic", "claude-opus-5-5")
BUDGET = AIBudget(per_call_usd=D("0.05"), daily_usd=D("1"), monthly_usd=D("10"))


# ------------------------------------------------------------------ Preise


def test_cost_uses_all_four_token_prices_and_rounds_up():
    usage = TokenUsage(input_tokens=1000, output_tokens=200, cache_write_tokens=100, cache_read_tokens=5000)
    # Opus 5.5: 4 / 20 / 5 (1,25 x 4) / 0,20 USD je Million
    assert OPUS.cost(usage) == D("0.0095")  # 0,004 + 0,004 + 0,0005 + 0,001
    assert HAIKU.cost(TokenUsage(input_tokens=1)) == D("0.00000010")
    assert HAIKU.cost(TokenUsage(output_tokens=1, input_tokens=1)) == D("0.00000060")
    tiny = ModelPrice("x", "y", D("0.000000001"), D(0), D(0), D(0))
    assert tiny.cost(TokenUsage(input_tokens=1)) == D("0.00000001")  # aufgerundet, nie auf 0 abgerundet


def test_unknown_model_or_bad_values_have_no_price():
    with pytest.raises(PricingError):
        DEFAULT_PRICES.get("anthropic", "claude-unbekannt")
    with pytest.raises(PricingError):
        ModelPrice("a", "b", D("NaN"), D(1), D(1), D(1))
    with pytest.raises(PricingError):
        TokenUsage(input_tokens=-1)
    with pytest.raises(PricingError):
        TokenUsage(output_tokens=True)  # type: ignore[arg-type]


def test_price_table_is_dated_and_tiers_get_more_expensive():
    assert DEFAULT_PRICES.as_of == date(2026, 10, 6) and "Anthropic" in DEFAULT_PRICES.source
    sonnet = DEFAULT_PRICES.get("anthropic", "claude-sonnet-5-5")
    assert HAIKU.input_per_mtok < sonnet.input_per_mtok < OPUS.input_per_mtok
    assert HAIKU.output_per_mtok < sonnet.output_per_mtok < OPUS.output_per_mtok


@pytest.mark.parametrize("price", [HAIKU, OPUS])
def test_max_cost_is_a_true_upper_bound(price):
    """Solange Eingabe-Tokens <= Bytes + Zuschlag und Ausgabe <= max_tokens, liegen die Kosten nie darueber."""
    prompt_bytes, max_out = 3000, 500
    ceiling = price.max_cost(prompt_bytes, max_out)
    worst = TokenUsage(cache_write_tokens=prompt_bytes + REQUEST_OVERHEAD_TOKENS, output_tokens=max_out)
    for usage in (worst, TokenUsage(input_tokens=prompt_bytes + REQUEST_OVERHEAD_TOKENS, output_tokens=max_out),
                  TokenUsage(input_tokens=10, output_tokens=1)):
        assert price.cost(usage) <= ceiling


# ------------------------------------------------------------------ Budget


def ledger(budget=BUDGET, clock=None):
    clock = clock or FixedClock(T0)
    return InMemoryBudgetLedger(budget, clock), clock


def test_without_approved_budget_nothing_can_be_spent():
    led, _ = ledger(NO_BUDGET)
    assert not NO_BUDGET.allows_spending
    with pytest.raises(BudgetError):
        led.reserve(D("0.00000001"), "KIBO", "m")


def test_per_call_daily_and_monthly_limits_are_hard():
    led, clock = ledger(AIBudget(D("0.5"), D("1"), D("1.5")))
    with pytest.raises(BudgetError, match="Einzelbudget"):
        led.reserve(D("0.51"), "KIBO", "m")
    a = led.reserve(D("0.5"), "KIBO", "m")
    led.reserve(D("0.5"), "KIBO", "m")
    with pytest.raises(BudgetError, match="Tagesbudget"):
        led.reserve(D("0.00000001"), "KIBO", "m")
    led.settle(a, D("0.1"))  # tatsaechlich billiger -> wieder Platz
    led.reserve(D("0.4"), "KIBO", "m")
    clock.set(T0 + timedelta(days=1))
    led.reserve(D("0.4"), "KIBO", "m")
    with pytest.raises(BudgetError, match="Monatsbudget"):
        led.reserve(D("0.2"), "KIBO", "m")  # Monat: 0,1 + 0,5 + 0,4 + 0,4 = 1,4
    clock.set(T0.replace(month=2, day=1))
    led.reserve(D("0.5"), "KIBO", "m")  # neuer Monat


def test_failed_call_counts_with_the_full_reservation():
    led, _ = ledger()
    r = led.reserve(D("0.05"), "KIBO", "m")
    led.settle(r, None)
    assert led.status().spent_today == D("0.05")
    with pytest.raises(BudgetError):
        led.settle(r, D("0.01"))  # genau einmal abrechnen


def test_actual_cost_above_reservation_blocks_all_further_calls():
    led, _ = ledger()
    r = led.reserve(D("0.01"), "KIBO", "m")
    led.settle(r, D("0.02"))
    assert led.status().blocked
    with pytest.raises(BudgetError, match="gesperrt"):
        led.reserve(D("0.00000001"), "KIBO", "m")


def test_concurrent_reservations_never_exceed_the_daily_budget():
    led, _ = ledger(AIBudget(D("0.01"), D("0.5"), D("5")))
    ok = []

    def worker():
        for _ in range(40):
            try:
                led.reserve(D("0.01"), "KIBO", "m")
                ok.append(1)
            except BudgetError:
                pass

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 50 and led.status().spent_today == D("0.5")


@pytest.mark.parametrize("bad", [dict(per_call_usd=D("-1")), dict(daily_usd=D("NaN")),
                                 dict(per_call_usd=D(1), daily_usd=D(5), monthly_usd=D(1))])
def test_invalid_budgets_are_rejected(bad):
    with pytest.raises(BudgetError):
        AIBudget(**bad)


# ------------------------------------------------------------------ ereignisbasierte Aktivierung


def test_agents_run_only_on_their_events_and_after_the_cooldown():
    clock = FixedClock(T0)
    act = ActivationPolicy(DEFAULT_ACTIVATION, clock)
    assert not act.should_run("RIN", TriggerEvent.SETUP_CANDIDATE, "BTC")  # Regelagent: nie KI
    assert not act.should_run("KIBO", TriggerEvent.SCHEDULED_REVIEW, "BTC")  # nicht zustaendig
    assert act.should_run("KIBO", TriggerEvent.SETUP_CANDIDATE, "BTC")
    assert not act.should_run("KIBO", TriggerEvent.SETUP_CANDIDATE, "BTC")  # Abkuehlzeit
    assert act.should_run("KIBO", TriggerEvent.SETUP_CANDIDATE, "ETH")  # anderer Gegenstand
    clock.advance(899)
    assert not act.should_run("KIBO", TriggerEvent.SETUP_CANDIDATE, "BTC")
    clock.advance(1)
    assert act.should_run("KIBO", TriggerEvent.SETUP_CANDIDATE, "BTC")
    clock.set(T0)  # Uhr springt zurueck: im Zweifel kein Aufruf
    assert not act.should_run("KIBO", TriggerEvent.SETUP_CANDIDATE, "BTC")


# ------------------------------------------------------------------ Orchestrator


class FakeProvider(AIProvider):
    """Attrappe: antwortet je Modell mit vorgegebener Sicherheit und meldet einen plausiblen Verbrauch."""

    name = "anthropic"

    def __init__(self, confidence=None, text=None, fail=False, input_tokens=None):
        self.calls = []
        self.confidence = confidence or {"claude-haiku-5-5": 0.9}
        self.text = text
        self.fail = fail
        self.input_tokens = input_tokens

    def complete(self, request: AIRequest) -> AIResponse:
        self.calls.append(request)
        if self.fail:
            raise TimeoutError("Anbieter antwortet nicht")
        text = self.text if self.text is not None else json.dumps(
            {"approve": True, "confidence": self.confidence.get(request.model, 0.5)})
        tokens = self.input_tokens if self.input_tokens is not None else len((request.system + request.prompt)) // 4
        return AIResponse(text, self.name, request.model, TokenUsage(input_tokens=tokens, output_tokens=40))


class Env:
    def __init__(self, provider=None, budget=BUDGET, routing=DEFAULT_ROUTING, activation=DEFAULT_ACTIVATION):
        self.clock = FixedClock(T0)
        self.provider = provider or FakeProvider()
        registry = ProviderRegistry()
        registry.register(self.provider)
        self.ledger = InMemoryBudgetLedger(budget, self.clock)
        self.records = []
        self.orch = AIOrchestrator(registry, DEFAULT_PRICES, self.ledger, ActivationPolicy(activation, self.clock),
                                   self.clock, self.records.append, routing)


def request(**kw):
    base = dict(agent="KIBO", kind="kibo.setup_review", instruction="Bewerte das Setup. Antworte als JSON.",
                payload={"setup": "s1", "rsi": D("61.5"), "trend": "up"}, expected_value_usd=D(50),
                trigger=TriggerEvent.SETUP_CANDIDATE, subject="BTC", setup_id="s1")
    base.update(kw)
    return AnalysisRequest(**base)


def test_confident_rules_need_no_ai_at_all():
    env = Env()
    a = env.orch.analyze(request(), RuleAnswer({"approve": True}, D("0.95")))
    assert a.source is Source.RULES and a.cost == 0 and env.provider.calls == []
    assert [r.source for r in env.records] == [Source.RULES]


def test_cheapest_model_first_and_everything_is_metered():
    env = Env()
    a = env.orch.analyze(request())
    assert a.source is Source.AI and a.tier is Tier.CHEAP and a.model == "claude-haiku-5-5"
    assert [c.model for c in env.provider.calls] == ["claude-haiku-5-5"]
    call = env.provider.calls[0]
    assert call.system.startswith("Bewerte") and json.loads(call.prompt) == {"rsi": "61.5", "setup": "s1", "trend": "up"}
    rec = env.records[-1]
    assert rec.agent == "KIBO" and rec.setup_id == "s1" and rec.usage.output_tokens == 40
    assert rec.cost == HAIKU.cost(rec.usage) and rec.cost > 0 and a.cost == rec.cost
    assert env.ledger.status().spent_today == rec.cost


def test_escalation_only_when_unsure_and_never_beyond_the_allowed_tier():
    unsure = FakeProvider({"claude-haiku-5-5": 0.3, "claude-sonnet-5-5": 0.8})
    env = Env(unsure)
    a = env.orch.analyze(request(max_tier=Tier.PREMIUM))
    assert [c.model for c in unsure.calls] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert a.tier is Tier.STANDARD and a.confidence == D("0.8")
    capped = FakeProvider({"claude-haiku-5-5": 0.3})
    env = Env(capped)
    a = env.orch.analyze(request(max_tier=Tier.CHEAP))
    assert [c.model for c in capped.calls] == ["claude-haiku-5-5"] and a.confidence == D("0.3")


def test_expensive_tier_needs_enough_expected_value():
    unsure = FakeProvider({"claude-haiku-5-5": 0.3})
    env = Env(unsure, budget=AIBudget(D(1), D(10), D(100)))
    # Haiku-Obergrenze ~0,0003 USD passt unter 10 % von 0,01 USD? Nein -> gar kein Aufruf.
    a = env.orch.analyze(request(expected_value_usd=D("0.001"), max_tier=Tier.PREMIUM))
    assert unsure.calls == [] and a.source is Source.SKIPPED and a.reason == "NUTZEN_ZU_GERING"
    # Genug Nutzen fuer Haiku, aber nicht fuer Sonnet: keine Eskalation trotz Unsicherheit.
    a = env.orch.analyze(request(payload={"x": 1}, expected_value_usd=D("0.01"), max_tier=Tier.PREMIUM))
    assert [c.model for c in unsure.calls] == ["claude-haiku-5-5"]
    assert any(r.reason == "NUTZEN_ZU_GERING" and r.tier is Tier.STANDARD for r in env.records)


def test_identical_analysis_is_reused_from_cache_even_for_another_agent():
    env = Env()
    first = env.orch.analyze(request())
    second = env.orch.analyze(request(agent="YUZU"))
    assert len(env.provider.calls) == 1
    assert second.source is Source.CACHE and second.cost == 0 and second.answer == first.answer
    env.clock.advance(DEFAULT_ROUTING.cache_ttl_s + 1)
    env.orch.analyze(request(subject="BTC-2"))
    assert len(env.provider.calls) == 2  # Haltezeit abgelaufen


def test_not_activated_means_no_call_and_the_rules_decide():
    env = Env()
    rules = RuleAnswer({"approve": False}, D("0.5"))
    a = env.orch.analyze(request(trigger=TriggerEvent.SCHEDULED_REVIEW), rules)
    assert env.provider.calls == [] and a.source is Source.RULES and a.answer == {"approve": False}
    assert a.reason == "REGELN_ERSATZ:NICHT_AKTIVIERT"


def test_without_budget_nothing_is_called():
    env = Env(budget=NO_BUDGET)
    a = env.orch.analyze(request())
    assert env.provider.calls == [] and a.source is Source.SKIPPED and a.reason == "BUDGET"


def test_budget_is_never_exceeded_over_many_analyses():
    env = Env(budget=AIBudget(D("0.01"), D("0.003"), D("1")), activation=ActivationRules(
        {"KIBO": AgentActivation(frozenset({TriggerEvent.SETUP_CANDIDATE}), 0)}))
    for i in range(200):
        env.orch.analyze(request(payload={"i": i}, subject="S%d" % i))
    spent = sum(r.cost for r in env.records)
    assert 0 < spent <= D("0.003") and env.ledger.status().spent_today <= D("0.003")
    assert any(r.reason == "BUDGET" for r in env.records)


def test_provider_failure_is_charged_conservatively_and_falls_back():
    env = Env(FakeProvider(fail=True))
    a = env.orch.analyze(request(), RuleAnswer({"approve": False}, D("0.4")))
    assert a.source is Source.RULES and a.answer == {"approve": False}
    failed = [r for r in env.records if r.reason.startswith("ANBIETER_FEHLER")]
    assert failed and failed[0].cost == failed[0].reserved > 0
    assert env.ledger.status().spent_today == failed[0].reserved


@pytest.mark.parametrize("text", ["kein json", "[1,2]", '{"approve": true}', '{"confidence": 1.5}',
                                  '{"confidence": "NaN"}', '{"confidence": true}'])
def test_unusable_answers_are_never_used(text):
    env = Env(FakeProvider(text=text))
    a = env.orch.analyze(request(max_tier=Tier.CHEAP))
    assert a.answer is None and a.source is Source.SKIPPED
    assert parse_answer(text) == (None, D(0))


def test_too_large_or_invalid_requests_never_reach_the_provider():
    env = Env()
    big = env.orch.analyze(request(payload={"x": "y" * MAX_PROMPT_BYTES}))
    assert big.reason == "EINGABE_ZU_GROSS"
    assert env.orch.analyze(request(expected_value_usd=D("NaN"))).reason == "NUTZEN_UNGUELTIG"
    assert env.orch.analyze(request(max_output_tokens=0)).reason == "AUSGABE_UNGUELTIG"
    assert env.provider.calls == []


def test_violated_token_assumption_blocks_the_budget():
    env = Env(FakeProvider(input_tokens=10_000_000))
    env.orch.analyze(request())
    assert env.ledger.status().blocked
    assert env.orch.analyze(request(subject="X", payload={"other": 1})).reason == "BUDGET"


def test_unknown_price_means_no_call():
    routing = RoutingPolicy({Tier.CHEAP: DEFAULT_ROUTING.models[Tier.CHEAP].__class__("anthropic", "claude-neu")})
    env = Env(routing=routing)
    assert env.orch.analyze(request()).reason == "PREIS_ODER_ANBIETER_UNBEKANNT"
    assert env.provider.calls == []


def test_payload_is_compact_and_order_independent():
    assert canonical_payload({"b": 1, "a": D("1.50")}) == canonical_payload({"a": D("1.50"), "b": 1}) == '{"a":"1.50","b":1}'


# ------------------------------------------------------------------ Sicherheitspruefungen bleiben unabhaengig


SAFETY_MODULES = ["src/trading/safety", "src/trading/execution", "src/trading/agents/rin_rules.py",
                  "src/trading/agents/sora_rules.py", "src/trading/persistence/pg_executor.py",
                  "src/trading/persistence/kill_switch.py", "src/trading/config.py"]


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name


# RIN und SORA erben die Agenten-Grundform (optionale KI-Schnittstelle als Typ); die Kostenmechanik kennen sie nicht.
ALLOWED = {"src/trading/agents/rin_rules.py": {"trading.ai.provider"}, "src/trading/agents/sora_rules.py": {"trading.ai.provider"}}


@pytest.mark.parametrize("target", SAFETY_MODULES)
def test_safety_code_never_depends_on_ai_or_its_budget(target):
    """Sicherheitspruefungen duerfen nie zur Kostenersparnis abgeschaltet werden - sie kennen KI und Budget gar nicht."""
    base = ROOT / target
    files = [base] if base.is_file() else sorted(base.rglob("*.py"))
    assert files
    for f in files:
        bad = [m for m in _imports(f) if m.startswith("trading.ai") and m not in ALLOWED.get(target, set())]
        assert not bad, "%s importiert %s" % (f, bad)


def test_cooldown_starts_only_with_a_real_call():
    """Wurde wegen Wertschranke oder Budget NICHT aufgerufen, verbraucht das die Abkuehlzeit nicht."""
    env = Env()
    assert env.orch.analyze(request(expected_value_usd=D("0.0001"))).reason == "NUTZEN_ZU_GERING"
    a = env.orch.analyze(request(payload={"neu": 1}))
    assert a.source is Source.AI and len(env.provider.calls) == 1
    assert env.orch.analyze(request(payload={"neu": 2})).reason == "NICHT_AKTIVIERT"  # jetzt laeuft sie


# ------------------------------------------------------------------ Budget aus der Umgebung, AKI-Anbindung


def test_budget_from_environment_defaults_to_zero_and_rejects_garbage():
    from trading.ai.budget import budget_from_env

    assert budget_from_env({}) == NO_BUDGET
    assert budget_from_env({"AI_BUDGET_PER_CALL_USD": "0.05", "AI_BUDGET_DAILY_USD": " 1 ",
                            "AI_BUDGET_MONTHLY_USD": "20"}) == AIBudget(D("0.05"), D("1"), D("20"))
    for bad in ({"AI_BUDGET_DAILY_USD": "viel"}, {"AI_BUDGET_DAILY_USD": "NaN", "AI_BUDGET_MONTHLY_USD": "1"},
                {"AI_BUDGET_DAILY_USD": "-1"}, {"AI_BUDGET_DAILY_USD": "5", "AI_BUDGET_MONTHLY_USD": "1"}):
        with pytest.raises(BudgetError):
            budget_from_env(bad)


def test_aki_records_every_analysis_step_and_reports_net_profit_after_all_costs():
    from trading.agents.aki import AkiAgent
    from trading.audit.costs import CostCategory, OperatingCost
    from trading.audit.event_log import InMemoryEventLog
    from trading.audit.events import EventType

    env = Env()
    log = InMemoryEventLog(env.clock)
    aki = AkiAgent(log)
    registry = ProviderRegistry()
    registry.register(env.provider)
    orch = AIOrchestrator(registry, DEFAULT_PRICES, env.ledger, ActivationPolicy(DEFAULT_ACTIVATION, env.clock),
                          env.clock, aki.record_ai_call)
    orch.analyze(request())
    orch.analyze(request(agent="YUZU"))  # Cache
    orch.analyze(request(subject="ETH"), RuleAnswer({"approve": True}, D("0.9")))  # Regeln
    events = log.of_type(EventType.AI_CALL)
    assert [e.payload["source"] for e in events] == ["AI", "CACHE", "RULES"]
    assert events[0].payload["usage"]["output_tokens"] == 40 and D(events[0].payload["cost"]) > 0
    summary = aki.ai_cost_summary()
    assert summary.paid_analyses == 1 and summary.cache_hits == 1 and summary.rule_decisions == 1
    aki.record_operating_cost(OperatingCost(CostCategory.SERVER, D("3"), T0, T0 + timedelta(days=3), "VPS"))
    report = aki.net_profit_report(T0, T0 + timedelta(days=1))
    assert report.server_cost == D("1") and report.ai_cost == summary.total_cost
    assert report.net_after_all_costs == -(D("1") + summary.total_cost)  # ohne Trades: nur Kosten
    assert log.of_type(EventType.OPERATING_COST)[0].payload["category"] == "SERVER"
    assert log.verify().ok
