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


def test_violated_token_assumption_blocks_even_when_the_cost_stays_below_the_reservation():
    """Mehr Eingabe-Tokens als Bytes + Zuschlag heisst: Die Obergrenze stimmt grundsaetzlich nicht - sperren, auch wenn
    dieser eine Aufruf (wegen kurzer Antwort) noch unter seiner Reservierung blieb."""
    env = Env(FakeProvider(input_tokens=1000))  # Prompt hier nur ~100 Bytes + 512 Zuschlag
    a = env.orch.analyze(request())
    rec = [r for r in env.records if r.source is Source.AI][0]
    assert rec.cost < rec.reserved  # die Abrechnung allein haette NICHT gesperrt
    assert a.source is Source.AI
    status = env.ledger.status()
    assert status.blocked and "Eingabe-Tokens" in status.note


# ------------------------------------------------------------------ Befunde der unabhaengigen Pruefung (D-016)


class ScriptedProvider(FakeProvider):
    """Attrappe mit frei waehlbarem Verbrauch, Modell und Verzoegerung."""

    def __init__(self, usage=None, model=None, delay=0.0, **kw):
        super().__init__(**kw)
        self.usage, self.model, self.delay = usage, model, delay

    def complete(self, request):
        import time

        if self.delay:
            time.sleep(self.delay)
        base = super().complete(request)
        return AIResponse(base.text, base.provider, self.model or base.model,
                          self.usage if self.usage is not None else base.usage)


def test_missing_usage_is_charged_with_the_full_reservation_and_blocks():
    env = Env(ScriptedProvider(usage=TokenUsage()))
    env.orch.analyze(request())
    rec = [r for r in env.records if r.source is Source.AI][0]
    assert rec.cost == rec.reserved > 0
    status = env.ledger.status()
    assert status.spent_today == rec.reserved and status.blocked and "keinen Verbrauch" in status.note


def test_other_model_than_requested_is_charged_in_full_and_blocks():
    env = Env(ScriptedProvider(model="claude-opus-5-5"))
    env.orch.analyze(request())
    rec = [r for r in env.records if r.source is Source.AI][0]
    assert rec.cost == rec.reserved and env.ledger.status().blocked
    assert "anderes Modell" in env.ledger.status().note


def test_output_above_max_tokens_blocks_the_budget():
    env = Env(ScriptedProvider(usage=TokenUsage(input_tokens=50, output_tokens=401)))
    env.orch.analyze(request(max_output_tokens=400))
    assert env.ledger.status().blocked and "Ausgabe-Tokens" in env.ledger.status().note


def test_value_cap_holds_for_the_whole_analysis_not_per_tier():
    from trading.ai.orchestrator import TIER_ORDER  # noqa: F401 - Reihenfolge billig -> teuer

    unsure = FakeProvider({"claude-haiku-5-5": 0.1, "claude-sonnet-5-5": 0.1, "claude-opus-5-5": 0.1})
    env = Env(unsure, budget=AIBudget(D(1), D(10), D(100)))
    req = request(max_tier=Tier.PREMIUM)
    prompt_bytes = len((req.instruction + "\n" + canonical_payload(req.payload)).encode())
    sonnet_ceiling = DEFAULT_PRICES.get("anthropic", "claude-sonnet-5-5").max_cost(prompt_bytes, req.max_output_tokens)
    # Die Sonnet-Obergrenze allein passt unter die Schranke, zusammen mit den Haiku-Kosten aber nicht.
    value = (sonnet_ceiling + D("0.00000002")) / DEFAULT_ROUTING.max_cost_share
    a = env.orch.analyze(request(max_tier=Tier.PREMIUM, expected_value_usd=value))
    assert [c.model for c in unsure.calls] == ["claude-haiku-5-5"]
    assert a.cost <= value * DEFAULT_ROUTING.max_cost_share
    assert any(r.reason == "NUTZEN_ZU_GERING" and r.tier is Tier.STANDARD for r in env.records)


def test_less_confident_ai_never_overrides_more_confident_rules():
    env = Env(FakeProvider({"claude-haiku-5-5": 0.3}))
    rules = RuleAnswer({"approve": False}, D("0.79"))
    a = env.orch.analyze(request(), rules)
    assert a.source is Source.RULES and a.answer == {"approve": False} and a.reason == "REGELN_ERSATZ:KI_NICHT_SICHERER"
    assert a.cost > 0  # der Aufruf wurde trotzdem bezahlt und gemessen
    env2 = Env(FakeProvider({"claude-haiku-5-5": 0.9}))
    assert env2.orch.analyze(request(), rules).source is Source.AI


def test_uncertain_ai_results_are_not_reused_from_cache():
    env = Env(FakeProvider({"claude-haiku-5-5": 0.5}))
    env.orch.analyze(request(max_tier=Tier.CHEAP))
    env.orch.analyze(request(max_tier=Tier.CHEAP, subject="ETH"))  # gleiche Eingabe, anderer Gegenstand
    assert len(env.provider.calls) == 2


def test_rule_fallbacks_are_measured_as_rule_decisions():
    env = Env(budget=NO_BUDGET)
    env.orch.analyze(request(), RuleAnswer({"approve": False}, D("0.5")))
    assert [(r.source, r.reason) for r in env.records] == [(Source.SKIPPED, "BUDGET"),
                                                          (Source.RULES, "REGELN_ERSATZ:BUDGET")]


def test_failed_paid_calls_count_as_paid_calls():
    from trading.audit.costs import summarize_ai_costs

    env = Env(FakeProvider(fail=True))
    env.orch.analyze(request())
    s = summarize_ai_costs(env.records)
    assert s.paid_calls_by_agent == {"KIBO": 1} and s.total_cost > 0 and s.skipped_by_reason == {}


def test_failing_measurement_blocks_further_paid_calls_but_keeps_the_result():
    env = Env()

    def broken(rec):
        raise OSError("Protokoll voll")

    env.orch._recorder = broken
    a = env.orch.analyze(request())
    assert a.source is Source.AI and a.records and a.records[-1].cost > 0
    assert env.ledger.status().blocked and "Messung" in env.ledger.status().note


class SettleFails(InMemoryBudgetLedger):
    def settle(self, reservation, actual):
        raise ConnectionError("Datenbank weg")


def test_failing_settlement_blocks_and_falls_back_but_the_call_is_measured():
    clock = FixedClock(T0)
    registry = ProviderRegistry()
    provider = FakeProvider()
    registry.register(provider)
    led = SettleFails(BUDGET, clock)
    records = []
    orch = AIOrchestrator(registry, DEFAULT_PRICES, led, ActivationPolicy(DEFAULT_ACTIVATION, clock), clock,
                          records.append)
    a = orch.analyze(request(), RuleAnswer({"approve": False}, D("0.4")))
    assert a.source is Source.RULES and a.reason == "REGELN_ERSATZ:BUDGET_ABRECHNUNG_FEHLER"
    assert [r.source for r in records][:1] == [Source.AI] and led.status().blocked
    assert led.status().spent_today > 0  # offene Reservierung zaehlt voll


def test_deeply_nested_answers_are_unusable_not_an_exception():
    assert parse_answer("[" * 200_000) == (None, D(0))
    env = Env(FakeProvider(text="[" * 200_000))
    assert env.orch.analyze(request(max_tier=Tier.CHEAP)).answer is None


def test_identical_parallel_analyses_are_paid_only_once():
    slow = ScriptedProvider(delay=0.3)
    env = Env(slow)
    results = []
    threads = [threading.Thread(target=lambda a=agent: results.append(env.orch.analyze(request(agent=a))))
               for agent in ("KIBO", "YUZU", "KIBO")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(slow.calls) == 1 and sorted(r.source.value for r in results) == ["AI", "CACHE", "CACHE"]


def test_a_slow_provider_does_not_block_rule_decisions_of_others():
    import time

    env = Env(ScriptedProvider(delay=0.5))
    t = threading.Thread(target=lambda: env.orch.analyze(request()))
    t.start()
    time.sleep(0.1)
    started = time.monotonic()
    a = env.orch.analyze(request(subject="ETH", payload={"z": 1}), RuleAnswer({"approve": True}, D("0.95")))
    assert a.source is Source.RULES and time.monotonic() - started < 0.3
    t.join(5)


def test_same_subject_is_claimed_once_even_in_parallel():
    slow = ScriptedProvider(delay=0.3)
    env = Env(slow)
    threads = [threading.Thread(target=lambda i=i: env.orch.analyze(request(payload={"i": i}))) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(slow.calls) == 1  # gleiches Setup-Subjekt: nur ein bezahlter Aufruf je Abkuehlzeit
    paid = [r for r in env.records if r.source is Source.AI]
    assert env.ledger.status().spent_today == sum(r.cost for r in paid)  # nicht genutzte Reservierungen: 0 USD


def test_cache_is_bounded(monkeypatch):
    import trading.ai.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "MAX_CACHE_ENTRIES", 3)
    env = Env(activation=ActivationRules({"KIBO": AgentActivation(frozenset({TriggerEvent.SETUP_CANDIDATE}), 0)}))
    for i in range(10):
        env.orch.analyze(request(payload={"i": i}, subject="S%d" % i))
    assert len(env.orch._cache) <= 3


class RejectOnce(InMemoryBudgetLedger):
    rejected = False

    def reserve(self, amount, agent, model):
        if not self.rejected:
            self.rejected = True
            raise BudgetError("einmal abgelehnt")
        return super().reserve(amount, agent, model)


def test_budget_refusal_does_not_start_the_cooldown():
    clock = FixedClock(T0)
    registry = ProviderRegistry()
    provider = FakeProvider()
    registry.register(provider)
    orch = AIOrchestrator(registry, DEFAULT_PRICES, RejectOnce(BUDGET, clock),
                          ActivationPolicy(DEFAULT_ACTIVATION, clock), clock)
    assert orch.analyze(request()).reason == "BUDGET"
    assert orch.analyze(request(payload={"neu": 1})).source is Source.AI  # gleicher Gegenstand, direkt danach


class RacingLedger(InMemoryBudgetLedger):
    """Haelt jeden Reservierenden nach dem Lesen des Verbrauchs an, bis alle anderen auch gelesen haben (hoechstens 0,2 s).
    Ohne Sperre lesen so alle denselben alten Verbrauch und buchen gemeinsam ueber das Budget - deterministisch."""

    def __init__(self, budget, clock, parties):
        super().__init__(budget, clock)
        self.barrier = threading.Barrier(parties, timeout=0.2)

    def _spent(self, day):
        result = super()._spent(day)
        try:
            self.barrier.wait()
        except threading.BrokenBarrierError:  # mit Sperre kommt nie ein Zweiter hinzu: Wartezeit laeuft ab
            pass
        return result


def test_reservation_checks_and_books_under_one_lock():
    led = RacingLedger(AIBudget(D("0.01"), D("0.05"), D("5")), FixedClock(T0), parties=8)
    ok = []

    def worker():
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
    assert len(ok) == 5 and led.status().spent_today == D("0.05")


def test_concurrent_reservations_hold_even_with_aggressive_thread_switching():
    import sys

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for _ in range(5):
            led, _ = ledger(AIBudget(D("0.01"), D("0.2"), D("5")))
            ok: list = []

            def worker(led=led, ok=ok):
                for _ in range(10):
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
            assert len(ok) == 20 and led.status().spent_today == D("0.2")
    finally:
        sys.setswitchinterval(old)
