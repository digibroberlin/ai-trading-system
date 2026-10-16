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
from trading.ai.budget import BudgetError, BudgetLedger, Reservation
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
    AI = "AI"  # Aufruf beim Anbieter (bezahlt) - auch wenn er scheiterte oder unbrauchbar antwortete
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
    except Exception:  # noqa: BLE001 - jede unlesbare Antwort (auch zu tief verschachtelt) ist unbrauchbar
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


MAX_CACHE_ENTRIES = 10_000


class _Run:
    """Zustand EINER Analyse: Messpunkte, bisherige Kosten, ob der Gegenstand schon beansprucht wurde."""

    def __init__(self, analysis_id: str) -> None:
        self.analysis_id = analysis_id
        self.records: List[AICallRecord] = []
        self.claimed = False

    @property
    def spent(self) -> Decimal:
        return sum((r.cost for r in self.records), ZERO)


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
        self._inflight: Dict[str, threading.Event] = {}  # gleiche Analyse laeuft gerade -> warten statt doppelt zahlen
        self._ids = itertools.count(1)
        # Schuetzt nur den inneren Zustand (Cache, laufende Analysen, Nummern). Der Anbieter-Aufruf laeuft AUSSERHALB:
        # ein langsamer Anbieter haelt Regel- und Cache-Entscheidungen anderer Agenten nicht auf.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ oeffentlich

    def analyze(self, request: AnalysisRequest, rules: Optional[RuleAnswer] = None) -> Analysis:
        with self._lock:
            run = _Run("A%08d" % next(self._ids))
        valid_rules = rules if rules is not None and _valid_rules(rules) else None

        problem = _request_problem(request)
        if problem:
            self._note(run, request, Source.SKIPPED, problem)
            return self._fallback(run, request, valid_rules, problem)

        # 1. Regeln zuerst
        if valid_rules is not None and valid_rules.confidence >= self._routing.rule_confidence:
            self._note(run, request, Source.RULES, "REGELN_SICHER", confidence=valid_rules.confidence)
            return self._finish(run, Source.RULES, "REGELN_SICHER", valid_rules.answer, valid_rules.confidence)

        # 2. Cache / doppelte Analyse (auch gleichzeitig laufende: einmal zahlen, alle bekommen das Ergebnis)
        payload = canonical_payload(request.payload)
        key = self._cache_key(request, payload)
        while True:
            with self._lock:
                cached = self._cached(key)
                waiting = self._inflight.get(key) if cached is None else None
                if cached is None and waiting is None:
                    self._inflight[key] = threading.Event()
                    break
            if cached is not None:
                self._note(run, request, Source.CACHE, "CACHE_TREFFER", tier=cached.tier, model=cached.model,
                           confidence=cached.confidence)
                if valid_rules is not None and cached.confidence <= valid_rules.confidence:
                    # Wie bei einer frischen Analyse: Eine nicht sicherere KI-Antwort ersetzt die Regeln nie (auch
                    # nicht aus dem Cache oder fuer Wartende einer parallel laufenden Analyse).
                    return self._fallback(run, request, valid_rules, "KI_NICHT_SICHERER")
                return self._finish(run, Source.CACHE, "CACHE_TREFFER", cached.answer, cached.confidence,
                                    cached.tier, cached.model)
            assert waiting is not None
            if not waiting.wait(timeout=120):
                self._note(run, request, Source.SKIPPED, "PARALLEL_LAUFEND")
                return self._fallback(run, request, valid_rules, "PARALLEL_LAUFEND")
        try:
            return self._analyze_new(run, request, valid_rules, payload, key)
        finally:
            with self._lock:
                event = self._inflight.pop(key, None)
            if event is not None:
                event.set()

    # ------------------------------------------------------------------ intern

    def _analyze_new(
        self, run: _Run, request: AnalysisRequest, rules: Optional[RuleAnswer], payload: str, key: str
    ) -> Analysis:
        # 3. Ereignisbasierte Aktivierung (Vorpruefung; beansprucht wird erst beim echten Aufruf)
        if not self._activation.ready(request.agent, request.trigger, request.subject or request.kind):
            self._note(run, request, Source.SKIPPED, "NICHT_AKTIVIERT")
            return self._fallback(run, request, rules, "NICHT_AKTIVIERT")

        # 4./5. Modellstufen mit Wertschranke (fuer die GANZE Analyse) und Budget
        prompt_bytes = len((request.instruction + "\n" + payload).encode("utf-8"))
        best: Optional[Tuple[Mapping[str, object], Decimal, Tier, str]] = None
        stop_reason = "KEINE_STUFE"
        for tier in TIER_ORDER[: TIER_ORDER.index(request.max_tier) + 1]:
            outcome = self._try_tier(run, request, tier, payload, prompt_bytes)
            if isinstance(outcome, str):
                stop_reason = outcome
                break
            answer, confidence, model = outcome
            if answer is not None and (best is None or confidence > best[1]):
                best = (answer, confidence, tier, model)
            if answer is not None and confidence >= self._routing.accept_confidence:
                break
            stop_reason = "UNSICHER"
        if best is None:
            return self._fallback(run, request, rules, stop_reason)
        if rules is not None and best[1] <= rules.confidence:
            # Die KI war nicht sicherer als die (guenstigeren) Regeln: die Regeln entscheiden.
            return self._fallback(run, request, rules, "KI_NICHT_SICHERER")
        result = self._finish(run, Source.AI, "KI", best[0], best[1], best[2], best[3])
        if self._routing.cache_ttl_s > 0 and best[1] >= self._routing.accept_confidence:
            with self._lock:  # nur ausreichend sichere Ergebnisse wiederverwenden
                self._store(key, result)
        return result

    def _try_tier(self, run: _Run, request: AnalysisRequest, tier: Tier, payload: str, prompt_bytes: int) -> TierOutcome:
        """Ein Aufruf auf einer Stufe. Rueckgabe: (Antwort, Sicherheit, Modell) oder Abbruchgrund (str)."""
        target = self._routing.models.get(tier)
        if target is None:
            self._note(run, request, Source.SKIPPED, "STUFE_OHNE_MODELL", tier=tier)
            return "STUFE_OHNE_MODELL"
        meta = dict(tier=tier, provider=target.provider, model=target.model)
        try:
            price = self._prices.get(target.provider, target.model)
            provider: AIProvider = self._registry.get(target.provider)
            ceiling = price.max_cost(prompt_bytes, request.max_output_tokens)
        except (PricingError, KeyError):
            self._note(run, request, Source.SKIPPED, "PREIS_ODER_ANBIETER_UNBEKANNT", **meta)
            return "PREIS_ODER_ANBIETER_UNBEKANNT"
        # Wertschranke fuer die ganze Analyse: bisherige Stufen + Obergrenze dieser Stufe
        if run.spent + ceiling > request.expected_value_usd * self._routing.max_cost_share:
            self._note(run, request, Source.SKIPPED, "NUTZEN_ZU_GERING", reserved=ceiling, **meta)
            return "NUTZEN_ZU_GERING"
        try:
            reservation = self._ledger.reserve(ceiling, request.agent, target.model)
        except BudgetError:
            self._note(run, request, Source.SKIPPED, "BUDGET", reserved=ceiling, **meta)
            return "BUDGET"
        if not run.claimed:
            # Erst jetzt beanspruchen (unteilbar): Ein anderer Lauf fuer denselben Gegenstand kann nicht dazwischen.
            if not self._activation.should_run(request.agent, request.trigger, request.subject or request.kind):
                self._settle(reservation, ZERO)  # kein Aufruf -> nichts verbraucht
                self._note(run, request, Source.SKIPPED, "NICHT_AKTIVIERT", **meta)
                return "NICHT_AKTIVIERT"
            run.claimed = True
        try:
            response = provider.complete(AIRequest(
                task=request.kind, prompt=payload, system=request.instruction, max_tokens=request.max_output_tokens,
                model=target.model,
            ))
        except Exception as exc:  # noqa: BLE001 - Anbieterfehler: der Aufruf kann trotzdem berechnet worden sein
            self._note(run, request, Source.AI, "ANBIETER_FEHLER:%s" % type(exc).__name__, cost=reservation.amount,
                       reserved=reservation.amount, **meta)
            self._settle(reservation, None)
            return "ANBIETER_FEHLER"

        # Annahmen der Obergrenze pruefen. Jede Verletzung sperrt das Budget, bis ein Mensch prueft.
        usage = response.usage
        problems: List[str] = []
        wrong_model = response.provider != target.provider or response.model != target.model
        no_usage = usage.total_input == 0 and (price.input_per_mtok > 0 or price.cache_write_per_mtok > 0)
        # Eine nicht leere Antwort hat mindestens ein Ausgabe-Token: 0 ist sicher eine falsche Meldung.
        no_output = usage.output_tokens == 0 and bool(response.text) and price.output_per_mtok > 0
        if wrong_model:
            problems.append("Anbieter meldet anderes Modell (%s/%s statt %s/%s)"
                            % (response.provider, response.model, target.provider, target.model))
        if no_usage:
            problems.append("Anbieter meldet keinen Verbrauch")
        if no_output:
            problems.append("Anbieter meldet 0 Ausgabe-Tokens fuer eine nicht leere Antwort")
        if usage.total_input > prompt_bytes + REQUEST_OVERHEAD_TOKENS:
            problems.append("mehr Eingabe-Tokens (%d) als Bytes plus Zuschlag (%d)"
                            % (usage.total_input, prompt_bytes + REQUEST_OVERHEAD_TOKENS))
        if usage.output_tokens > request.max_output_tokens:
            problems.append("mehr Ausgabe-Tokens (%d) als max_tokens (%d)" % (usage.output_tokens, request.max_output_tokens))
        # Verbrauch unbekannt oder falsches Modell: Kosten nicht bestimmbar -> volle Reservierung (im Zweifel teurer).
        charge: Optional[Decimal] = None if wrong_model or no_usage or no_output else price.cost(usage)
        answer, confidence = parse_answer(response.text)
        self._note(run, request, Source.AI, "KI" if answer is not None else "ANTWORT_UNBRAUCHBAR", usage=usage,
                   cost=reservation.amount if charge is None else charge, reserved=reservation.amount,
                   confidence=confidence, **meta)
        if problems:
            self._block("; ".join(problems) + " bei %s" % target.model)
        if not self._settle(reservation, charge):
            return "BUDGET_ABRECHNUNG_FEHLER"
        return answer, confidence, target.model

    def _note(self, run: _Run, request: AnalysisRequest, source: Source, reason: str, **kw: object) -> AICallRecord:
        rec = AICallRecord(run.analysis_id, self._clock.now(), request.agent, request.kind, source, reason,
                           setup_id=request.setup_id, subject=request.subject, trigger=request.trigger,
                           **kw)  # type: ignore[arg-type]
        run.records.append(rec)
        if self._recorder is not None:
            try:
                self._recorder(rec)
            except Exception as exc:  # noqa: BLE001 - ohne Messung kein weiterer bezahlter Aufruf
                self._block("Messung (AKI) gescheitert: %s" % type(exc).__name__)
        return rec

    def _block(self, reason: str) -> None:
        try:
            self._ledger.block(reason)
        except Exception:  # noqa: BLE001 - Sperre nicht schreibbar: offene Reservierungen zaehlen weiter voll
            pass

    def _settle(self, reservation: Reservation, charge: Optional[Decimal]) -> bool:
        """Rechnet ab. Scheitert das (z. B. Datenbank weg), bleibt die Reservierung voll stehen und das Budget wird gesperrt."""
        try:
            self._ledger.settle(reservation, charge)
            return True
        except Exception as exc:  # noqa: BLE001
            self._block("Abrechnung gescheitert: %s" % type(exc).__name__)
            return False

    def _finish(self, run: _Run, source: Source, reason: str, answer: Optional[Mapping[str, object]],
                confidence: Decimal, tier: Optional[Tier] = None, model: str = "") -> Analysis:
        return Analysis(run.analysis_id, source, answer, confidence, run.spent, reason, tier, model, tuple(run.records))

    def _fallback(self, run: _Run, request: AnalysisRequest, rules: Optional[RuleAnswer], reason: str) -> Analysis:
        """Ohne (bessere) KI-Antwort: das Regelergebnis - auch das wird gemessen -, sonst nichts."""
        if rules is not None:
            self._note(run, request, Source.RULES, "REGELN_ERSATZ:%s" % reason, confidence=rules.confidence)
            return self._finish(run, Source.RULES, "REGELN_ERSATZ:%s" % reason, rules.answer, rules.confidence)
        return self._finish(run, Source.SKIPPED, reason, None, ZERO)

    def _cache_key(self, request: AnalysisRequest, payload: str) -> str:
        raw = "\x1f".join((request.kind, request.instruction, payload, request.max_tier.value,
                            str(request.max_output_tokens)))
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

    def _store(self, key: str, result: Analysis) -> None:
        now = self._clock.now()
        for k in [k for k, (t, _) in self._cache.items() if not 0 <= (now - t).total_seconds() <= self._routing.cache_ttl_s]:
            del self._cache[k]
        while len(self._cache) >= MAX_CACHE_ENTRIES:  # begrenzt: aeltester Eintrag faellt weg
            del self._cache[next(iter(self._cache))]
        self._cache[key] = (now, result)


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
