"""Coinbase Exchange: historische Spot-Kerzen ueber die oeffentliche Schnittstelle (AP-14, nur lesend, ohne Schluessel).

`GET https://api.exchange.coinbase.com/products/{BASIS-QUOTE}/candles?granularity=..&start=..&end=..`
Antwort: Liste von `[Zeit (s), Tief, Hoch, Eroeffnung, Schluss, Volumen]`, neueste zuerst, hoechstens 300 je Abruf.
Coinbase laesst Intervalle ohne Handel aus - solche Luecken bleiben Luecken (Pruefung in `MarketDataset.build`).
Das kanonische Symbol ("BTC-USD") ist zugleich die Produkt-Kennung der Boerse.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, FrozenSet, List

from trading.backtest.data import DataQualityError
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.marketdata.daycache import Row
from trading.marketdata.history import ExchangeHistorySource, decimal_text, int_seconds

BASE_URL = "https://api.exchange.coinbase.com"
MAX_CANDLES = 300


class CoinbaseHistorySource(ExchangeHistorySource):
    exchange: ClassVar[Exchange] = Exchange.COINBASE
    markets: ClassVar[FrozenSet[MarketType]] = frozenset({MarketType.SPOT})
    intervals: ClassVar[FrozenSet[int]] = frozenset({60, 300, 900, 3600, 21600, 86400})

    def fetch_candles(self, instrument: InstrumentId, interval_s: int, start: datetime, end: datetime) -> List[Row]:
        url = "%s/products/%s/candles" % (BASE_URL, instrument.symbol)
        step = timedelta(seconds=interval_s * (MAX_CANDLES - 1))  # Ende einschliesslich: hoechstens 300 Kerzen
        rows: List[Row] = []
        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + step)
            data = self.get(url, {"granularity": str(interval_s), "start": _iso(cursor), "end": _iso(chunk_end)})
            rows += parse_candles(data, instrument.key)
            cursor = chunk_end
        return rows


def parse_candles(data: Any, key: str) -> List[Row]:
    if not isinstance(data, list):
        raise DataQualityError(["%s: Antwort ist keine Liste (%s)" % (key, _short(data))])
    rows: List[Row] = []
    issues: List[str] = []
    for i, item in enumerate(data):
        try:
            if not isinstance(item, list) or len(item) != 6:
                raise ValueError("Form")
            t, low, high, opn, close, volume = item
            rows.append((str(int_seconds(t, unit_ms=False)), decimal_text(opn), decimal_text(high), decimal_text(low),
                         decimal_text(close), decimal_text(volume)))
        except ValueError as exc:
            issues.append("%s: Zeile %d unbrauchbar (%s)" % (key, i, exc))
    if issues:
        raise DataQualityError(issues)
    return rows


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short(data: Any) -> str:
    text = repr(data)
    return text if len(text) <= 120 else text[:117] + "..."


__all__ = ["CoinbaseHistorySource", "parse_candles"]
