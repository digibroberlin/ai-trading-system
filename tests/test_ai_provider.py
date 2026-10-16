"""KI-Anbieter-Anschluss (AP-22, D-024): Claude-Adapter hinter dem Orchestrator - NUR mit einer Attrappe des SDK-Clients.

Kein Test ruft einen echten Anbieter auf, keiner braucht einen Schluessel oder das Paket `anthropic`.
"""

from __future__ import annotations

import ast
import json
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import T0

from trading.ai.activation import DEFAULT_ACTIVATION, ActivationPolicy, TriggerEvent
from trading.ai.anthropic_provider import (
    APPROVAL_ENV,
    APPROVAL_TEXT,
    KEY_ENV,
    MAX_PRICE_AGE_DAYS,
    AnthropicProvider,
    ProviderUnavailable,
    check_price_table,
    live_registry,
    usage_of,
)
from trading.ai.budget import NO_BUDGET, AIBudget, InMemoryBudgetLedger
from trading.ai.orchestrator import DEFAULT_ROUTING, AIOrchestrator, AnalysisRequest, RoutingPolicy, RuleAnswer, Source
from trading.ai.orchestrator import Tier, TierModel
from trading.ai.pricing import DEFAULT_PRICES, NO_USAGE, PriceTable, PricingError, TokenUsage
from trading.ai.provider import AIRequest, ProviderRegistry
from trading.clock import FixedClock

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "trading"
FAKE_KEY = "attrappe-ohne-wert"
TODAY = DEFAULT_PRICES.as_of + timedelta(days=10)
BUDGET = AIBudget(per_call_usd=D("0.05"), daily_usd=D("1"), monthly_usd=D("10"))


def message(text="", model="claude-haiku-5-5", usage=None, stop="end_turn", blocks=None):
    usage = usage if usage is not None else SimpleNamespace(input_tokens=120, output_tokens=30,
                                                             cache_creation_input_tokens=None,
                                                             cache_read_input_tokens=None)
    content = blocks if blocks is not None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=content, model=model, usage=usage, stop_reason=stop)


class FakeClient:
    """Bildet `anthropic.Anthropic().messages.create` nach: nimmt die Argumente entgegen, liefert eine Nachricht."""

    def __init__(self, reply=None, error=None):
        self.calls = []
        self.reply = reply or (lambda kw: message(json.dumps({"approve": True, "confidence": 0.9}), kw["model"]))
        self.error = error
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if self.error is not None:
            raise self.error
        return self.reply(kw)


def ai_request(**kw):
    base = dict(task="kibo.setup_review", prompt='{"rsi":"61.5"}', system="Bewerte das Setup. Antworte als JSON.",
                max_tokens=300, model="claude-haiku-5-5")
    base.update(kw)
    return AIRequest(**base)


# ------------------------------------------------------------------ Uebersetzung Anfrage/Antwort


def test_request_is_translated_into_one_sdk_call_with_a_cacheable_instruction_and_a_timeout():
    client = FakeClient()
    AnthropicProvider(client, timeout_s=12).complete(ai_request())
    assert client.calls == [{
        "model": "claude-haiku-5-5", "max_tokens": 300, "timeout": 12.0,
        "messages": [{"role": "user", "content": '{"rsi":"61.5"}'}],
        "system": [{"type": "text", "text": "Bewerte das Setup. Antworte als JSON.",
                    "cache_control": {"type": "ephemeral"}}],
    }]
    AnthropicProvider(client, cache_instruction=False).complete(ai_request())
    assert client.calls[-1]["system"] == [{"type": "text", "text": "Bewerte das Setup. Antworte als JSON."}]
    AnthropicProvider(client).complete(ai_request(system=""))
    assert "system" not in client.calls[-1]


def test_usage_is_taken_from_the_provider_answer():
    usage = SimpleNamespace(input_tokens=50, output_tokens=20, cache_creation_input_tokens=900,
                            cache_read_input_tokens=1800)
    r = AnthropicProvider(FakeClient(lambda kw: message('{"confidence": 0.8}', usage=usage))).complete(ai_request())
    assert r.usage == TokenUsage(input_tokens=50, output_tokens=20, cache_write_tokens=900, cache_read_tokens=1800)
    assert (r.provider, r.model, r.text) == ("anthropic", "claude-haiku-5-5", '{"confidence": 0.8}')
    assert usage_of(SimpleNamespace(usage=None)) == NO_USAGE  # kein Verbrauch gemeldet -> keiner erfunden
    assert usage_of(SimpleNamespace(usage=SimpleNamespace(input_tokens=3, output_tokens=1))) == TokenUsage(3, 1)
    for bad in (SimpleNamespace(input_tokens=-1, output_tokens=1), SimpleNamespace(input_tokens="12", output_tokens=1),
                SimpleNamespace(input_tokens=1.5, output_tokens=1)):
        with pytest.raises(PricingError):
            usage_of(SimpleNamespace(usage=bad))


def test_only_text_blocks_count_and_cut_off_or_refused_answers_are_never_used():
    blocks = [SimpleNamespace(type="thinking", thinking="..."), SimpleNamespace(type="text", text='{"confidence":'),
              SimpleNamespace(type="text", text=" 0.7}")]
    provider = AnthropicProvider(FakeClient(lambda kw: message(blocks=blocks)))
    assert provider.complete(ai_request()).text == '{"confidence": 0.7}'
    for stop in ("max_tokens", "refusal"):
        cut = AnthropicProvider(FakeClient(lambda kw, s=stop: message('{"confidence": 0.9}', stop=s)))
        r = cut.complete(ai_request())
        assert r.text == "" and r.usage.output_tokens == 30  # verbraucht ist trotzdem verbraucht
    assert AnthropicProvider(FakeClient(lambda kw: message(blocks=[]))).complete(ai_request()).text == ""


def test_provider_errors_are_not_swallowed_and_calls_need_a_model():
    with pytest.raises(TimeoutError):
        AnthropicProvider(FakeClient(error=TimeoutError("zu langsam"))).complete(ai_request())
    client = FakeClient()
    with pytest.raises(ValueError):
        AnthropicProvider(client).complete(ai_request(model=""))
    assert client.calls == []
    for bad in (0, -1, 61):
        with pytest.raises(ValueError):
            AnthropicProvider(client, timeout_s=bad)
    assert FAKE_KEY not in repr(AnthropicProvider(SimpleNamespace(api_key=FAKE_KEY)))


# ------------------------------------------------------------------ zusammen mit dem Orchestrator


class Env:
    def __init__(self, client, budget=BUDGET):
        self.clock = FixedClock(T0)
        registry = ProviderRegistry()
        registry.register(AnthropicProvider(client))
        self.client = client
        self.ledger = InMemoryBudgetLedger(budget, self.clock)
        self.records = []
        self.orch = AIOrchestrator(registry, DEFAULT_PRICES, self.ledger, ActivationPolicy(DEFAULT_ACTIVATION,
                                                                                         self.clock),
                                   self.clock, self.records.append)

    def analyze(self, max_tier=Tier.CHEAP, subject="BTC", rsi=D("61.5")):
        return self.orch.analyze(AnalysisRequest(
            "KIBO", "kibo.setup_review", "Bewerte das Setup. Antworte als JSON.", {"rsi": rsi}, D(50),
            TriggerEvent.SETUP_CANDIDATE, subject, "s1", max_tier), RuleAnswer({"approve": False}, D("0.6")))


def test_orchestrated_call_is_metered_with_the_reported_usage_including_the_prompt_cache():
    usage = SimpleNamespace(input_tokens=40, output_tokens=25, cache_creation_input_tokens=0,
                            cache_read_input_tokens=30)
    env = Env(FakeClient(lambda kw: message('{"approve": true, "confidence": 0.9}', kw["model"], usage)))
    a = env.analyze()
    assert a.source is Source.AI and a.answer == {"approve": True, "confidence": 0.9} and a.model == "claude-haiku-5-5"
    expected = DEFAULT_PRICES.get("anthropic", "claude-haiku-5-5").cost(TokenUsage(40, 25, 0, 30))
    assert a.cost == expected and env.ledger.status().spent_today == expected
    ai = [r for r in env.records if r.source is Source.AI]
    assert len(ai) == 1 and ai[0].usage.cache_read_tokens == 30 and ai[0].cost == expected
    assert env.client.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_escalates_to_the_next_model_only_when_the_cheap_answer_is_unsure():
    conf = {"claude-haiku-5-5": 0.5, "claude-sonnet-5-5": 0.85}
    env = Env(FakeClient(lambda kw: message(json.dumps({"approve": True, "confidence": conf[kw["model"]]}),
                                            kw["model"])))
    a = env.analyze(max_tier=Tier.STANDARD)
    assert [c["model"] for c in env.client.calls] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert a.model == "claude-sonnet-5-5" and a.source is Source.AI


def test_failures_fall_back_to_the_rules_and_are_charged_conservatively():
    env = Env(FakeClient(error=TimeoutError("zu langsam")))
    a = env.analyze()
    assert a.source is Source.RULES and a.answer == {"approve": False}
    failed = [r for r in env.records if r.reason.startswith("ANBIETER_FEHLER")]
    assert failed and failed[0].cost == failed[0].reserved > 0  # Aufruf koennte berechnet worden sein
    cut = Env(FakeClient(lambda kw: message('{"approve": true, "confidence": 0.99}', kw["model"], stop="max_tokens")))
    a = cut.analyze()
    assert a.source is Source.RULES and [r.reason for r in cut.records if r.source is Source.AI] == ["ANTWORT_UNBRAUCHBAR"]


def _no_usage(kw):
    reply = message('{"confidence": 0.9}', kw["model"])
    reply.usage = None
    return reply


@pytest.mark.parametrize("reply", [
    _no_usage,  # Anbieter meldet keinen Verbrauch
    lambda kw: message('{"confidence": 0.9}', kw["model"], SimpleNamespace(input_tokens=0, output_tokens=0)),
    lambda kw: message('{"confidence": 0.9}', "claude-opus-5-5"),  # anderes Modell als angefragt
], ids=["ohne-verbrauch", "verbrauch-null", "anderes-modell"])
def test_missing_usage_or_another_model_blocks_the_budget(reply):
    env = Env(FakeClient(reply))
    env.analyze()
    assert env.ledger.status().blocked  # bis ein Mensch prueft
    ai = [r for r in env.records if r.source is Source.AI]
    assert ai[0].cost == ai[0].reserved  # Kosten unbekannt -> volle Reservierung
    later = env.analyze(subject="ETH", rsi=D(40))  # neue Frage: kein Cache, aber auch kein Aufruf mehr
    assert later.source is Source.RULES and len(env.client.calls) == 1


def test_without_an_approved_budget_the_provider_is_never_called():
    env = Env(FakeClient(), budget=NO_BUDGET)
    assert env.analyze().source is Source.RULES and env.client.calls == []


# ------------------------------------------------------------------ Einsatzpruefung (echter Anschluss)


def approved(**extra):
    env = {APPROVAL_ENV: APPROVAL_TEXT, KEY_ENV: FAKE_KEY}
    env.update(extra)
    return env


def test_live_connection_needs_approval_key_and_current_prices():
    made = []

    def factory(key, timeout):
        made.append((key, timeout))
        return FakeClient()

    for env in ({}, {KEY_ENV: FAKE_KEY}, {APPROVAL_ENV: "ja", KEY_ENV: FAKE_KEY}, approved(**{KEY_ENV: "  "})):
        with pytest.raises(ProviderUnavailable) as err:
            live_registry(DEFAULT_PRICES, TODAY, env=env, client_factory=factory)
        assert FAKE_KEY not in str(err.value)
    stale = TODAY + timedelta(days=MAX_PRICE_AGE_DAYS + 1)
    with pytest.raises(PricingError):
        live_registry(DEFAULT_PRICES, stale, env=approved(), client_factory=factory)
    other = RoutingPolicy({Tier.CHEAP: TierModel("openai", "gpt-x")})
    with pytest.raises(ProviderUnavailable):
        live_registry(DEFAULT_PRICES, TODAY, other, env=approved(), client_factory=factory)
    assert made == []  # ohne alle Pruefungen wird nicht einmal ein Client gebaut
    registry = live_registry(DEFAULT_PRICES, TODAY, env=approved(), client_factory=factory)
    assert registry.names() == ["anthropic"] and made == [(FAKE_KEY, 30.0)]


def test_price_table_must_be_current_and_cover_every_tier():
    check_price_table(DEFAULT_PRICES, DEFAULT_ROUTING, DEFAULT_PRICES.as_of)
    check_price_table(DEFAULT_PRICES, DEFAULT_ROUTING, DEFAULT_PRICES.as_of + timedelta(days=MAX_PRICE_AGE_DAYS))
    for day in (DEFAULT_PRICES.as_of - timedelta(days=1), DEFAULT_PRICES.as_of + timedelta(days=MAX_PRICE_AGE_DAYS + 1)):
        with pytest.raises(PricingError):
            check_price_table(DEFAULT_PRICES, DEFAULT_ROUTING, day)
    only_haiku = PriceTable.build(date(2026, 10, 6), "Test", [DEFAULT_PRICES.get("anthropic", "claude-haiku-5-5")])
    with pytest.raises(PricingError):
        check_price_table(only_haiku, DEFAULT_ROUTING, TODAY)


def test_missing_sdk_is_reported_instead_of_guessed(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # Import scheitert wie ohne installiertes Paket
    with pytest.raises(ProviderUnavailable):
        live_registry(DEFAULT_PRICES, TODAY, env=approved())


def test_sdk_client_is_built_without_automatic_retries(monkeypatch):
    built = []
    fake_sdk = SimpleNamespace(Anthropic=lambda **kw: built.append(kw) or FakeClient())
    monkeypatch.setitem(sys.modules, "anthropic", fake_sdk)
    live_registry(DEFAULT_PRICES, TODAY, env=approved())
    # Jede Wiederholung waere ein weiterer, nicht reservierter Aufruf -> nur der Orchestrator entscheidet
    assert built == [{"api_key": FAKE_KEY, "max_retries": 0, "timeout": 30.0}]


def test_live_connection_still_spends_nothing_without_a_budget():
    registry = live_registry(DEFAULT_PRICES, TODAY, env=approved(), client_factory=lambda k, t: FakeClient())
    clock = FixedClock(T0)
    orch = AIOrchestrator(registry, DEFAULT_PRICES, InMemoryBudgetLedger(NO_BUDGET, clock),
                          ActivationPolicy(DEFAULT_ACTIVATION, clock), clock)
    a = orch.analyze(AnalysisRequest("KIBO", "kibo.setup_review", "x", {"a": 1}, D(50), TriggerEvent.SETUP_CANDIDATE,
                                     "BTC"), RuleAnswer({"approve": True}, D("0.6")))
    assert a.source is Source.RULES and a.cost == 0


# ------------------------------------------------------------------ Struktur


def _calls(path, attr):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr]


def test_providers_are_called_only_by_the_orchestrator_and_the_sdk_is_loaded_in_one_place():
    complete = sorted(str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if _calls(p, "complete"))
    assert complete == ["ai/orchestrator.py"]
    loaders = sorted(str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if _calls(p, "import_module"))
    assert loaders == ["ai/anthropic_provider.py"]
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert "anthropic" not in names, path
