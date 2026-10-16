"""Coinbase Exchange: oeffentlicher WebSocket-Strom fuer Handel und Herzschlag (AP-15) - Protokoll ohne Netz.

Adresse `wss://ws-feed.exchange.coinbase.com`. Abo: `{"type": "subscribe", "product_ids": [...], "channels":
["matches", "heartbeat"]}`. Coinbase hat dort keinen Kerzen-Kanal: Die Kerzen entstehen aus jedem Handel
(`match`, mit fortlaufender `trade_id` je Produkt). Der Herzschlag (`heartbeat`, jede Sekunde, mit `last_trade_id`)
schliesst Kerzen puenktlich und verraet verpasste Handel. `last_match` nach dem Abo ist der letzte Handel VOR der
Verbindung: Er setzt nur den Ausgangspunkt der Zaehlung. Fehler kommen als `{"type": "error", "message": ...}`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Sequence

from trading.domain.instrument import InstrumentId, MarketType
from trading.marketdata.history import decimal_text
from trading.marketdata.stream import ExchangeStream, Heartbeat, StreamEvent, StreamProtocolError, Trade

URL = "wss://ws-feed.exchange.coinbase.com"


def parse_time(text: Any) -> datetime:
    """'2026-09-01T12:00:00.123456Z' (auch ohne Bruchteile) -> UTC."""
    if not isinstance(text, str) or not text.endswith("Z"):
        raise ValueError("Zeit unbrauchbar: %r" % (text,))
    body = text[:-1]
    if "." in body:
        head, frac = body.split(".", 1)
        if not frac.isdigit():
            raise ValueError("Zeit unbrauchbar: %r" % (text,))
        body = "%s.%s" % (head, (frac + "000000")[:6])
        fmt = "%Y-%m-%dT%H:%M:%S.%f"
    else:
        fmt = "%Y-%m-%dT%H:%M:%S"
    return datetime.strptime(body, fmt).replace(tzinfo=timezone.utc)


def _trade_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("trade_id unbrauchbar: %r" % (value,))
    return value


class CoinbaseStream(ExchangeStream):
    url = URL

    def __init__(self) -> None:
        self._products: Dict[str, InstrumentId] = {}

    def subscribe(self, instruments: Sequence[InstrumentId], interval_s: int) -> List[str]:
        if any(i.market_type is not MarketType.SPOT for i in instruments):
            raise ValueError("Coinbase Exchange: nur Spot.")
        self._products = {i.symbol: i for i in instruments}
        return [json.dumps({"type": "subscribe", "product_ids": sorted(self._products),
                            "channels": ["matches", "heartbeat"]}, separators=(",", ":"))]

    def parse(self, text: str) -> List[StreamEvent]:
        try:
            message: Any = json.loads(text, parse_float=Decimal)
        except ValueError:
            raise StreamProtocolError("kein JSON") from None
        if not isinstance(message, Mapping):
            raise StreamProtocolError("unerwartete Nachricht")
        kind = message.get("type")
        if kind == "error":
            raise StreamProtocolError("Coinbase-Fehler: %s" % str(message.get("message"))[:80])
        if kind == "subscriptions":
            return []
        if kind not in ("match", "last_match", "heartbeat"):
            raise StreamProtocolError("unbekannter Typ %r" % (kind,))
        instrument = self._products.get(str(message.get("product_id")))
        if instrument is None:
            raise StreamProtocolError("unbekanntes Produkt")
        try:
            when = parse_time(message.get("time"))
            if kind == "heartbeat":
                return [Heartbeat(when, instrument, _trade_id(message.get("last_trade_id")))]
            trade_id = _trade_id(message.get("trade_id"))
            if kind == "last_match":  # vor der Verbindung: nur Ausgangspunkt der Zaehlung
                return [Heartbeat(when, instrument, trade_id)]
            price, size = Decimal(decimal_text(message.get("price"))), Decimal(decimal_text(message.get("size")))
            if price <= 0 or size <= 0:
                raise ValueError("Preis/Menge nicht positiv")
            return [Trade(instrument, when, price, size, trade_id)]
        except ValueError as exc:
            raise StreamProtocolError("%s unbrauchbar (%s)" % (kind, exc)) from None


__all__ = ["CoinbaseStream", "URL", "parse_time"]
