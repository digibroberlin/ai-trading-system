"""Handelskosten: Gebuehren, Slippage, Funding.

Alle Standardwerte sind vorsichtige ANNAHMEN (keine echten Boersengebuehren). Vor dem
Aussagekraeftigen Backtest muessen sie durch die realen Tarife von Coinbase/Bitget ersetzt
werden. Lieber zu hohe Kosten annehmen als zu niedrige.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional

from trading.domain.models import MarketSnapshot, Side

FUNDING_HOURS_UTC = (0, 8, 16)  # uebliche 8-Stunden-Intervalle bei Perpetual Futures


@dataclass(frozen=True)
class CostModel:
    taker_fee: Decimal = Decimal("0.0006")  # 0,06 %
    maker_fee: Decimal = Decimal("0.0002")  # 0,02 %
    slippage_bps: Decimal = Decimal("2")  # Einstieg per Market-Order
    stop_slippage_bps: Decimal = Decimal("5")  # Stop-Loss wird zur Market-Order, oft ungünstiger

    def fee(self, notional: Decimal, taker: bool = True) -> Decimal:
        return notional * (self.taker_fee if taker else self.maker_fee)

    def entry_fill_price(self, side: Side, snapshot: MarketSnapshot) -> Decimal:
        """Long kauft zum Ask plus Slippage, Short verkauft zum Bid minus Slippage."""
        slip = self.slippage_bps / 10000
        if side is Side.LONG:
            return snapshot.ask * (1 + slip)
        return snapshot.bid * (1 - slip)

    def stop_exit_price(self, side: Side, raw_stop_price: Decimal) -> Decimal:
        """Stop-Ausstieg mit Slippage immer zum Nachteil des Traders."""
        slip = self.stop_slippage_bps / 10000
        if side is Side.LONG:
            return raw_stop_price * (1 - slip)
        return raw_stop_price * (1 + slip)

    def worst_case_loss(
        self,
        side: Side,
        quantity: Decimal,
        entry_price: Decimal,
        stop_price: Decimal,
        funding_rate: Optional[Decimal] = None,
    ) -> Decimal:
        """Hoechstverlust bei Stop-Ausloesung: Kursverlust + beide Gebuehren + Stop-Slippage
        (+ eine Funding-Zahlung als Puffer bei Futures). Linear in `quantity`."""
        exit_price = self.stop_exit_price(side, stop_price)
        price_loss = side.sign * (entry_price - exit_price) * quantity
        fees = self.fee(quantity * entry_price) + self.fee(quantity * exit_price)
        funding = Decimal(0)
        if funding_rate is not None:
            funding = abs(funding_rate) * quantity * entry_price
        return price_loss + fees + funding


def funding_times_between(start: datetime, end: datetime) -> List[datetime]:
    """Alle Funding-Zeitpunkte im Intervall (start, end]."""
    times = []  # type: List[datetime]
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        for hour in FUNDING_HOURS_UTC:
            t = day + timedelta(hours=hour)
            if start < t <= end:
                times.append(t)
        day += timedelta(days=1)
    return times
