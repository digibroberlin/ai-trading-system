"""Gemeinsame Testhilfen fuer Marktueberwachung und Paper-Dienst: simulierter Boersen-Server (Drehbuch),
Bitget-Kerzen-Nachrichten und ein REST-Ersatz zum Nachladen. Kein Netz."""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading.backtest.data import DataQualityError, HistoricalDataSource, InstrumentSeries
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.domain.models import Candle
from trading.marketdata.stream import ConnectionLost

D = Decimal
UTC = timezone.utc
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)
BG = InstrumentId(Exchange.BITGET, MarketType.SPOT, "BTC-USDT")
CB = InstrumentId(Exchange.COINBASE, MarketType.SPOT, "BTC-USD")


def ms(moment):
    return int((moment - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1000)


def price(open_time):
    return D(27000) + D(int((open_time - T0).total_seconds()) // 60)


def candle(instrument, open_time, interval=60, volume=D(2)):
    p = price(open_time)
    return Candle(instrument, open_time, interval, p, p + 5, p - 5, p + 1, volume)


def push(open_time, sent, high_delta=5, volume="2"):
    p = price(open_time)
    row = [str(ms(open_time)), str(p), str(p + high_delta), str(p - 5), str(p + 1), volume, "0", "0"]
    return json.dumps({"action": "update", "arg": {"instType": "SPOT", "channel": "candle1m", "instId": "BTCUSDT"},
                       "data": [row], "ts": ms(sent)})


def at(sent, open_time, **kw):
    """Drehbuch-Eintrag: Stand der Kerze `open_time`, gesendet (und mit Server-Zeit) `sent`."""
    return sent, push(open_time, sent, **kw)


DROP = object()


class SimServer:
    """Simulierter Boersen-Server: liefert ein Drehbuch [(Zeitpunkt, Nachricht oder DROP)] zur jeweiligen Uhrzeit."""

    def __init__(self, clock, script, refuse=0):
        self.clock, self.script, self.refuse = clock, deque(script), refuse
        self.sent, self.connects, self.open = [], [], False
        self.answer_pings = True

    def connect(self, url):
        if self.refuse:
            self.refuse -= 1
            raise ConnectionLost("Server nicht erreichbar")
        self.connects.append((self.clock.now(), url))
        self.open = True

    def send(self, text):
        if not self.open:
            raise ConnectionLost("zu")
        self.sent.append((self.clock.now(), text))
        if text == "ping" and self.answer_pings:  # wie Bitget: sofort "pong"
            self.script.appendleft((self.clock.now(), "pong"))

    def recv(self, timeout_s):
        if not self.open:
            raise ConnectionLost("zu")
        if not self.script:
            self.clock.advance(timeout_s)
            return None
        when, item = self.script[0]
        wait = (when - self.clock.now()).total_seconds()
        if wait > timeout_s:
            self.clock.advance(timeout_s)
            return None
        self.clock.advance(max(wait, 0))
        self.script.popleft()
        if item is DROP:
            self.open = False
            raise ConnectionLost("Verbindung abgebrochen")
        return item

    def close(self):
        self.open = False


class Backfill(HistoricalDataSource):
    """REST-Ersatz (AP-14) als Attrappe: liefert die richtigen Kerzen; kann zunaechst scheitern."""

    exchange = Exchange.BITGET

    def __init__(self, fail=0):
        self.calls, self.fail = [], fail

    def load(self, instrument, interval_s, start, end):
        self.calls.append((start, end))
        if self.fail:
            self.fail -= 1
            raise DataQualityError(["noch nicht verfuegbar"])
        times = [start + MIN * i for i in range(int((end - start) / MIN))]
        return InstrumentSeries(instrument, interval_s, tuple(candle(instrument, t) for t in times))
