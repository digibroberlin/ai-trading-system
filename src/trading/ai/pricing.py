"""Preise der KI-Modelle und Kostenrechnung je Aufruf.

Grundsaetze:
* Preise sind DATEN, keine Konstanten im Code: eine datierte Tabelle (`PriceTable`) mit Quelle. Vor echtem Einsatz
  (Arbeitspaket KI-Anbieter, Freigabe B-004) wird sie gegen die aktuelle Preisliste des Anbieters geprueft.
* Geld ist `Decimal`, Kosten werden auf 8 Stellen AUFgerundet (zu Ungunsten des Systems, wie alle Kosten).
* Unbekanntes Modell oder fehlender Preis -> `PricingError`: Ohne bekannte Kosten gibt es keinen Aufruf (fail closed).
* Obergrenze vor dem Aufruf (`max_cost`): Ein Token ist bei den verwendeten Tokenisierern nie kuerzer als ein Byte,
  also ist die Zahl der Eingabe-Tokens hoechstens die Zahl der UTF-8-Bytes. Die Ausgabe ist durch `max_tokens` begrenzt
  (Denk-Tokens zaehlen dazu). Damit ist die Reservierung im Budget eine echte Obergrenze - das Budget kann nicht durch
  eine zu knappe Schaetzung ueberschritten werden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, Mapping, Optional

from trading.domain.numeric import is_finite_decimal
from trading.execution.costs import cost_up

MILLION = Decimal(1_000_000)
ZERO = Decimal(0)
# Fester Zuschlag je Anfrage fuer Rollen, Formatierung und Werkzeug-Definitionen, die nicht im Prompt-Text stehen.
REQUEST_OVERHEAD_TOKENS = 512


class PricingError(ValueError):
    """Kosten nicht bestimmbar (unbekanntes Modell, ungueltiger Preis, ungueltige Token-Zahl)."""


@dataclass(frozen=True)
class TokenUsage:
    """Verbrauch eines Aufrufs, wie ihn der Anbieter meldet (Anthropic: usage.*)."""

    input_tokens: int = 0  # Eingabe ohne Cache
    output_tokens: int = 0  # Ausgabe inkl. Denk-Tokens
    cache_write_tokens: int = 0  # in den Cache geschrieben (teurer als normale Eingabe)
    cache_read_tokens: int = 0  # aus dem Cache gelesen (deutlich billiger)

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PricingError("Token-Zahl %s ungueltig: %r" % (name, value))

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_write_tokens + self.cache_read_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens,
            self.cache_write_tokens + other.cache_write_tokens, self.cache_read_tokens + other.cache_read_tokens,
        )


NO_USAGE = TokenUsage()


@dataclass(frozen=True)
class ModelPrice:
    """USD je 1 Million Tokens."""

    provider: str
    model: str
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_write_per_mtok: Decimal
    cache_read_per_mtok: Decimal

    def __post_init__(self) -> None:
        for name in ("input_per_mtok", "output_per_mtok", "cache_write_per_mtok", "cache_read_per_mtok"):
            value = getattr(self, name)
            if not is_finite_decimal(value) or value < 0:
                raise PricingError("Preis %s fuer %s ungueltig: %r" % (name, self.model, value))
        if not self.provider or not self.model:
            raise PricingError("Preis ohne Anbieter oder Modell.")

    def cost(self, usage: TokenUsage) -> Decimal:
        """Tatsaechliche Kosten eines Aufrufs, aufgerundet."""
        raw = (Decimal(usage.input_tokens) * self.input_per_mtok
               + Decimal(usage.output_tokens) * self.output_per_mtok
               + Decimal(usage.cache_write_tokens) * self.cache_write_per_mtok
               + Decimal(usage.cache_read_tokens) * self.cache_read_per_mtok) / MILLION
        return cost_up(raw)

    def max_cost(self, prompt_bytes: int, max_output_tokens: int) -> Decimal:
        """Hoechstmoegliche Kosten VOR dem Aufruf (Obergrenze, siehe Moduldoku)."""
        if prompt_bytes < 0 or max_output_tokens <= 0:
            raise PricingError("Obergrenze braucht Prompt-Laenge >= 0 und max_tokens > 0.")
        worst_input_price = max(self.input_per_mtok, self.cache_write_per_mtok)
        raw = (Decimal(prompt_bytes + REQUEST_OVERHEAD_TOKENS) * worst_input_price
               + Decimal(max_output_tokens) * self.output_per_mtok) / MILLION
        return cost_up(raw)


@dataclass(frozen=True)
class PriceTable:
    """Preise mehrerer Modelle mit Stand und Quelle."""

    as_of: date
    source: str
    prices: Mapping[str, ModelPrice]  # Schluessel: "anbieter/modell"

    @staticmethod
    def build(as_of: date, source: str, prices: Iterable[ModelPrice]) -> "PriceTable":
        table: Dict[str, ModelPrice] = {}
        for price in prices:
            key = price_key(price.provider, price.model)
            if key in table:
                raise PricingError("Preis doppelt: %s" % key)
            table[key] = price
        return PriceTable(as_of, source, dict(table))

    def get(self, provider: str, model: str) -> ModelPrice:
        price: Optional[ModelPrice] = self.prices.get(price_key(provider, model))
        if price is None:
            raise PricingError("Kein Preis fuer %s - ohne bekannte Kosten kein Aufruf." % price_key(provider, model))
        return price


def price_key(provider: str, model: str) -> str:
    return "%s/%s" % (provider, model)


def _anthropic(model: str, inp: str, out: str, cache_read: str) -> ModelPrice:
    # Cache-Schreiben (5 Minuten Haltezeit) kostet das 1,25-Fache der Eingabe.
    return ModelPrice("anthropic", model, Decimal(inp), Decimal(out), Decimal(inp) * Decimal("1.25"), Decimal(cache_read))


# Stand der Anthropic-Preisliste vom 2026-10-06 (Erstanbieter-API). Fuer Haiku 5.5 gilt der Preis bis 100 000
# Prompt-Tokens; darueber ist er hoeher - die Orchestrierung begrenzt Prompts deshalb weit darunter (siehe
# orchestrator.MAX_PROMPT_BYTES). VOR echtem Einsatz gegen die aktuelle Preisliste pruefen.
DEFAULT_PRICES = PriceTable.build(
    date(2026, 10, 6),
    "Anthropic-Preisliste (Erstanbieter-API), Stand 2026-10-06",
    [
        _anthropic("claude-haiku-5-5", "0.10", "0.50", "0.01"),
        _anthropic("claude-sonnet-5-5", "2.00", "10.00", "0.20"),
        _anthropic("claude-opus-5-5", "4.00", "20.00", "0.20"),
        ModelPrice("null", "none", ZERO, ZERO, ZERO, ZERO),  # Offline-Provider ohne KI
    ],
)

__all__ = [
    "DEFAULT_PRICES", "MILLION", "ModelPrice", "NO_USAGE", "PriceTable", "PricingError", "REQUEST_OVERHEAD_TOKENS",
    "TokenUsage", "price_key",
]
