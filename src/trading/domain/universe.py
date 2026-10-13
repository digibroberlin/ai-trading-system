"""Erlaubte Handelsinstrumente. Alles, was nicht ausdruecklich erlaubt ist, ist verboten."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, FrozenSet, Iterable

from trading.domain.instrument import (
    Exchange,
    InstrumentId,
    MarketType,
    is_instrument,
    is_well_formed_symbol,
)

__all__ = ["TradingUniverse", "is_well_formed_symbol"]


@dataclass(frozen=True)
class TradingUniverse:
    instruments: FrozenSet[InstrumentId] = frozenset()

    @staticmethod
    def of(
        spot: Iterable[str] = (),
        futures: Iterable[str] = (),
        exchange: Exchange = Exchange.SYNTHETIC,
    ) -> "TradingUniverse":
        """Bequeme Erzeugung fuer EINE Boerse. Ohne Angabe gilt die synthetische Test-Boerse."""
        items = [InstrumentId(exchange, MarketType.SPOT, s) for s in spot]
        items += [InstrumentId(exchange, MarketType.FUTURES, s) for s in futures]
        return TradingUniverse(frozenset(items))

    def __post_init__(self) -> None:
        for inst in self.instruments:
            if not is_instrument(inst):
                raise ValueError("Ungueltiges Instrument in der Whitelist: %r" % (inst,))

    def merged(self, other: "TradingUniverse") -> "TradingUniverse":
        return TradingUniverse(self.instruments | other.instruments)

    def allows(self, instrument: Any) -> bool:
        return is_instrument(instrument) and instrument in self.instruments
