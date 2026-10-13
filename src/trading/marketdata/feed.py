"""Schnittstelle fuer Marktdatenquellen.

Spaetere Implementierungen (Coinbase, Bitget per WebSocket) duerfen NUR oeffentliche
Marktdaten LESEN. Sie brauchen keine API-Schluessel und duerfen keine Order-Funktion haben.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from trading.domain.models import MarketSnapshot


class MarketDataFeed(ABC):
    @abstractmethod
    def snapshots(self) -> Iterator[MarketSnapshot]:
        """Liefert fortlaufend Marktdaten (Zeitstempel = Zeit der Boerse)."""
