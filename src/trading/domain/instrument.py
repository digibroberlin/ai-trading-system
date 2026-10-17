"""Eindeutige Identifikation eines Handelsinstruments.

Ein Instrument ist erst durch DREI Angaben eindeutig: Boerse, Marktart und Handelspaar.
"BTC-USD" als Spot bei Coinbase und "BTC-USD" als Futures bei Bitget sind verschiedene Instrumente
mit verschiedenen Kursen. Alle Kerzen, Snapshots, Setups, Positionen und Bewertungskurse tragen
deshalb eine `InstrumentId` - nie nur ein Symbol.

Symbole werden in EINER kanonischen Schreibweise gefuehrt ("BASIS-QUOTE", z. B. "BTC-USD").
Die spaeteren Boersen-Adapter uebersetzen die boerseneigene Schreibweise (z. B. "BTCUSDT") dorthin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class MarketType(str, Enum):
    SPOT = "spot"
    FUTURES = "futures"


class Exchange(str, Enum):
    SYNTHETIC = "synthetic"  # erfundene Daten fuer Tests und Backtests
    COINBASE = "coinbase"
    BITGET = "bitget"
    # Nur Herkunft historischer Forschungsdaten (AP-20: Bitstamp-1m-Kerzen aus einer oeffentlichen Kopie). Kein Adapter,
    # kein Betrieb: Der Paper-Dienst kennt keine Intervalle dafuer, die Datenbank laesst die Boerse nicht zu.
    BITSTAMP = "bitstamp"


_SYMBOL_FORMAT = re.compile(r"^[A-Z0-9]{2,12}-[A-Z]{2,6}$")


def is_well_formed_symbol(symbol: Any) -> bool:
    return isinstance(symbol, str) and _SYMBOL_FORMAT.match(symbol) is not None


@dataclass(frozen=True)
class InstrumentId:
    """Reiner Datentraeger ohne eigene Pruefung: Auch fehlerhafte Angaben aus fremden Quellen
    muessen darstellbar sein, damit sie mit einem Grund ABGELEHNT werden koennen (statt abzustuerzen).
    Ob ein Instrument formal gueltig ist, sagt `is_well_formed()`; ob es erlaubt ist, das Handelsuniversum."""

    exchange: Exchange
    market_type: MarketType
    symbol: str

    def is_well_formed(self) -> bool:
        return (
            isinstance(self.exchange, Exchange)
            and isinstance(self.market_type, MarketType)
            and is_well_formed_symbol(self.symbol)
        )

    @property
    def key(self) -> str:
        """Stabile Textform, z. B. "coinbase:spot:BTC-USD" (fuer Sortierung, Protokoll, Fingerabdruecke)."""
        return "%s:%s:%s" % (
            getattr(self.exchange, "value", self.exchange),
            getattr(self.market_type, "value", self.market_type),
            self.symbol,
        )

    def __str__(self) -> str:
        return self.key


def is_instrument(obj: Any) -> bool:
    return isinstance(obj, InstrumentId) and obj.is_well_formed()
