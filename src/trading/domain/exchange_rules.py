"""Boersenregeln je Instrument (AP-13): Preis- und Mengenschritt, Mindestmenge, Mindestwert, Hoechsthebel.
(Gebuehrenstufen nach Handelsvolumen: `execution.fees`.)

Wer sie nutzt:
  * SORA rundet die Menge auf den Mengenschritt AB und haelt Mindestmenge/-wert ein;
  * der unabhaengige Risikomanager lehnt jede Order ab, die eine Regel verletzt (eine echte Boerse taete es auch -
    im Paper-Betrieb soll nichts durchgehen, was spaeter scheitern wuerde);
  * Strategie-Vorschlaege bekommen Stop und Ziel auf dem Preisschritt - zu Ungunsten gerundet (Stop weiter weg, Ziel
    naeher dran).
Die echten Werte je Boerse liefern spaeter die Adapter (AP-14) aus den Instrument-Angaben der Boerse; hier stehen nur
die Regeln der erfundenen Test-Boerse. Fehlen fuer ein Instrument die Regeln, wird NICHT geraten: Order abgelehnt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal
from typing import Mapping, Optional, Tuple

from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.domain.models import Side
from trading.domain.numeric import is_finite_decimal, is_positive_decimal

D = Decimal


@dataclass(frozen=True)
class InstrumentRules:
    price_tick: Decimal  # kleinster Preisschritt
    quantity_step: Decimal  # kleinster Mengenschritt
    min_quantity: Decimal
    min_notional: Decimal  # Mindestwert einer Order (Menge x Kurs)
    max_leverage: Decimal  # Hoechsthebel der Boerse fuer dieses Instrument (Spot: 1)

    def __post_init__(self) -> None:
        for name in ("price_tick", "quantity_step", "min_quantity", "max_leverage"):
            if not is_positive_decimal(getattr(self, name)):
                raise ValueError("%s muss eine positive Decimal-Zahl sein." % name)
        if not is_finite_decimal(self.min_notional) or self.min_notional < 0:
            raise ValueError("min_notional muss eine nicht-negative Decimal-Zahl sein.")
        if self.min_quantity % self.quantity_step != 0:
            raise ValueError("min_quantity muss ein Vielfaches des Mengenschritts sein.")

    # ------------------------------------------------------------------ Menge

    def floor_quantity(self, quantity: Decimal) -> Decimal:
        return (quantity / self.quantity_step).to_integral_value(rounding=ROUND_DOWN) * self.quantity_step

    def ceil_quantity(self, quantity: Decimal) -> Decimal:
        return (quantity / self.quantity_step).to_integral_value(rounding=ROUND_CEILING) * self.quantity_step

    def minimum_quantity(self, price: Decimal) -> Decimal:
        """Kleinste erlaubte Menge bei diesem Kurs (Mindestmenge UND Mindestwert)."""
        return max(self.min_quantity, self.ceil_quantity(self.min_notional / price)) if price > 0 else self.min_quantity

    def on_quantity_step(self, quantity: Decimal) -> bool:
        return quantity % self.quantity_step == 0

    # ------------------------------------------------------------------ Preis

    def on_price_tick(self, price: Decimal) -> bool:
        return price % self.price_tick == 0

    def protective_stop(self, side: Side, stop: Decimal) -> Decimal:
        """Stop auf den Preisschritt - WEITER weg vom Einstieg (Long: ab-, Short: aufrunden)."""
        rounding = ROUND_FLOOR if side is Side.LONG else ROUND_CEILING
        return (stop / self.price_tick).to_integral_value(rounding=rounding) * self.price_tick

    def conservative_target(self, side: Side, target: Decimal) -> Decimal:
        """Ziel auf den Preisschritt - NAEHER zum Einstieg (Long: ab-, Short: aufrunden)."""
        rounding = ROUND_FLOOR if side is Side.LONG else ROUND_CEILING
        return (target / self.price_tick).to_integral_value(rounding=rounding) * self.price_tick


@dataclass(frozen=True)
class ExchangeRules:
    """Regelwerk: ausdrueckliche Regeln je Instrument, sonst die Vorgabe je (Boerse, Marktart). Kein Treffer -> None."""

    instruments: Mapping[InstrumentId, InstrumentRules] = field(default_factory=dict)
    defaults: Mapping[Tuple[Exchange, MarketType], InstrumentRules] = field(default_factory=dict)

    def for_instrument(self, instrument: InstrumentId) -> Optional[InstrumentRules]:
        found = self.instruments.get(instrument)
        if found is not None:
            return found
        return self.defaults.get((instrument.exchange, instrument.market_type))


SYNTHETIC_RULES = ExchangeRules(defaults={
    (Exchange.SYNTHETIC, MarketType.SPOT): InstrumentRules(D("0.01"), D("0.0001"), D("0.0001"), D("10"), D("1")),
    (Exchange.SYNTHETIC, MarketType.FUTURES): InstrumentRules(D("0.01"), D("0.0001"), D("0.0001"), D("5"), D("20")),
})


__all__ = ["ExchangeRules", "InstrumentRules", "SYNTHETIC_RULES"]
