"""Laufende Marktueberwachung ueber WebSocket (AP-15, D-026) - nur lesend, oeffentliche Kanaele, ohne Schluessel.

Aufgabe: aus dem Datenstrom einer Boerse GESCHLOSSENE, gepruefte Kerzen machen (fuer die Laufzeit-Engine) und dabei
jeden Ausfall erkennen, statt ihn zu ueberdecken:
  * Wiederverbindung mit wachsender Wartezeit (1, 2, 4 ... hoechstens 60 s); jede Verbindung abonniert neu.
  * Luecken (nach einer Unterbrechung, eine Kerze, die erst mitten im Intervall beobachtet wurde, oder fehlende
    Folgenummern der Boerse): Die betroffenen Kerzen werden NICHT aus Bruchstuecken gebaut, sondern ueber die
    REST-Schnittstelle (AP-14) nachgeladen und IN REIHENFOLGE ausgegeben; spaetere Kerzen warten so lange. Gelingt das
    nicht, wird nichts erfunden: Die Luecke bleibt und wird gemeldet.
  * Herzschlag: Kommt `reconnect_after_s` lang keine Nachricht, wird neu verbunden; bleiben Marktdaten `halt_after_s`
    lang aus, loest der Monitor den Not-Aus aus (DATA_ERROR) - er bleibt bis zur bewussten Freigabe durch einen Menschen.
  * Pruefung: Kerzen gehen erst nach `candle_problems` hinaus; Zeitstempel aus der Zukunft oder fehlerhafte Kerzen der
    Boerse loesen - wie ueberall im System - den Not-Aus aus. Unbrauchbare Nachrichten werden verworfen und gezaehlt.
Boersen-Protokoll (`ExchangeStream`) und Verbindung (`Transport`) sind austauschbar: Tests benutzen einen simulierten
Server ohne Netz; der echte Zugang ist `public_ws.PublicWebSocket` (nur feste Adressen).
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Deque, Dict, Iterator, List, Optional, Protocol, Sequence, Tuple, Union

from trading.backtest.data import HistoricalDataSource
from trading.clock import Clock
from trading.domain.instrument import InstrumentId
from trading.domain.models import Candle
from trading.marketdata.candles import candle_problems
from trading.safety.kill_switch import HaltReason, KillSwitch

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
LOG = logging.getLogger("trading.marketdata.stream")
FUTURE_TOLERANCE = timedelta(seconds=5)
MAX_ISSUES = 50


class ConnectionLost(RuntimeError):
    """Verbindung weg oder nicht herstellbar."""


class StreamProtocolError(ValueError):
    """Nachricht der Boerse unbrauchbar oder Fehlermeldung der Boerse."""


# ------------------------------------------------------------------ Ereignisse aus dem Strom


@dataclass(frozen=True)
class Trade:
    instrument: InstrumentId
    time: datetime
    price: Decimal
    size: Decimal
    trade_id: Optional[int] = None  # fortlaufend je Instrument (Coinbase) -> Lueckenerkennung


@dataclass(frozen=True)
class CandlePush:
    """Stand einer Kerze, wie die Boerse ihn schickt (Bitget: laufende Kerze bei jeder Aenderung, nach dem Abo auch
    die letzten abgeschlossenen)."""

    candle: Candle


@dataclass(frozen=True)
class Heartbeat:
    time: datetime  # Zeit der Boerse
    instrument: Optional[InstrumentId] = None
    last_trade_id: Optional[int] = None


@dataclass(frozen=True)
class Pong:
    pass


StreamEvent = Union[Trade, CandlePush, Heartbeat, Pong]


class ExchangeStream(ABC):
    """Protokoll einer Boerse - reine Uebersetzung, kein Netz."""

    url: str = ""
    keepalive_s: float = 0.0  # > 0: so oft `keepalive()` senden (Bitget: "ping" alle 30 s)

    @abstractmethod
    def subscribe(self, instruments: Sequence[InstrumentId], interval_s: int) -> List[str]:
        """Abo-Nachrichten fuer eine frische Verbindung."""

    @abstractmethod
    def parse(self, text: str) -> List[StreamEvent]:
        """Nachricht -> Ereignisse. Unbrauchbares oder eine Fehlermeldung der Boerse -> StreamProtocolError."""

    def keepalive(self) -> Optional[str]:
        return None


class Transport(Protocol):
    def connect(self, url: str) -> None: ...

    def send(self, text: str) -> None: ...

    def recv(self, timeout_s: float) -> Optional[str]:
        """Naechste Textnachricht oder None nach `timeout_s` ohne Nachricht. ConnectionLost bei Abbruch."""

    def close(self) -> None: ...


# ------------------------------------------------------------------ Kerzen aus dem Strom


@dataclass(frozen=True)
class Gap:
    """Fehlende Kerzen [start, end) - werden ueber REST nachgeladen."""

    start: datetime
    end: datetime


def bucket(moment: datetime, interval_s: int) -> datetime:
    seconds = int((moment - EPOCH).total_seconds())
    return EPOCH + timedelta(seconds=seconds - seconds % interval_s)


class CandleBuilder:
    """Ein Instrument: macht aus Handel/Kerzen-Staenden abgeschlossene Kerzen oder Luecken, streng in Zeitreihenfolge."""

    def __init__(self, instrument: InstrumentId, interval_s: int) -> None:
        self.instrument = instrument
        self.interval = timedelta(seconds=interval_s)
        self.interval_s = interval_s
        self.forming: Optional[Candle] = None
        self.tainted = False  # laufende Kerze unvollstaendig beobachtet -> wird durch REST ersetzt
        self.last_trade_id: Optional[int] = None
        self.done_until: Optional[datetime] = None  # Ende der letzten ausgegebenen Kerze/Luecke
        self.watch_from: Optional[datetime] = None  # seit wann (diese Verbindung) jeder Handel gesehen wird
        self.from_trades = False  # Kerzen aus Einzelhandel (Coinbase) statt vollstaendiger Boersen-Staende (Bitget)

    def reconnected(self, now: datetime) -> List[Union[Candle, Gap]]:
        """Neue Verbindung: Eine aus Handel gebaute, laufende Kerze ist ab jetzt unvollstaendig (Handel waehrend der
        Unterbrechung fehlt). Boersen-Staende (Bitget) enthalten immer die ganze Kerze und bleiben gueltig."""
        self.watch_from = now
        self.last_trade_id = None
        if self.forming is not None and self.from_trades:
            self.tainted = True
        return []

    def on_push(self, candle: Candle) -> List[Union[Candle, Gap]]:
        self.from_trades = False
        out: List[Union[Candle, Gap]] = []
        if self.done_until is not None and candle.open_time < self.done_until:
            return out  # schon ausgegeben (Wiederholung nach dem Abo)
        if self.forming is None or candle.open_time > self.forming.open_time:
            if self.forming is not None:
                out += self._close()
            out += self._gap_until(candle.open_time)
            self.forming, self.tainted = candle, False
        elif candle.open_time == self.forming.open_time:
            self.forming = candle  # neuer Stand derselben Kerze
        return out

    def on_trade(self, trade: Trade) -> List[Union[Candle, Gap]]:
        self.from_trades = True
        out: List[Union[Candle, Gap]] = []
        if trade.trade_id is not None and self.last_trade_id is not None:
            if trade.trade_id <= self.last_trade_id:
                return out  # schon gesehen
            if trade.trade_id != self.last_trade_id + 1:
                self.tainted = self.forming is not None  # Handel verpasst
                if self.forming is None:
                    self.watch_from = trade.time  # erst ab hier vollstaendig
        if trade.trade_id is not None:
            self.last_trade_id = trade.trade_id
        start = bucket(trade.time, self.interval_s)
        if self.done_until is not None and start < self.done_until:
            return out
        if self.forming is not None and start > self.forming.open_time:
            out += self._close()
        if self.forming is None:
            out += self._gap_until(start)
            self.tainted = self.watch_from is None or self.watch_from > start
            self.forming = Candle(self.instrument, start, self.interval_s, trade.price, trade.price, trade.price,
                                  trade.price, trade.size)
        else:
            f = self.forming
            self.forming = Candle(self.instrument, f.open_time, self.interval_s, f.open, max(f.high, trade.price),
                                  min(f.low, trade.price), trade.price, f.volume + trade.size)
        return out

    def on_heartbeat(self, beat: Heartbeat) -> List[Union[Candle, Gap]]:
        out: List[Union[Candle, Gap]] = []
        if beat.last_trade_id is not None:
            if self.last_trade_id is None:
                self.last_trade_id = beat.last_trade_id  # Ausgangspunkt (z. B. letzter Handel vor dem Abo)
            elif beat.last_trade_id > self.last_trade_id:
                self.tainted = self.forming is not None  # Handel verpasst
                self.last_trade_id = beat.last_trade_id
                if self.forming is None:
                    self.watch_from = beat.time
        if self.forming is not None and beat.time >= self.forming.close_time:
            out += self._close()
        return out

    def _close(self) -> List[Union[Candle, Gap]]:
        assert self.forming is not None
        f, self.forming = self.forming, None
        out: List[Union[Candle, Gap]] = [Gap(f.open_time, f.close_time) if self.tainted else f]
        self.tainted = False
        self.done_until = f.close_time
        return out

    def _gap_until(self, start: datetime) -> List[Union[Candle, Gap]]:
        if self.done_until is None or start <= self.done_until:
            return []
        gap = Gap(self.done_until, start)
        self.done_until = start
        return [gap]


# ------------------------------------------------------------------ Monitor


@dataclass(frozen=True)
class MonitorPolicy:
    poll_s: float = 1.0  # so lange wartet ein Schritt hoechstens auf eine Nachricht
    reconnect_after_s: float = 45.0  # keine Nachricht -> neu verbinden (laenger als jeder Herzschlag-Abstand)
    halt_after_s: float = 120.0  # keine Marktdaten -> Not-Aus (DATA_ERROR)
    backoff_s: Tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0)
    backfill_delay_s: float = 10.0  # REST hat eine gerade geschlossene Kerze evtl. noch nicht
    backfill_attempts: int = 3

    def __post_init__(self) -> None:
        if not (0 < self.poll_s <= self.reconnect_after_s <= self.halt_after_s) or not self.backoff_s \
                or self.backfill_attempts < 1 or self.backfill_delay_s < 0:
            raise ValueError("MonitorPolicy: 0 < poll_s <= reconnect_after_s <= halt_after_s, Wartezeiten angeben.")


DEFAULT_POLICY = MonitorPolicy()


@dataclass
class MonitorStatus:
    connected: bool = False
    connects: int = 0
    disconnects: int = 0
    messages: int = 0
    candles_out: int = 0
    gaps_filled: int = 0
    gaps_given_up: int = 0
    rejected: int = 0  # unbrauchbare Nachrichten/Ereignisse
    halted_by_monitor: bool = False
    last_message_at: Optional[datetime] = None
    last_data_at: Optional[datetime] = None
    issues: Deque[str] = field(default_factory=lambda: deque(maxlen=MAX_ISSUES))


@dataclass
class _Pending:
    item: Union[Candle, Gap]
    attempts: int = 0
    next_try: Optional[datetime] = None


class MarketStreamMonitor:
    def __init__(
        self,
        protocol: ExchangeStream,
        transport: Transport,
        instruments: Sequence[InstrumentId],
        interval_s: int,
        clock: Clock,
        kill_switch: KillSwitch,
        backfill: Optional[HistoricalDataSource] = None,
        policy: MonitorPolicy = DEFAULT_POLICY,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not instruments or len(set(instruments)) != len(instruments):
            raise ValueError("Mindestens ein Instrument, keines doppelt.")
        if protocol.keepalive_s and protocol.keepalive_s >= policy.reconnect_after_s:
            raise ValueError("reconnect_after_s muss laenger sein als der Herzschlag-Abstand der Boerse.")
        self._protocol, self._transport = protocol, transport
        self._instruments = tuple(instruments)
        self._interval_s = interval_s
        self._clock, self._kill_switch, self._backfill = clock, kill_switch, backfill
        self._policy, self._sleep = policy, sleep
        self._builders = {i: CandleBuilder(i, interval_s) for i in self._instruments}
        self._queues: Dict[InstrumentId, Deque[_Pending]] = {i: deque() for i in self._instruments}
        self._ready: Deque[Candle] = deque()
        self._next_attempt = clock.now()
        self._failures = 0
        self._last_keepalive: Optional[datetime] = None
        self._started = clock.now()
        self.status = MonitorStatus()

    # ------------------------------------------------------------------ oeffentlich

    @property
    def policy(self) -> MonitorPolicy:
        return self._policy

    def candles(self, stop: Callable[[], bool] = lambda: False) -> Iterator[Candle]:
        """Geschlossene, gepruefte Kerzen in Zeitreihenfolge (je Instrument) - z. B. fuer `RuntimeEngine.run`."""
        while not stop():
            self.step()
            while self._ready:
                yield self._ready.popleft()

    def step(self) -> None:
        now = self._clock.now()
        self._check_stale(now)
        if not self.status.connected:
            if now < self._next_attempt:
                self._sleep(min((self._next_attempt - now).total_seconds(), self._policy.poll_s))
                return
            self._connect(now)
            return
        self._send_keepalive(now)
        try:
            text = self._transport.recv(self._policy.poll_s)
        except ConnectionLost as exc:
            self._lost("Verbindung verloren (%s)" % exc)
            return
        now = self._clock.now()
        if text is None:
            last = self.status.last_message_at or self._started
            if (now - last).total_seconds() >= self._policy.reconnect_after_s:
                self._lost("seit %.0f s keine Nachricht" % (now - last).total_seconds())
        else:
            self.status.messages += 1
            self.status.last_message_at = now
            self._handle(text, now)
        self._flush(now)

    # ------------------------------------------------------------------ Verbindung

    def _connect(self, now: datetime) -> None:
        try:
            self._transport.connect(self._protocol.url)
            for message in self._protocol.subscribe(self._instruments, self._interval_s):
                self._transport.send(message)
        except ConnectionLost as exc:
            self._schedule_retry(now, "Verbindung nicht moeglich (%s)" % exc)
            return
        self.status.connected = True
        self.status.connects += 1
        self.status.last_message_at = now  # Frist fuer die erste Nachricht beginnt jetzt
        self._last_keepalive = now
        for builder in self._builders.values():
            builder.reconnected(now)

    def _lost(self, why: str) -> None:
        now = self._clock.now()
        try:
            self._transport.close()
        except Exception:  # noqa: BLE001 - Schliessen darf nie den Monitor beenden
            pass
        self.status.connected = False
        self.status.disconnects += 1
        self._schedule_retry(now, why)

    def _schedule_retry(self, now: datetime, why: str) -> None:
        delay = self._policy.backoff_s[min(self._failures, len(self._policy.backoff_s) - 1)]
        self._failures += 1
        self._next_attempt = now + timedelta(seconds=delay)
        self._issue(now, "%s - neuer Versuch in %.0f s" % (why, delay))

    def _send_keepalive(self, now: datetime) -> None:
        if self._protocol.keepalive_s <= 0 or self._last_keepalive is None:
            return
        if (now - self._last_keepalive).total_seconds() >= self._protocol.keepalive_s:
            message = self._protocol.keepalive()
            if message is not None:
                try:
                    self._transport.send(message)
                except ConnectionLost as exc:
                    self._lost("Herzschlag nicht sendbar (%s)" % exc)
                    return
            self._last_keepalive = now

    # ------------------------------------------------------------------ Daten

    def _handle(self, text: str, now: datetime) -> None:
        try:
            events = self._protocol.parse(text)
        except StreamProtocolError as exc:
            self.status.rejected += 1
            self._issue(now, "Nachricht verworfen: %s" % exc)
            return
        for event in events:
            if isinstance(event, Pong):
                continue
            stamp = event.candle.open_time if isinstance(event, CandlePush) else event.time
            if stamp > now + FUTURE_TOLERANCE:
                self._data_error(now, "Zeitstempel aus der Zukunft (%s)" % stamp)
                continue
            instrument = event.candle.instrument if isinstance(event, CandlePush) else event.instrument
            if instrument is not None and instrument not in self._builders:
                self.status.rejected += 1
                continue
            self.status.last_data_at = now
            self._failures = 0  # Daten kommen wieder: Wartezeit zuruecksetzen
            builders = [self._builders[instrument]] if instrument is not None else list(self._builders.values())
            for builder in builders:
                if isinstance(event, Trade):
                    produced = builder.on_trade(event)
                elif isinstance(event, CandlePush):
                    produced = builder.on_push(event.candle)
                else:
                    produced = builder.on_heartbeat(event)
                self._queues[builder.instrument].extend(_Pending(p) for p in produced)

    def _flush(self, now: datetime) -> None:
        for instrument, queue in self._queues.items():
            while queue:
                head = queue[0]
                if isinstance(head.item, Candle):
                    problems = candle_problems(head.item, instrument)
                    if problems:
                        self._data_error(now, "fehlerhafte Kerze der Boerse (%s)" % ", ".join(p.value for p in problems))
                        head.item = Gap(head.item.open_time, head.item.close_time)
                        continue
                    self._emit(head.item)
                    queue.popleft()
                    continue
                if not self._fill(instrument, head, now):
                    break  # spaetere Kerzen warten, bis die Luecke gefuellt oder aufgegeben ist
                queue.popleft()

    def _fill(self, instrument: InstrumentId, pending: _Pending, now: datetime) -> bool:
        gap = pending.item
        assert isinstance(gap, Gap)
        due = pending.next_try or gap.end + timedelta(seconds=self._policy.backfill_delay_s)
        if now < due:
            return False
        if self._backfill is None:
            self._issue(now, "%s: Luecke %s..%s (kein Nachladen eingerichtet)" % (instrument.key, gap.start, gap.end))
            self.status.gaps_given_up += 1
            return True
        pending.attempts += 1
        try:
            series = self._backfill.load(instrument, self._interval_s, gap.start, gap.end)
        except Exception as exc:  # noqa: BLE001 - Nachladen gescheitert: spaeter erneut, dann aufgeben
            if pending.attempts < self._policy.backfill_attempts:
                pending.next_try = now + timedelta(seconds=max(self._policy.backfill_delay_s, 1.0))
                return False
            self._issue(now, "%s: Luecke %s..%s nicht nachladbar (%s)" % (instrument.key, gap.start, gap.end,
                                                                            type(exc).__name__))
            self.status.gaps_given_up += 1
            return True
        for candle in series.candles:
            if not candle_problems(candle, instrument):
                self._emit(candle)
        self.status.gaps_filled += 1
        return True

    def _emit(self, candle: Candle) -> None:
        self._ready.append(candle)
        self.status.candles_out += 1

    # ------------------------------------------------------------------ Schutz

    def _check_stale(self, now: datetime) -> None:
        last = self.status.last_data_at or self._started
        silent = (now - last).total_seconds()
        if silent >= self._policy.halt_after_s and not self.status.halted_by_monitor:
            self.status.halted_by_monitor = True
            self._kill_switch.trip(HaltReason.DATA_ERROR, "Marktdaten seit %.0f s veraltet (%s)" % (
                silent, ", ".join(i.key for i in self._instruments)))
            self._issue(now, "Not-Aus: Marktdaten seit %.0f s veraltet" % silent)

    def _data_error(self, now: datetime, why: str) -> None:
        self.status.rejected += 1
        self._kill_switch.trip(HaltReason.DATA_ERROR, "Marktueberwachung: %s" % why)
        self.status.halted_by_monitor = True
        self._issue(now, "Not-Aus: %s" % why)

    def _issue(self, now: datetime, text: str) -> None:
        self.status.issues.append("%s %s" % (now.isoformat(), text))
        LOG.log(logging.CRITICAL if text.startswith("Not-Aus") else logging.WARNING, text,
                extra={"component": "marketdata", "exchange": self._protocol.url})


__all__ = [
    "CandleBuilder", "CandlePush", "ConnectionLost", "ExchangeStream", "Gap", "Heartbeat", "MarketStreamMonitor",
    "MonitorPolicy", "MonitorStatus", "Pong", "StreamEvent", "StreamProtocolError", "Trade", "Transport", "bucket",
]
