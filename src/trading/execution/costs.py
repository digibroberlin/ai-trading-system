"""Handelskosten: Gebuehren, Slippage, Funding - und die Rundung von Geldbetraegen.

Alle Standardwerte sind vorsichtige ANNAHMEN (keine echten Boersengebuehren). Vor einem
aussagekraeftigen Backtest muessen sie durch die realen Tarife von Coinbase/Bitget ersetzt
werden. Lieber zu hohe Kosten annehmen als zu niedrige.

Rundung: Geldbetraege werden auf 8 Nachkommastellen festgelegt - Kosten und Verluste immer AUF,
Gewinne immer AB (also stets zu Ungunsten des Haendlers). So bleiben Betraege exakt reproduzierbar,
und die Buchhaltung geht ohne Rundungsreste auf.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Optional

from trading.domain.models import MarketSnapshot, Side

MONEY_QUANTUM = Decimal("0.00000001")


def cost_up(amount: Decimal) -> Decimal:
    """Kosten/Verluste: zur naechsten Stelle AUFrunden (auch bei negativen Betraegen in Richtung +unendlich)."""
    return amount.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING)


def gain_down(amount: Decimal) -> Decimal:
    """Gewinne/Erloese: ABrunden (in Richtung -unendlich)."""
    return amount.quantize(MONEY_QUANTUM, rounding=ROUND_FLOOR)


@dataclass(frozen=True)
class FillQuote:
    """Was eine Market-Order JETZT kosten wuerde - gemeinsame Grundlage von SORA, Risikomanager und Executor."""

    fill_price: Decimal  # erwarteter Ausfuehrungskurs inkl. Spread und Slippage
    mark_price: Decimal  # Bewertungskurs (Mitte zwischen Bid und Ask)
    funding_rate: Optional[Decimal]  # nur Futures


@dataclass(frozen=True)
class CostModel:
    taker_fee: Decimal = Decimal("0.0006")  # 0,06 %
    maker_fee: Decimal = Decimal("0.0002")  # 0,02 %
    slippage_bps: Decimal = Decimal("2")  # Market-Order (Einstieg, Ausstieg auf Signal)
    stop_slippage_bps: Decimal = Decimal("5")  # Stop-Loss wird zur Market-Order, oft unguenstiger
    funding_buffer_intervals: int = 1  # so viele Funding-Zahlungen werden im geplanten Risiko vorgehalten

    def __post_init__(self) -> None:
        for name in ("taker_fee", "maker_fee", "slippage_bps", "stop_slippage_bps"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("%s muss eine nicht-negative Decimal-Zahl sein." % name)
        if not isinstance(self.funding_buffer_intervals, int) or self.funding_buffer_intervals < 1:
            raise ValueError("funding_buffer_intervals muss eine ganze Zahl >= 1 sein.")

    def fee(self, notional: Decimal, taker: bool = True) -> Decimal:
        return cost_up(notional * (self.taker_fee if taker else self.maker_fee))

    # ------------------------------------------------------------------ Ausfuehrungskurse

    def entry_fill_price(self, side: Side, snapshot: MarketSnapshot) -> Decimal:
        """Long kauft zum Ask plus Slippage, Short verkauft zum Bid minus Slippage."""
        slip = self.slippage_bps / 10000
        if side is Side.LONG:
            return snapshot.ask * (1 + slip)
        return snapshot.bid * (1 - slip)

    def market_exit_price(self, side: Side, snapshot: MarketSnapshot) -> Decimal:
        """Schliessung per Market-Order: Long verkauft zum Bid minus Slippage, Short kauft zum Ask plus Slippage."""
        slip = self.slippage_bps / 10000
        if side is Side.LONG:
            return snapshot.bid * (1 - slip)
        return snapshot.ask * (1 + slip)

    def stop_exit_price(self, side: Side, raw_stop_price: Decimal) -> Decimal:
        """Stop-Ausstieg mit Slippage immer zum Nachteil des Traders."""
        slip = self.stop_slippage_bps / 10000
        if side is Side.LONG:
            return raw_stop_price * (1 - slip)
        return raw_stop_price * (1 + slip)

    def quote(self, side: Side, snapshot: MarketSnapshot) -> FillQuote:
        return FillQuote(self.entry_fill_price(side, snapshot), snapshot.mid, snapshot.funding_rate)

    # ------------------------------------------------------------------ geplanter Hoechstverlust

    def worst_case_loss(
        self,
        side: Side,
        quantity: Decimal,
        entry_price: Decimal,
        stop_price: Decimal,
        funding_rate: Optional[Decimal] = None,
    ) -> Decimal:
        """Geplanter Hoechstverlust bei Stop-Ausloesung: Kursverlust (inkl. Stop-Slippage)
        + Einstiegs- und Ausstiegsgebuehr (+ Funding-Puffer bei Futures).

        Die Summe wird aus denselben einzeln gerundeten Bestandteilen gebildet, die der Executor
        spaeter tatsaechlich bucht. Ohne Kursluecke ist der reale Verlust daher exakt gleich."""
        exit_price = self.stop_exit_price(side, stop_price)
        price_loss = cost_up(side.sign * (entry_price - exit_price) * quantity)
        fees = self.fee(quantity * entry_price) + self.fee(quantity * exit_price)
        funding = Decimal(0)
        if funding_rate is not None:
            funding = cost_up(abs(funding_rate) * quantity * entry_price * self.funding_buffer_intervals)
        return price_loss + fees + funding
