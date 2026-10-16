"""KI-Orchestrierung mit Kostenkontrolle: jede KI-Analyse laeuft hier durch - nirgends sonst.

Reihenfolge je Anfrage (billigstes zuerst, jede Stufe darf abbrechen):
  1. Regeln: Liefert der deterministische Algorithmus schon ein ausreichend sicheres Ergebnis -> fertig, 0 USD.
  2. Cache: Dieselbe Analyse (gleiche Art, gleiche Eingabe) innerhalb der Haltezeit -> Ergebnis wiederverwenden, 0 USD.
     Das vermeidet auch doppelte Analysen verschiedener Agenten fuer denselben Sachverhalt.
  3. Aktivierung: KI nur bei zustaendigen Ereignissen und nach Abkuehlzeit (activation.py).
  4. Modellstufen: billigste Stufe zuerst; eine teurere nur, wenn die Antwort zu unsicher war UND die Kosten weiter durch
     den erwarteten Nutzen gedeckt sind (Wertschranke: Obergrenze der Kosten <= Anteil x erwarteter Nutzen).
  5. Budget: Vor jedem Aufruf wird die Obergrenze reserviert (budget.py); passt sie nicht, kein Aufruf.
Jeder Schritt wird gemessen (Tokens, Kosten, Modell, Agent, Anlass, Setup) und an AKI gemeldet.

Sicherheit: KI-Antworten sind Beratung (typisierte Daten). Der Orchestrator hat keinen Zugriff auf Limits, Risikomanager,
Executor oder Not-Aus; Sicherheitspruefungen haengen nie von KI oder Budget ab. Faellt die KI aus (Budget, Fehler,
unlesbare Antwort), entscheiden die Agenten regelbasiert weiter.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Callable, Dict, List, Mapping, Optional, Tuple, Union

from trading.ai.activation import ActivationPolicy, TriggerEvent
from trading.ai.budget import BudgetError, BudgetLedger
from trading.ai.pricing import NO_USAGE, REQUEST_OVERHEAD_TOKENS, PriceTable, PricingError, TokenUsage
from trading.ai.provider import AIProvider, AIRequest, ProviderRegistry
from trading.audit.events import to_jsonable
from trading.clock import Clock
from trading.domain.numeric import is_finite_decimal

ZERO = Decimal(0)
ONE = Decimal(1)
# Kompakte Prompts: strukturierte Daten, keine Romane. Liegt weit unter Preisgrenzen (z. B. 100 000 Tokens bei Haiku 5.5).
MAX_PROMPT_BYTES = 32_000
MAX_OUTPUT_TOKENS = 4_000


class Tier(str, Enum):
    CHEAP = "CHEAP"
    STANDARD = "STANDARD"
    PREMIUM = "PREMIUM"


TIER_ORDER: Tuple[Tier, ...] = (Tier.CHEAP, Tier.STANDARD, Tier.PREMIUM)


class Source(str, Enum):
    RULES = "RULES"  # deterministisch, ohne KI
    CACHE = "CACHE"  # wiederverwendet, ohne neuen Aufruf
    AI = "AI"  # bezahlter Aufruf
    SKIPPED = "SKIPPED"  # kein Aufruf (Grund in `reason`)


@dataclass(frozen=True)
class TierModel:
    provider: str
    model: str


@dataclass(frozen=True)
class RoutingPolicy:
    models: Mapping[Tier, TierModel]
    max_cost_share: Decimal = Decimal("0.10")  # eine Analyse darf hoechstens 10 % ihres erwarteten Nutzens kosten
    accept_confidence: Decimal = Decimal("0.70")  # ab dieser Sicherheit wird nicht eskaliert
    rule_confidence: Decimal = Decimal("0.80")  # so sichere Regelergebnisse brauchen keine KI
    cache_ttl_s: int = 900

    def __post_init__(self) -> None:
        for name in ("max_cost_share", "accept_confidence", "rule_confidence"):
            value = getattr(self, name)
            if not is_finite_decimal(value) or not ZERO <= value <= ONE:
                raise ValueError("RoutingPolicy.%s muss zwischen 0 und 1 liegen." % name)
        if self.cache_ttl_s < 0:
            raise ValueError("cache_ttl_s darf nicht negativ sein.")


DEFAULT_ROUTING = RoutingPolicy({
    Tier.CHEAP: TierModel("anthropic", "claude-haiku-5-5"),
    Tier.STANDARD: TierModel("anthropic", "claude-sonnet-5-5"),
    Tier.PREMIUM: TierModel("anthropic", "claude-opus-5-5"),
})


@dataclass(frozen=True)
class RuleAnswer:
    """Ergebnis des deterministischen Algorithmus (ohne KI)."""

    answer: Mapping[str, object]
    confidence: Decimal


@dataclass(frozen=True)
class AnalysisRequest:
    agent: str  # z. B. "KIBO"
    kind: str  # Art der Analyse, z. B. "kibo.setup_review"
    instruction: str  # kurze, feste Anweisung (gleich bleibender Anfang -> Prompt-Cache beim Anbieter)
    payload: Mapping[str, object]  # strukturierte Eingabe; wird als kompaktes, sortiertes JSON gesendet
    expected_value_usd: Decimal  # geschaetzter Nutzen einer besseren Entscheidung (Basis der Wertschranke)
    trigger: TriggerEvent
    subject: str = ""  # Gegenstand (Instrument, Setup) fuer Abkuehlzeit und Auswertung
    setup_id: Optional[str] = None  # Zuordnung Analyse -> Signal -> Trade
    max_tier: Tier = Tier.CHEAP
    max_output_tokens: int = 400


@dataclass(frozen=True)
class AICallRecord:
    """Ein Messpunkt fuer AKI: jede Analyse-Stufe, auch die ohne Kosten (Regeln, Cache, uebersprungen)."""

    analysis_id: str
    time: datetime
    agent: str
    kind: str
    source: Source
    reason: str
    tier: Optional[Tier] = None
    provider: str = ""
    model: str = ""
    usage: TokenUsage = NO_USAGE
    cost: Decimal = ZERO
    reserved: Decimal = ZERO
    setup_id: Optional[str] = None
    subject: str = ""
    trigger: Optional[TriggerEvent] = None
    confidence: Optional[Decimal] = None


@dataclass(frozen=True)
class Analysis:
    analysis_id: str
    source: Source
    answer: Optional[Mapping[str, object]]
    confidence: Decimal
    cost: Decimal
    reason: str
    tier: Optional[Tier] = None
    model: str = ""
    records: Tuple[AICallRecord, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        return self.answer is not None


Recorder = Callable[[AICallRecord], None]
TierOutcome = Union[str, Tuple[Optional[Mapping[str, object]], Decimal, str]]


def canonical_payload(payload: Mapping[str, object]) -> str:
    """Kompaktes, stabil sortiertes JSON (gleiche Daten -> gleicher Text -> Cache-Treffer)."""
    return json.dumps(to_jsonable(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_answer(text: str) -> Tuple[Optional[Mapping[str, object]], Decimal]:
    """Erwartet ein JSON-Objekt mit 'confidence' (0..1). Alles andere -> (None, 0): unbrauchbar, fail closed."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None, ZERO
    if not isinstance(data, dict):
        return None, ZERO
    raw = data.get("confidence")
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None, ZERO
    try:
        confidence = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None, ZERO
    if not confidence.is_finite() or not ZERO <= confidence <= ONE:
        return None, ZERO
    return data, confidence


class AIOrchestrator:
    def __init__(
        self,
        registry: ProviderRegistry,
        prices: PriceTable,
        ledger: BudgetLedger,
        activation: ActivationPolicy,
        clock: Clock,
        recorder: Optional[Recorder] = None,
        routing: RoutingPolicy = DEFAULT_ROUTING,
    ) -> None:
        self._registry = registry
        self._prices = prices
        self._ledger = ledger
        self._activation = activation
        self._clock = clock
        self._recorder = recorder
        self._routing = routing
        self._cache: Dict[str, Tuple[datetime, Analysis]] = {}
        self._ids = itertools.count(1)
        self._lock = threading.RLock()  # gleiche Analysen laufen nicht doppelt gleichzeitig

    # ------------------------------------------------------------------ oeffentlich

    def analyze(self, request: AnalysisRequest, rules: Optional[RuleAnswer] = None) -> Analysis:
        with self._lock:
            analysis_id = "A%08d" % next(self._ids)
            records: List[AICallRecord] = []

            def note(source: Source, reason: str, **kw: object) -> AICallRecord:
                rec = AICallRecord(analysis_id, self._clock.now(), request.agent, request.kind, source, reason,
                                   setup_id=request.setup_id, subject=request.subject, trigger=request.trigger,
                                   **kw)  # type: ignore[arg-type]
                records.append(rec)
                if self._recorder is not None:
                    self._recorder(rec)
                return rec

            def finish(source: Source, reason: str, answer: Optional[Mapping[str, object]], confidence: Decimal,
                       tier: Optional[Tier] = None, model: str = "") -> Analysis:
                cost = sum((r.cost for r in records), ZERO)
                return Analysis(analysis_id, source, answer, confidence, cost, reason, tier, model, tuple(records))

            problem = _request_problem(request)
            if problem:
                note(Source.SKIPPED, problem)
                return self._fallback(rules, finish, problem)

            # 1. Regeln zuerst
            if rules is not None and _valid_rules(rules) and rules.confidence >= self._routing.rule_confidence:
                note(Source.RULES, "REGELN_SICHER", confidence=rules.confidence)
                return finish(Source.RULES, "REGELN_SICHER", rules.answer, rules.confidence)

            # 2. Cache / doppelte Analyse
            payload = canonical_payload(request.payload)
            key = self._cache_key(request, payload)
            cached = self._cached(key)
            if cached is not None:
                note(Source.CACHE, "CACHE_TREFFER", tier=cached.tier, model=cached.model, confidence=cached.confidence)
                return finish(Source.CACHE, "CACHE_TREFFER", cached.answer, cached.confidence, cached.tier, cached.model)

            # 3. Ereignisbasierte Aktivierung
            subject = request.subject or request.kind
            if not self._activation.ready(request.agent, request.trigger, subject):
                note(Source.SKIPPED, "NICHT_AKTIVIERT")
                return self._fallback(rules, finish, "NICHT_AKTIVIERT")

            # 4./5. Modellstufen mit Wertschranke und Budget
            prompt_bytes = len((request.instruction + "\n" + payload).encode("utf-8"))
            best: Optional[Tuple[Mapping[str, object], Decimal, Tier, str]] = None
            stop_reason = "KEINE_STUFE"
            for tier in TIER_ORDER[: TIER_ORDER.index(request.max_tier) + 1]:
                outcome = self._try_tier(request, tier, payload, prompt_bytes, note)
                if isinstance(outcome, str):
                    stop_reason = outcome
                    break
                answer, confidence, model = outcome
                if answer is not None and (best is None or confidence >= best[1]):
                    best = (answer, confidence, tier, model)
                if answer is not None and confidence >= self._routing.accept_confidence:
                    break
                stop_reason = "UNSICHER"
            if best is None:
                return self._fallback(rules, finish, stop_reason)
            result = finish(Source.AI, "KI", best[0], best[1], best[2], best[3])
            if self._routing.cache_ttl_s > 0:
                self._cache[key] = (self._clock.now(), result)
            return result

    # ------------------------------------------------------------------ intern

    def _try_tier(
        self, request: AnalysisRequest, tier: Tier, payload: str, prompt_bytes: int,
        note: Callable[..., AICallRecord],
    ) -> TierOutcome:
        """Ein Aufruf auf einer Stufe. Rueckgabe: (Antwort, Sicherheit, Modell) oder Abbruchgrund (str)."""
        target = self._routing.models.get(tier)
        if target is None:
            note(Source.SKIPPED, "STUFE_OHNE_MODELL", tier=tier)
            return "STUFE_OHNE_MODELL"
        try:
            price = self._prices.get(target.provider, target.model)
            provider: AIProvider = self._registry.get(target.provider)
            ceiling = price.max_cost(prompt_bytes, request.max_output_tokens)
        except (PricingError, KeyError):
            note(Source.SKIPPED, "PREIS_ODER_ANBIETER_UNBEKANNT", tier=tier, provider=target.provider,
                 model=target.model)
            return "PREIS_ODER_ANBIETER_UNBEKANNT"
        if ceiling > request.expected_value_usd * self._routing.max_cost_share:
            note(Source.SKIPPED, "NUTZEN_ZU_GERING", tier=tier, provider=target.provider, model=target.model,
                 reserved=ceiling)
            return "NUTZEN_ZU_GERING"
        try:
            reservation = self._ledger.reserve(ceiling, request.agent, target.model)
        except BudgetError:
            note(Source.SKIPPED, "BUDGET", tier=tier, provider=target.provider, model=target.model, reserved=ceiling)
            return "BUDGET"
        self._activation.mark(request.agent, request.subject or request.kind)  # erst ein echter Aufruf zaehlt
        try:
            response = provider.complete(AIRequest(
                task=request.kind, prompt=payload, system=request.instruction, max_tokens=request.max_output_tokens,
                model=target.model,
            ))
            usage = response.usage
            cost = price.cost(usage)
        except Exception as exc:  # noqa: BLE001 - Anbieterfehler: volle Reservierung gilt als verbraucht
            self._ledger.settle(reservation, None)
            note(Source.SKIPPED, "ANBIETER_FEHLER:%s" % type(exc).__name__, tier=tier, provider=target.provider,
                 model=target.model, cost=reservation.amount, reserved=reservation.amount)
            return "ANBIETER_FEHLER"
        if usage.total_input > prompt_bytes + REQUEST_OVERHEAD_TOKENS:
            # Annahme der Obergrenze (Token >= 1 Byte) verletzt: Budget sperren, bis ein Mensch prueft.
            self._ledger.block("mehr Eingabe-Tokens (%d) als Bytes plus Zuschlag (%d) bei %s"
                               % (usage.total_input, prompt_bytes + REQUEST_OVERHEAD_TOKENS, target.model))
        self._ledger.settle(reservation, cost)
        answer, confidence = parse_answer(response.text)
        note(Source.AI, "KI" if answer is not None else "ANTWORT_UNBRAUCHBAR", tier=tier, provider=target.provider,
             model=target.model, usage=usage, cost=cost, reserved=reservation.amount, confidence=confidence)
        return answer, confidence, target.model

    def _fallback(self, rules: Optional[RuleAnswer], finish: Callable[..., Analysis], reason: str) -> Analysis:
        """Ohne verwertbare KI-Antwort: das (weniger sichere) Regelergebnis, sonst nichts."""
        if rules is not None and _valid_rules(rules):
            return finish(Source.RULES, "REGELN_ERSATZ:%s" % reason, rules.answer, rules.confidence)
        return finish(Source.SKIPPED, reason, None, ZERO)

    def _cache_key(self, request: AnalysisRequest, payload: str) -> str:
        raw = "\x1f".join((request.kind, request.instruction, payload, request.max_tier.value))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cached(self, key: str) -> Optional[Analysis]:
        hit = self._cache.get(key)
        if hit is None:
            return None
        stored, analysis = hit
        age = (self._clock.now() - stored).total_seconds()
        if age < 0 or age > self._routing.cache_ttl_s:
            del self._cache[key]
            return None
        return analysis


def _valid_rules(rules: RuleAnswer) -> bool:
    return is_finite_decimal(rules.confidence) and ZERO <= rules.confidence <= ONE


def _request_problem(request: AnalysisRequest) -> str:
    if not isinstance(request.trigger, TriggerEvent):
        return "ANLASS_UNGUELTIG"
    if not is_finite_decimal(request.expected_value_usd) or request.expected_value_usd < 0:
        return "NUTZEN_UNGUELTIG"
    if not isinstance(request.max_tier, Tier):
        return "STUFE_UNGUELTIG"
    if not 0 < request.max_output_tokens <= MAX_OUTPUT_TOKENS:
        return "AUSGABE_UNGUELTIG"
    try:
        payload = canonical_payload(request.payload)
    except (TypeError, ValueError):
        return "EINGABE_UNGUELTIG"
    if len((request.instruction + "\n" + payload).encode("utf-8")) > MAX_PROMPT_BYTES:
        return "EINGABE_ZU_GROSS"
    return ""


__all__ = [
    "AICallRecord", "AIOrchestrator", "Analysis", "AnalysisRequest", "DEFAULT_ROUTING", "MAX_OUTPUT_TOKENS",
    "MAX_PROMPT_BYTES", "RoutingPolicy", "RuleAnswer", "Source", "Tier", "TierModel", "canonical_payload",
    "parse_answer",
]
