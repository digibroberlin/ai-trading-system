"""Erlaubte Handelspaare. Alles, was nicht ausdruecklich erlaubt ist, ist verboten."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, FrozenSet, Iterable

from trading.domain.models import MarketType

# Format "BASE-QUOTE", nur Grossbuchstaben/Ziffern, z. B. BTC-USD. Verhindert Muell-Eingaben.
_SYMBOL_FORMAT = re.compile(r"^[A-Z0-9]{2,12}-[A-Z]{2,6}$")


def is_well_formed_symbol(symbol: Any) -> bool:
    return isinstance(symbol, str) and _SYMBOL_FORMAT.match(symbol) is not None


@dataclass(frozen=True)
class TradingUniverse:
    spot: FrozenSet[str] = frozenset()
    futures: FrozenSet[str] = frozenset()

    @staticmethod
    def of(spot: Iterable[str] = (), futures: Iterable[str] = ()) -> "TradingUniverse":
        return TradingUniverse(frozenset(spot), frozenset(futures))

    def __post_init__(self) -> None:
        for sym in self.spot | self.futures:
            if not is_well_formed_symbol(sym):
                raise ValueError("Ungueltiges Symbol in der Whitelist: %r" % (sym,))

    def allows(self, symbol: Any, market_type: Any) -> bool:
        if not is_well_formed_symbol(symbol):
            return False
        if market_type is MarketType.SPOT:
            return symbol in self.spot
        if market_type is MarketType.FUTURES:
            return symbol in self.futures
        return False
