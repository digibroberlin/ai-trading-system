"""Bitget: historische Kerzen (Spot und USDT-Futures) und Funding-Saetze ueber die oeffentliche Schnittstelle (AP-14).

Nur lesend, ohne Schluessel. Schnittstellen (Version 2):
  * Spot:    `GET /api/v2/spot/market/history-candles?symbol=BTCUSDT&granularity=1h&endTime=<ms>&limit=200`
  * Futures: `GET /api/v2/mix/market/history-candles?symbol=BTCUSDT&productType=USDT-FUTURES&granularity=1H&...`
    Zeilen: `[Zeit (ms), Eroeffnung, Hoch, Tief, Schluss, Basis-Volumen, ...]` (weitere Volumen-Spalten ignoriert).
  * Funding: `GET /api/v2/mix/market/history-fund-rate?symbol=..&productType=USDT-FUTURES&pageSize=100&pageNo=<n>`
    Eintraege `{"fundingRate": "...", "fundingTime": "<ms>"}`, neueste zuerst.
Antworten kommen als `{"code": "00000", "msg": "success", "data": ...}`; jeder andere Code ist ein Fehler.
Kanonisch "BTC-USDT" <-> Bitget "BTCUSDT". Unterstuetzt werden nur Intervalle bis 4 h: groessere Bitget-Kerzen beginnen
je nach Art zu UTC+8 und laegen nicht auf dem UTC-Raster.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping

from trading.backtest.data import DataQualityError
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.marketdata.daycache import Row
from trading.marketdata.history import MAX_PAGES, ExchangeHistorySource, decimal_text, int_seconds, to_seconds
from trading.marketdata.public_http import FetchError

BASE_URL = "https://api.bitget.com"
PRODUCT_TYPE = "USDT-FUTURES"
MAX_CANDLES = 200
FUNDING_PAGE = 100
SPOT_GRANULARITY: Dict[int, str] = {60: "1min", 300: "5min", 900: "15min", 1800: "30min", 3600: "1h", 14400: "4h"}
FUTURES_GRANULARITY: Dict[int, str] = {60: "1m", 300: "5m", 900: "15m", 1800: "30m", 3600: "1H", 14400: "4H"}


def bitget_symbol(instrument: InstrumentId) -> str:
    return instrument.symbol.replace("-", "")


class BitgetHistorySource(ExchangeHistorySource):
    exchange: ClassVar[Exchange] = Exchange.BITGET
    markets: ClassVar[FrozenSet[MarketType]] = frozenset({MarketType.SPOT, MarketType.FUTURES})
    intervals: ClassVar[FrozenSet[int]] = frozenset(SPOT_GRANULARITY)

    def fetch_candles(self, instrument: InstrumentId, interval_s: int, start: datetime, end: datetime) -> List[Row]:
        futures = instrument.market_type is MarketType.FUTURES
        url = BASE_URL + ("/api/v2/mix/market/history-candles" if futures else "/api/v2/spot/market/history-candles")
        params = {"symbol": bitget_symbol(instrument), "limit": str(MAX_CANDLES),
                  "granularity": (FUTURES_GRANULARITY if futures else SPOT_GRANULARITY)[interval_s]}
        if futures:
            params["productType"] = PRODUCT_TYPE
        lo, cursor = to_seconds(start), to_seconds(end)
        rows: List[Row] = []
        for _ in range(MAX_PAGES):  # rueckwaerts in Seiten bis zum Beginn
            page = parse_candles(payload(self.get(url, dict(params, endTime=str(cursor * 1000))), instrument.key),
                                 instrument.key)
            rows += page
            earliest = min((int(r[0]) for r in page), default=None)
            if earliest is None or earliest <= lo:
                return rows
            if earliest >= cursor:
                raise DataQualityError(["%s: Bitget liefert keine aelteren Kerzen (Seite ohne Fortschritt)"
                                        % instrument.key])
            cursor = earliest
        raise DataQualityError(["%s: zu viele Seiten" % instrument.key])

    def fetch_funding(self, instrument: InstrumentId, start: datetime, end: datetime) -> List[Row]:
        url = BASE_URL + "/api/v2/mix/market/history-fund-rate"
        lo = to_seconds(start)
        rows: List[Row] = []
        for page_no in range(1, MAX_PAGES + 1):  # neueste zuerst: so weit zurueck, bis der Beginn erreicht ist
            data = payload(self.get(url, {"symbol": bitget_symbol(instrument), "productType": PRODUCT_TYPE,
                                          "pageSize": str(FUNDING_PAGE), "pageNo": str(page_no)}), instrument.key)
            page = parse_funding(data, instrument.key)
            rows += page
            if len(page) < FUNDING_PAGE or min(int(r[0]) for r in page) < lo:
                return rows
        raise DataQualityError(["%s: zu viele Funding-Seiten" % instrument.key])


def payload(response: Any, key: str) -> Any:
    if not isinstance(response, Mapping) or "data" not in response:
        raise FetchError("%s: unerwartete Antwort von Bitget" % key)
    if response.get("code") != "00000":
        raise FetchError("%s: Bitget-Fehler %s (%s)" % (key, response.get("code"), str(response.get("msg"))[:80]))
    return response["data"]


def parse_candles(data: Any, key: str) -> List[Row]:
    if not isinstance(data, list):
        raise DataQualityError(["%s: Kerzen-Daten sind keine Liste" % key])
    rows: List[Row] = []
    issues: List[str] = []
    for i, item in enumerate(data):
        try:
            if not isinstance(item, list) or len(item) < 6:
                raise ValueError("Form")
            rows.append((str(int_seconds(item[0], unit_ms=True)),) + tuple(decimal_text(v) for v in item[1:6]))
        except ValueError as exc:
            issues.append("%s: Kerze %d unbrauchbar (%s)" % (key, i, exc))
    if issues:
        raise DataQualityError(issues)
    return rows


def parse_funding(data: Any, key: str) -> List[Row]:
    if not isinstance(data, list):
        raise DataQualityError(["%s: Funding-Daten sind keine Liste" % key])
    rows: List[Row] = []
    issues: List[str] = []
    for i, item in enumerate(data):
        try:
            if not isinstance(item, Mapping):
                raise ValueError("Form")
            rows.append((str(int_seconds(item.get("fundingTime"), unit_ms=True)), decimal_text(item.get("fundingRate"))))
        except ValueError as exc:
            issues.append("%s: Funding %d unbrauchbar (%s)" % (key, i, exc))
    if issues:
        raise DataQualityError(issues)
    return rows


__all__ = ["BitgetHistorySource", "bitget_symbol", "parse_candles", "parse_funding", "payload"]
