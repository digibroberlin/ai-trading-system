"""Marktueberwachung ueber WebSocket (AP-15, D-026): Wiederverbindung, Lueckenerkennung, Herzschlag, Not-Aus bei
veralteten Daten - mit einem simulierten Server (ohne Netz). Der echte WebSocket-Zugang: tests/test_public_ws.py."""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.backtest.data import DataQualityError, HistoricalDataSource, InstrumentSeries
from trading.clock import FixedClock
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.domain.models import Candle
from trading.marketdata.bitget_ws import BitgetStream
from trading.marketdata.coinbase_ws import CoinbaseStream, parse_time
from trading.marketdata.stream import (
    CandleBuilder,
    CandlePush,
    ConnectionLost,
    Gap,
    Heartbeat,
    MarketStreamMonitor,
    MonitorPolicy,
    StreamProtocolError,
    Trade,
)
from trading.safety.kill_switch import HaltReason, InMemoryKillSwitch

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


def monitor(script, protocol=None, instrument=BG, backfill=None, policy=MonitorPolicy(), refuse=0):
    clock = FixedClock(T0)
    server = SimServer(clock, script, refuse)
    switch = InMemoryKillSwitch(clock)
    mon = MarketStreamMonitor(protocol or BitgetStream(), server, [instrument], 60, clock, switch, backfill, policy,
                              sleep=clock.advance)
    return mon, server, switch, clock


def run_until(mon, clock, until):
    out = []
    for c in mon.candles(stop=lambda: clock.now() >= until):
        out.append(c)
    return out


# ------------------------------------------------------------------ Kerzen aus dem Strom


def test_pushes_become_closed_candles_in_order_and_skipped_minutes_become_gaps():
    b = CandleBuilder(BG, 60)
    assert b.on_push(candle(BG, T0)) == []
    assert b.on_push(candle(BG, T0, volume=D(3))) == []  # neuer Stand derselben Kerze
    assert b.on_push(candle(BG, T0 + MIN)) == [candle(BG, T0, volume=D(3))]  # letzter Stand gilt
    assert b.on_push(candle(BG, T0 + 4 * MIN)) == [candle(BG, T0 + MIN), Gap(T0 + 2 * MIN, T0 + 4 * MIN)]
    assert b.on_push(candle(BG, T0 + MIN)) == []  # alte Wiederholung (z. B. Abo-Schnappschuss)


def test_trades_build_candles_only_when_every_trade_was_seen():
    b = CandleBuilder(CB, 60)
    b.reconnected(T0 + timedelta(seconds=20))
    b.on_heartbeat(Heartbeat(T0 + timedelta(seconds=20), CB, 100))  # last_match: Ausgangspunkt
    b.on_trade(Trade(CB, T0 + timedelta(seconds=25), D(10), D(1), 101))
    out = b.on_trade(Trade(CB, T0 + MIN + timedelta(seconds=1), D(11), D(2), 102))
    assert out == [Gap(T0, T0 + MIN)]  # erste Minute nur zum Teil gesehen -> nachladen, nicht raten
    b.on_trade(Trade(CB, T0 + MIN + timedelta(seconds=5), D(13), D(1), 103))
    b.on_trade(Trade(CB, T0 + MIN + timedelta(seconds=9), D(9), D("0.5"), 103))  # Doppelt: ignoriert
    b.on_trade(Trade(CB, T0 + MIN + timedelta(seconds=9), D(12), D("0.5"), 104))
    out = b.on_heartbeat(Heartbeat(T0 + 2 * MIN, CB, 104))  # Herzschlag schliesst puenktlich
    assert out == [Candle(CB, T0 + MIN, 60, D(11), D(13), D(11), D(12), D("3.5"))]
    b.on_trade(Trade(CB, T0 + 2 * MIN + timedelta(seconds=3), D(12), D(1), 105))
    b.on_trade(Trade(CB, T0 + 2 * MIN + timedelta(seconds=4), D(12), D(1), 108))  # 106, 107 verpasst
    assert b.on_heartbeat(Heartbeat(T0 + 3 * MIN, CB, 108)) == [Gap(T0 + 2 * MIN, T0 + 3 * MIN)]
    b.on_trade(Trade(CB, T0 + 3 * MIN + timedelta(seconds=3), D(12), D(1), 109))
    b.on_heartbeat(Heartbeat(T0 + 3 * MIN + timedelta(seconds=30), CB, 111))  # Herzschlag kennt mehr Handel
    assert b.on_heartbeat(Heartbeat(T0 + 4 * MIN, CB, 111)) == [Gap(T0 + 3 * MIN, T0 + 4 * MIN)]
    b.on_trade(Trade(CB, T0 + 4 * MIN + timedelta(seconds=5), D(12), D(1), 112))
    b.reconnected(T0 + 4 * MIN + timedelta(seconds=30))  # Handel waehrend der Unterbrechung fehlt
    b.on_heartbeat(Heartbeat(T0 + 4 * MIN + timedelta(seconds=31), CB, 140))
    assert b.on_heartbeat(Heartbeat(T0 + 5 * MIN, CB, 140)) == [Gap(T0 + 4 * MIN, T0 + 5 * MIN)]


# ------------------------------------------------------------------ Monitor mit simuliertem Server


def test_monitor_delivers_checked_candles_and_keeps_the_connection_alive():
    script = [at(T0 + MIN * i + timedelta(seconds=s), T0 + MIN * i) for i in range(5) for s in (5, 40)]
    script.append((T0 + 5 * MIN + timedelta(seconds=2), "pong"))
    mon, server, switch, clock = monitor(script)
    out = run_until(mon, clock, T0 + 5 * MIN + timedelta(seconds=20))
    assert out == [candle(BG, T0 + MIN * i) for i in range(4)]  # die laufende fuenfte Kerze noch nicht
    assert json.loads(server.sent[0][1]) == {"op": "subscribe", "args": [
        {"instType": "SPOT", "channel": "candle1m", "instId": "BTCUSDT"}]}
    pings = [t for t, text in server.sent if text == "ping"]
    assert len(pings) >= 9 and all((b - a) >= timedelta(seconds=30) for a, b in zip(pings, pings[1:]))
    assert not switch.halted and mon.status.connects == 1 and mon.status.rejected == 0


def test_after_a_drop_it_reconnects_and_fills_the_gap_in_order_before_new_candles():
    script = [at(T0 + timedelta(seconds=5), T0), at(T0 + MIN + timedelta(seconds=5), T0 + MIN),
              (T0 + MIN + timedelta(seconds=10), DROP),
              at(T0 + 5 * MIN + timedelta(seconds=3), T0 + 5 * MIN),
              at(T0 + 6 * MIN + timedelta(seconds=3), T0 + 6 * MIN)]
    backfill = Backfill()  # 4 Minuten Ausfall: Not-Aus-Frist hier bewusst laenger (eigener Test unten)
    mon, server, switch, clock = monitor(script, backfill=backfill, policy=MonitorPolicy(halt_after_s=300))
    out = run_until(mon, clock, T0 + 7 * MIN)
    assert [c.open_time for c in out] == [T0 + MIN * i for i in range(6)]  # nichts fehlt, alles in Reihenfolge
    assert out == [candle(BG, T0 + MIN * i) for i in range(6)]
    assert backfill.calls == [(T0 + 2 * MIN, T0 + 5 * MIN)]
    assert mon.status.connects == 2 and mon.status.disconnects == 1 and mon.status.gaps_filled == 1
    assert server.connects[1][0] - (T0 + MIN + timedelta(seconds=10)) == timedelta(seconds=1)  # erst 1 s warten
    assert not switch.halted


def test_backoff_grows_while_the_server_is_unreachable():
    mon, server, switch, clock = monitor([], refuse=4)
    run_until(mon, clock, T0 + timedelta(seconds=40))
    tries = [text for text in mon.status.issues if "neuer Versuch" in text]
    assert [t.split("neuer Versuch in ")[1] for t in tries[:4]] == ["1 s", "2 s", "4 s", "8 s"]
    assert server.connects and server.connects[0][0] == T0 + timedelta(seconds=15)  # 1+2+4+8


def test_a_gap_that_cannot_be_filled_is_reported_and_never_invented():
    script = [at(T0 + timedelta(seconds=5), T0), at(T0 + 3 * MIN + timedelta(seconds=5), T0 + 3 * MIN),
              at(T0 + 4 * MIN + timedelta(seconds=5), T0 + 4 * MIN)]
    mon, server, switch, clock = monitor(script, backfill=Backfill(fail=5))
    out = run_until(mon, clock, T0 + 6 * MIN)
    assert [c.open_time for c in out] == [T0, T0 + 3 * MIN]  # Luecke 1..3 bleibt Luecke
    assert mon.status.gaps_given_up == 1 and any("nicht nachladbar" in i for i in mon.status.issues)


def test_later_candles_wait_while_a_gap_is_still_being_filled():
    script = [at(T0 + timedelta(seconds=5), T0), at(T0 + 2 * MIN + timedelta(seconds=1), T0 + 2 * MIN),
              at(T0 + 3 * MIN + timedelta(seconds=1), T0 + 3 * MIN)]
    backfill = Backfill(fail=1)  # erster Versuch 10 s nach der Luecke scheitert, zweiter 10 s spaeter gelingt
    mon, server, switch, clock = monitor(script, backfill=backfill)
    out = run_until(mon, clock, T0 + 2 * MIN + timedelta(seconds=15))
    assert [c.open_time for c in out] == [T0] and len(backfill.calls) == 1  # Minute 2 wartet auf Minute 1
    out += run_until(mon, clock, T0 + 3 * MIN + timedelta(seconds=5))
    assert [c.open_time for c in out] == [T0, T0 + MIN, T0 + 2 * MIN]
    assert len(backfill.calls) == 2


def test_silence_leads_to_reconnect_and_stale_data_to_the_kill_switch():
    script = [at(T0 + timedelta(seconds=5), T0)]
    policy = MonitorPolicy(reconnect_after_s=40, halt_after_s=120)
    mon, server, switch, clock = monitor(script, policy=policy)
    server.answer_pings = False  # Server haengt: nicht einmal "pong"
    run_until(mon, clock, T0 + timedelta(seconds=100))
    assert mon.status.disconnects >= 1 and len(server.connects) >= 2 and not switch.halted
    run_until(mon, clock, T0 + timedelta(seconds=126))
    status = switch.status()
    assert status.halted and status.reason is HaltReason.DATA_ERROR and "veraltet" in status.note
    run_until(mon, clock, T0 + timedelta(seconds=400))
    assert switch.status().since == status.since  # einmal ausgeloest; bleibt bis zur Freigabe durch einen Menschen


def test_future_timestamps_or_broken_candles_trip_the_kill_switch():
    future = push(T0 + 10 * MIN, sent=T0)  # Kerze "in 10 Minuten"
    mon, server, switch, clock = monitor([(T0 + timedelta(seconds=5), future)])
    run_until(mon, clock, T0 + timedelta(seconds=10))
    assert switch.halted and "Zukunft" in switch.status().note
    broken = push(T0 + MIN, T0 + MIN + timedelta(seconds=5), high_delta=-20)  # Hoch unter dem Tief
    backfill = Backfill()
    mon, server, switch, clock = monitor([(T0 + MIN + timedelta(seconds=5), broken),
                                          at(T0 + 2 * MIN + timedelta(seconds=5), T0 + 2 * MIN)], backfill=backfill)
    out = run_until(mon, clock, T0 + 3 * MIN)
    assert switch.halted and "fehlerhafte Kerze" in switch.status().note
    assert out == [candle(BG, T0 + MIN)] and backfill.calls == [(T0 + MIN, T0 + 2 * MIN)]  # ersetzt, nicht benutzt


def test_unusable_messages_are_counted_and_skipped():
    script = [(T0 + timedelta(seconds=1), "kein json"),
              (T0 + timedelta(seconds=2), json.dumps({"event": "error", "code": 30001, "msg": "instId does not exist"})),
              (T0 + timedelta(seconds=3), push(T0, T0 + timedelta(seconds=3)).replace("BTCUSDT", "ETHUSDT")),
              at(T0 + timedelta(seconds=5), T0), at(T0 + MIN + timedelta(seconds=5), T0 + MIN)]
    mon, server, switch, clock = monitor(script)
    out = run_until(mon, clock, T0 + MIN + timedelta(seconds=10))
    assert out == [candle(BG, T0)] and mon.status.rejected == 3 and not switch.halted


def test_coinbase_trades_become_candles_with_the_partial_first_minute_reloaded():
    def match(t, trade_id, p, size="1"):
        return json.dumps({"type": "match", "trade_id": trade_id, "sequence": 1, "time": t.strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"), "product_id": "BTC-USD", "size": size, "price": str(p), "side": "buy"})

    def beat(t, last):
        return json.dumps({"type": "heartbeat", "sequence": 1, "last_trade_id": last, "product_id": "BTC-USD",
                           "time": t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")})

    s = timedelta(seconds=1)
    beats = [(T0 + 20 * s + 10 * s * i, beat(T0 + 20 * s + 10 * s * i, 50 if i < 1 else 51 if i < 5 else 53))
             for i in range(11)]
    script = [(T0 + 20 * s, json.dumps({"type": "subscriptions", "channels": []})),
              (T0 + 20 * s, json.dumps({"type": "last_match", "trade_id": 50, "sequence": 1, "product_id": "BTC-USD",
                                        "time": "2026-09-01T12:00:19.000000Z", "size": "1", "price": "27000"})),
              (T0 + 30 * s, match(T0 + 30 * s, 51, 27000)), (T0 + MIN + 2 * s, match(T0 + MIN + 2 * s, 52, 27010)),
              (T0 + MIN + 9 * s, match(T0 + MIN + 9 * s, 53, 27020, "0.5"))]
    script = sorted(script + beats, key=lambda item: item[0])  # Coinbase: Herzschlag laufend
    backfill = Backfill()
    backfill.exchange = Exchange.COINBASE
    # erster Verbindungsversuch scheitert -> verbunden erst 12:00:01: die erste Minute ist nur teilweise beobachtet
    mon, server, switch, clock = monitor(script, CoinbaseStream(), CB, backfill, refuse=1)
    out = run_until(mon, clock, T0 + 2 * MIN + 15 * s)
    assert backfill.calls == [(T0, T0 + MIN)]
    assert out == [candle(CB, T0), Candle(CB, T0 + MIN, 60, D(27010), D(27020), D(27010), D(27020), D("1.5"))]
    assert json.loads(server.sent[0][1]) == {"type": "subscribe", "product_ids": ["BTC-USD"],
                                             "channels": ["matches", "heartbeat"]}


def test_monitor_candles_feed_the_runtime_engine():
    from helpers import NO_SLIP  # noqa: F401 - stellt sicher, dass Testhilfen geladen sind
    from trading.agents.aki import AkiAgent
    from trading.agents.rin_rules import TradingUniverse
    from trading.audit.event_log import InMemoryEventLog
    from trading.execution.paper_executor import PaperExecutor
    from trading.runtime.engine import RuntimeEngine
    from trading.safety.data_validator import SnapshotValidator
    from trading.safety.limits import DEFAULT_LIMITS
    from trading.safety.risk_manager import RiskManager
    from trading.strategies import BreakoutStrategy

    script = [at(T0 + MIN * i + timedelta(seconds=5), T0 + MIN * i) for i in range(6)]
    mon, server, switch, clock = monitor(script)
    log = InMemoryEventLog(clock)
    executor = PaperExecutor(D(10000), RiskManager(DEFAULT_LIMITS), SnapshotValidator(DEFAULT_LIMITS, clock), log,
                             clock)
    engine = RuntimeEngine(BreakoutStrategy(), executor, AkiAgent(log), clock, TradingUniverse(frozenset({BG})))
    for c in mon.candles(stop=lambda: clock.now() >= T0 + 6 * MIN):
        engine.process(c)
    assert engine.stats.candles == 5 and engine.stats.rejected_candles == 0 and engine.stats.errors == 0


# ------------------------------------------------------------------ Protokolle


def test_bitget_protocol_messages():
    stream = BitgetStream()
    stream.subscribe([BG, InstrumentId(Exchange.BITGET, MarketType.FUTURES, "ETH-USDT")], 3600)
    snapshot = json.dumps({"action": "snapshot", "arg": {"instType": "USDT-FUTURES", "channel": "candle1H",
                                                          "instId": "ETHUSDT"},
                           "data": [["1756728000000", "2", "3", "1", "2.5", "10", "0"],
                                    ["1756724400000", "1", "2", "1", "2", "5", "0"]], "ts": 1756728001500})
    events = stream.parse(snapshot)
    assert [type(e) for e in events] == [CandlePush, CandlePush, Heartbeat]
    assert events[0].candle.open_time < events[1].candle.open_time  # aufsteigend sortiert
    assert events[1].candle.close == D("2.5") and events[2].time.microsecond == 500000
    assert stream.parse('{"event":"subscribe","arg":{}}') == [] and stream.keepalive() == "ping"
    for bad in ('{"event":"error","code":30001,"msg":"x"}', "[]", '{"arg":{"channel":"ticker"},"data":[]}',
                snapshot.replace('"2.5"', '"abc"')):
        with pytest.raises(StreamProtocolError):
            stream.parse(bad)
    with pytest.raises(ValueError):
        BitgetStream().subscribe([BG], 86400)


def test_coinbase_protocol_messages():
    stream = CoinbaseStream()
    stream.subscribe([CB], 60)
    assert parse_time("2026-09-01T12:00:00Z") == T0
    assert parse_time("2026-09-01T12:00:00.5Z") == T0 + timedelta(milliseconds=500)
    trade = stream.parse(json.dumps({"type": "match", "trade_id": 7, "time": "2026-09-01T12:00:01.000001Z",
                                     "product_id": "BTC-USD", "size": "0.01", "price": "27000.12"}))[0]
    assert trade == Trade(CB, T0 + timedelta(seconds=1, microseconds=1), D("27000.12"), D("0.01"), 7)
    for bad in ('{"type":"error","message":"Failed to subscribe"}', '{"type":"ticker"}',
                '{"type":"match","trade_id":true,"time":"2026-09-01T12:00:00Z","product_id":"BTC-USD"}',
                '{"type":"match","trade_id":1,"time":"2026-09-01 12:00","product_id":"BTC-USD","size":"1","price":"1"}',
                '{"type":"match","trade_id":1,"time":"2026-09-01T12:00:00Z","product_id":"BTC-USD","size":"1",'
                '"price":"-1"}'):
        with pytest.raises(StreamProtocolError):
            stream.parse(bad)
    with pytest.raises(ValueError):
        CoinbaseStream().subscribe([InstrumentId(Exchange.COINBASE, MarketType.FUTURES, "BTC-USD")], 60)


def test_watch_command_shows_candles_and_the_state_without_trading():
    from trading.marketdata.watch import main

    clock = FixedClock(T0)
    server = SimServer(clock, [at(T0 + MIN * i + timedelta(seconds=5), T0 + MIN * i) for i in range(4)])
    lines = []
    code = main(["--exchange", "bitget", "--symbol", "BTC-USDT", "--minutes", "3.5"], transport=server,
                http=lambda url, params: pytest.fail("kein Nachladen noetig"), clock=clock, sleep=clock.advance,
                out=lines.append)
    assert code == 0 and len([x for x in lines if "bitget:spot:BTC-USDT O 27" in x]) == 3
    assert lines[3].startswith("Zustand: 1 Verbindungen, 0 Abbrueche, 3 Kerzen")
