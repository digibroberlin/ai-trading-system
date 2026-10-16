"""Bitget: oeffentlicher WebSocket-Kanal fuer Kerzen (AP-15) - Protokoll ohne Netz.

Adresse `wss://ws.bitget.com/v2/ws/public`. Abo je Instrument:
  `{"op": "subscribe", "args": [{"instType": "SPOT", "channel": "candle1H", "instId": "BTCUSDT"}]}`
  (Futures: `instType` "USDT-FUTURES"). Antworten: `{"event": "subscribe", ...}` (Bestaetigung), `{"event": "error",
  "code": ..., "msg": ...}` (Fehler), Daten `{"action": "snapshot"|"update", "arg": {...}, "data": [[ts, o, h, l, c,
  Basis-Volumen, ...]], "ts": <ms>}`. Nach dem Abo kommen auch die letzten abgeschlossenen Kerzen (fuellt kurze Luecken).
Lebenszeichen: Der Client sendet alle 30 s "ping", der Server antwortet "pong" (sonst trennt er nach ~2 min).
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from trading.domain.instrument import InstrumentId, MarketType
from trading.domain.models import Candle
from trading.marketdata.bitget import bitget_symbol
from trading.marketdata.history import decimal_text, from_seconds, int_seconds
from trading.marketdata.stream import (
    EPOCH,
    CandlePush,
    ExchangeStream,
    Heartbeat,
    Pong,
    StreamEvent,
    StreamProtocolError,
)

URL = "wss://ws.bitget.com/v2/ws/public"
CHANNELS: Dict[int, str] = {60: "candle1m", 300: "candle5m", 900: "candle15m", 1800: "candle30m", 3600: "candle1H",
                            14400: "candle4H"}
INST_TYPE = {MarketType.SPOT: "SPOT", MarketType.FUTURES: "USDT-FUTURES"}


class BitgetStream(ExchangeStream):
    url = URL
    keepalive_s = 30.0

    def __init__(self) -> None:
        self._by_key: Dict[Tuple[str, str], InstrumentId] = {}
        self._interval_s = 0

    def subscribe(self, instruments: Sequence[InstrumentId], interval_s: int) -> List[str]:
        if interval_s not in CHANNELS:
            raise ValueError("Intervall %r s gibt es bei Bitget nicht als Kerzen-Kanal." % interval_s)
        self._interval_s = interval_s
        args = []
        for instrument in instruments:
            key = (INST_TYPE[instrument.market_type], bitget_symbol(instrument))
            self._by_key[key] = instrument
            args.append({"instType": key[0], "channel": CHANNELS[interval_s], "instId": key[1]})
        return [json.dumps({"op": "subscribe", "args": args}, separators=(",", ":"))]

    def keepalive(self) -> Optional[str]:
        return "ping"

    def parse(self, text: str) -> List[StreamEvent]:
        if text == "pong":
            return [Pong()]
        try:
            message: Any = json.loads(text, parse_float=Decimal)
        except ValueError:
            raise StreamProtocolError("kein JSON") from None
        if not isinstance(message, Mapping):
            raise StreamProtocolError("unerwartete Nachricht")
        if message.get("event") == "error":
            raise StreamProtocolError("Bitget-Fehler %s: %s" % (message.get("code"), str(message.get("msg"))[:80]))
        if "event" in message:
            return []  # Bestaetigungen
        arg = message.get("arg")
        if not isinstance(arg, Mapping) or arg.get("channel") != CHANNELS.get(self._interval_s):
            raise StreamProtocolError("unbekannter Kanal")
        instrument = self._by_key.get((str(arg.get("instType")), str(arg.get("instId"))))
        rows = message.get("data")
        if instrument is None or not isinstance(rows, list):
            raise StreamProtocolError("unbekanntes Instrument oder keine Daten")
        events: List[StreamEvent] = []
        try:
            for row in sorted(rows, key=lambda r: int(r[0]) if isinstance(r, list) and r else 0):
                if not isinstance(row, list) or len(row) < 6:
                    raise ValueError("Form")
                values = [Decimal(decimal_text(v)) for v in row[1:6]]
                events.append(CandlePush(Candle(instrument, from_seconds(int_seconds(row[0], unit_ms=True)),
                                                self._interval_s, *values)))
            ts = message.get("ts")  # Sendezeit des Servers (ms): Zeit laeuft weiter, auch ohne neue Kerze
            if isinstance(ts, int) and not isinstance(ts, bool) and ts >= 0:
                events.append(Heartbeat(EPOCH + timedelta(milliseconds=ts), instrument))
        except (ValueError, TypeError) as exc:
            raise StreamProtocolError("Kerze unbrauchbar (%s)" % exc) from None
        return events


__all__ = ["BitgetStream", "CHANNELS", "URL"]
