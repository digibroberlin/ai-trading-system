"""Anschluss an die Claude-API (Anthropic) hinter dem Kosten-Orchestrator (AP-22, D-024).

Was dieser Adapter tut - und was bewusst nicht:
  * Er uebersetzt `AIRequest` in einen Aufruf `client.messages.create(...)` (Schnittstelle des offiziellen SDK) und die
    Antwort zurueck in `AIResponse` - mit dem Verbrauch, den der ANBIETER meldet (`usage.input_tokens`,
    `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`). Fehlt der Verbrauch, meldet er keinen;
    der Orchestrator rechnet dann die volle Reservierung ab und sperrt das Budget (D-016).
  * Die feste Anweisung (`system`) wird als cachebarer Block gesendet (Prompt-Caching, 5 Minuten). Kurze Anweisungen
    unterschreiten die Mindestlaenge des Anbieters - dann wird schlicht nichts gecacht (kein Fehler, keine Mehrkosten).
  * Zeitlimit je Aufruf (Standard 30 s, deutlich unter den 120 s, die eine parallel wartende Analyse im Orchestrator
    hoechstens wartet) und KEINE automatischen Wiederholungen im SDK (`max_retries=0`): jede Wiederholung waere ein
    weiterer, nicht reservierter Aufruf. Ob es noch einmal versucht wird, entscheiden Orchestrator und Budget.
  * Abgeschnittene (`max_tokens`) oder verweigerte (`refusal`) Antworten werden als leerer Text gemeldet: der Orchestrator
    nutzt sie nicht (fail closed), berechnet den Verbrauch aber normal.
  * Fehler des Anbieters werden NICHT verschluckt: der Orchestrator berechnet dann vorsichtig die volle Reservierung.
  * Der Adapter kennt weder Limits noch Risikomanager oder Executor; Antworten sind nur Beratung (siehe provider.py).

Echter Einsatz (`live_registry`) nur, wenn ALLES zutrifft (B-004): Freigabe des Eigners in `TRADING_AI_APPROVAL`,
Schluessel in `ANTHROPIC_API_KEY` (wird nie angezeigt oder protokolliert), SDK installiert, Preistabelle hoechstens
`MAX_PRICE_AGE_DAYS` alt und mit Preisen fuer jedes Modell der Stufen. Unabhaengig davon gilt das KI-Budget (Standard 0):
ohne freigegebenes Budget wird auch mit Anschluss kein einziger Aufruf bezahlt. Tests benutzen nur eine Attrappe.
"""

from __future__ import annotations

import importlib
import os
from datetime import date
from typing import Any, Callable, Dict, List, Mapping, Optional

from trading.ai.orchestrator import DEFAULT_ROUTING, RoutingPolicy
from trading.ai.pricing import NO_USAGE, PriceTable, PricingError, TokenUsage
from trading.ai.provider import AIProvider, AIRequest, AIResponse, ProviderRegistry

PROVIDER = "anthropic"
APPROVAL_ENV = "TRADING_AI_APPROVAL"
APPROVAL_TEXT = "KI-AUFRUFE FREIGEGEBEN"
KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_TIMEOUT_S = 30.0
MAX_PRICE_AGE_DAYS = 60  # aelter: erst gegen die aktuelle Preisliste des Anbieters pruefen
UNUSABLE_STOPS = frozenset({"max_tokens", "refusal"})

ClientFactory = Callable[[str, float], Any]


class ProviderUnavailable(RuntimeError):
    """Der echte KI-Anschluss darf (noch) nicht benutzt werden - Grund in der Meldung, nie mit Schluessel."""


class AnthropicProvider(AIProvider):
    name = PROVIDER

    def __init__(self, client: Any, timeout_s: float = DEFAULT_TIMEOUT_S, cache_instruction: bool = True) -> None:
        if not 0 < timeout_s <= 60:
            raise ValueError("timeout_s muss zwischen 0 und 60 Sekunden liegen.")
        self._client = client
        self._timeout = float(timeout_s)
        self._cache_instruction = cache_instruction

    def __repr__(self) -> str:  # nie den Client (und damit den Schluessel) zeigen
        return "AnthropicProvider(timeout_s=%s)" % self._timeout

    def complete(self, request: AIRequest) -> AIResponse:
        if not request.model:
            raise ValueError("Kein Modell gewaehlt - Aufrufe nur ueber den Orchestrator.")
        kwargs: Dict[str, Any] = dict(
            model=request.model,
            max_tokens=request.max_tokens,
            messages=[{"role": "user", "content": request.prompt}],
            timeout=self._timeout,
        )
        if request.system:
            block: Dict[str, Any] = {"type": "text", "text": request.system}
            if self._cache_instruction:
                block["cache_control"] = {"type": "ephemeral"}
            kwargs["system"] = [block]
        message = self._client.messages.create(**kwargs)
        return AIResponse(text=_text(message), provider=self.name, model=str(getattr(message, "model", "") or ""),
                          usage=usage_of(message))


def usage_of(message: Any) -> TokenUsage:
    """Verbrauch laut Anbieter. Fehlende Teilwerte (None) zaehlen 0; Unsinn (negativ, kein int) -> PricingError."""
    usage = getattr(message, "usage", None)
    if usage is None:
        return NO_USAGE
    return TokenUsage(
        input_tokens=_count(usage, "input_tokens"),
        output_tokens=_count(usage, "output_tokens"),
        cache_write_tokens=_count(usage, "cache_creation_input_tokens"),
        cache_read_tokens=_count(usage, "cache_read_input_tokens"),
    )


def _count(usage: Any, name: str) -> Any:
    value = getattr(usage, name, None)
    return 0 if value is None else value  # Typ und Vorzeichen prueft TokenUsage


def _text(message: Any) -> str:
    if getattr(message, "stop_reason", None) in UNUSABLE_STOPS:
        return ""  # abgeschnitten oder verweigert: nicht verwenden
    parts: List[str] = []
    for block in getattr(message, "content", None) or ():
        if getattr(block, "type", None) == "text":  # Denk-Bloecke u. ae. zaehlen nur im Verbrauch
            parts.append(str(getattr(block, "text", "")))
    return "".join(parts)


# ------------------------------------------------------------------ Einsatzpruefung


def check_price_table(prices: PriceTable, routing: RoutingPolicy, today: date,
                      max_age_days: int = MAX_PRICE_AGE_DAYS) -> None:
    """Vor echtem Einsatz: Preise aktuell genug und fuer jedes Modell der Stufen vorhanden. Sonst PricingError."""
    age = (today - prices.as_of).days
    if age < 0 or age > max_age_days:
        raise PricingError("Preistabelle vom %s ist %d Tage alt (erlaubt: 0..%d) - erst gegen die aktuelle Preisliste "
                           "des Anbieters pruefen und neu datieren." % (prices.as_of, age, max_age_days))
    for target in routing.models.values():
        prices.get(target.provider, target.model)  # wirft PricingError, wenn ein Preis fehlt


def _sdk_client(api_key: str, timeout_s: float) -> Any:
    try:
        sdk = importlib.import_module("anthropic")
    except ImportError:
        raise ProviderUnavailable("Paket 'anthropic' ist nicht installiert (Abhaengigkeit erst mit B-004).") from None
    return sdk.Anthropic(api_key=api_key, max_retries=0, timeout=timeout_s)


def live_registry(
    prices: PriceTable,
    today: date,
    routing: RoutingPolicy = DEFAULT_ROUTING,
    env: Optional[Mapping[str, str]] = None,
    client_factory: Optional[ClientFactory] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ProviderRegistry:
    """Anbieter-Verzeichnis mit dem echten Claude-Anschluss - nur nach allen Pruefungen (siehe Moduldoku)."""
    env = os.environ if env is None else env
    if env.get(APPROVAL_ENV) != APPROVAL_TEXT:
        raise ProviderUnavailable("KI-Aufrufe nicht freigegeben (%s=%r fehlt, B-004)." % (APPROVAL_ENV, APPROVAL_TEXT))
    api_key = env.get(KEY_ENV, "").strip()
    if not api_key:
        raise ProviderUnavailable("Kein Zugang: %s ist nicht gesetzt." % KEY_ENV)
    if any(t.provider != PROVIDER for t in routing.models.values()):
        raise ProviderUnavailable("Die Modellstufen nennen einen anderen Anbieter als %r." % PROVIDER)
    check_price_table(prices, routing, today)
    client = (client_factory or _sdk_client)(api_key, timeout_s)
    registry = ProviderRegistry()
    registry.register(AnthropicProvider(client, timeout_s))
    return registry


__all__ = [
    "APPROVAL_ENV", "APPROVAL_TEXT", "AnthropicProvider", "DEFAULT_TIMEOUT_S", "KEY_ENV", "MAX_PRICE_AGE_DAYS",
    "ProviderUnavailable", "check_price_table", "live_registry", "usage_of",
]
