"""Paper Executor: simuliert Orders, Stop-Loss, Take-Profit, Schliessung auf Signal, Gebuehren,
Slippage und Funding.

SICHERHEIT: Dieses Modul spricht mit keiner Boerse und importiert keine Netzwerk-Bibliothek
(ein Test stellt das sicher). Es gibt hier keinen Code-Pfad, der eine echte Order senden koennte.

Modellannahmen - im Zweifel immer zu Ungunsten des Haendlers:
* Einstieg per Market-Order: Long zum Ask, Short zum Bid, jeweils plus Slippage.
* Stop-Loss ist eine Market-Order mit (groesserer) Slippage. Bei einer Kursluecke ueber den
  Stop hinweg wird zum (schlechteren) Eroeffnungskurs der Kerze ausgefuehrt.
* Wurden in EINER Kerze sowohl Stop als auch Take-Profit erreicht, gilt der Stop
  (die Reihenfolge ist unbekannt - angenommen wird der schlechtere Fall).
* Take-Profit wird zu genau diesem Kurs ausgefuehrt (kein Kursvorteil bei Luecken), zur Maker-Gebuehr.
* Kein Blick in die Zukunft: Eine Kerze wird erst verarbeitet, wenn sie abgeschlossen ist, und nie
  rueckwirkend auf eine spaeter eroeffnete Position angewandt. Begann eine Kerze VOR dem Einstieg
  und endete danach, zaehlt nur ihr Stop (konservativ) - ob das Ziel nach dem Einstieg lag, ist unbekannt.
* Kerzen werden geprueft und nur in zeitlicher Reihenfolge angenommen; doppelte, aeltere oder
  fehlerhafte Kerzen werden protokolliert und NICHT verarbeitet.
* Futures: isolierte Margin. Bei Liquidation ist die gesamte Margin der Position verloren.
* Funding wird nur ueber ausdrueckliche Funding-Ereignisse gebucht (Satz und Kurs von der Boerse),
  jedes Ereignis je Position hoechstens einmal.
* Jede Operation ist unteilbar: Schlaegt unterwegs etwas fehl (auch das Protokoll), wird der Zustand
  vollstaendig zurueckgesetzt. Es gibt keinen Handel ohne Protokolleintrag.
* Globaler Not-Aus (safety/kill_switch.py): Ist er ausgeloest, wird keine Position eroeffnet; Schliessen bleibt moeglich.
  Der Executor loest ihn selbst aus bei formal fehlerhaften Marktdaten, bei einem Protokollfehler und bei den
  Notbremsen Tagesverlust/Drawdown. Ist sein Zustand nicht lesbar, gilt der Handel als gestoppt.
"""

from __future__ import annotations

import dataclasses
import functools
import threading
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TypeVar, cast

from trading.audit.event_log import EventLog
from trading.audit.events import EventType
from trading.clock import Clock
from trading.config import TradingMode
from trading.domain.instrument import InstrumentId, MarketType, is_instrument
from trading.domain.models import (
    Candle,
    ClosedTrade,
    CloseResult,
    ExecutionResult,
    ExitReason,
    FundingEvent,
    Mark,
    MarketSnapshot,
    OrderIntent,
    OrderStatus,
    PortfolioState,
    Position,
    RiskDecision,
    Side,
)
from trading.domain.numeric import is_finite_decimal, is_positive_decimal
from trading.domain.reasons import RejectReason
from trading.execution.costs import cost_up, gain_down
from trading.marketdata.candles import candle_problems, is_aware
from trading.safety.data_validator import FUTURE_TOLERANCE_S, SnapshotValidator
from trading.safety.kill_switch import HaltReason, HaltStatus, InMemoryKillSwitch, KillSwitch, check_release, clean_note
from trading.safety.order_risk import brake_reasons_for, is_beyond, position_stress_loss
from trading.safety.risk_manager import RiskManager

ZERO = Decimal(0)
F = TypeVar("F", bound=Callable[..., Any])
_REQUESTED_EXITS = (ExitReason.SIGNAL, ExitReason.MANUAL)
# Formal kaputte Daten deuten auf eine gestoerte Quelle oder Uhr -> Not-Aus. Doppelte/aeltere Kerzen dagegen sind bei
# Wiederholungen (z. B. nach einem Neustart) normal; zu weite Spreads, Spruenge, veraltete Kurse sind Marktlagen - sie
# blockieren nur die einzelne Order.
FORMAL_DATA_ERRORS = frozenset({
    RejectReason.NAIVE_TIMESTAMP, RejectReason.FUTURE_TIMESTAMP, RejectReason.MISSING_DATA,
    RejectReason.NON_FINITE_VALUE, RejectReason.NON_POSITIVE_PRICE, RejectReason.CROSSED_BOOK,
    RejectReason.BAD_CANDLE, RejectReason.CANDLE_MISALIGNED, RejectReason.BAD_FUNDING,
})
_HALT_REJECT = {HaltReason.DAILY_LOSS: RejectReason.DAILY_LOSS_LIMIT, HaltReason.DRAWDOWN: RejectReason.DRAWDOWN_LIMIT}


@dataclasses.dataclass(frozen=True)
class ExecutorState:
    """Vollstaendiger, speicherbarer Zustand des Executors (fuer die Datenbank)."""

    balance: Decimal
    positions: Tuple[Position, ...]
    marks: Dict[InstrumentId, Mark]
    last_candle_close: Dict[InstrumentId, datetime]
    peak_equity: Decimal
    day: date
    day_start_equity: Decimal
    position_counter: int


def _locked(fn: F) -> F:
    """Abfragen laufen nacheinander (ein Thread zur Zeit)."""

    @functools.wraps(fn)
    def wrapper(self: "PaperExecutor", *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return fn(self, *args, **kwargs)

    return cast(F, wrapper)


def _atomic(fn: F) -> F:
    """Zustandsaendernde Operationen: gesperrt UND unteilbar.

    Ereignisse werden waehrend der Operation gesammelt und erst am Ende ins Protokoll geschrieben.
    Scheitert irgendetwas - auch das Protokoll -, wird der gesamte Zustand zurueckgesetzt.
    (Ohne Sperre koennten zwei gleichzeitige Anfragen beide das Portfolio pruefen, bevor eine ihre
    Position eintraegt - und so die Limits ueberschreiten.)
    """

    @functools.wraps(fn)
    def wrapper(self: "PaperExecutor", *args: Any, **kwargs: Any) -> Any:
        # Auch den (evtl. geteilten) Not-Aus fuer die ganze Operation halten: Sonst koennte ein anderer Executor ihn
        # zwischen "Not-Aus geprueft" und "Position eingetragen" ausloesen. Reihenfolge immer: Executor, dann Not-Aus.
        with self._lock, self._halt.hold():
            backup = self._backup()
            self._pending = []
            self._new_order_ids = []
            self._in_atomic = True
            try:
                result = fn(self, *args, **kwargs)
                try:
                    for event_type, payload in self._pending:
                        self._log.append(event_type, payload)
                except Exception as exc:
                    # Protokoll nicht schreibbar: Operation verwerfen UND Not-Aus (ohne Protokolleintrag - es ist ja kaputt).
                    self._restore(backup)
                    self._trip_silently(HaltReason.PROTOCOL_ERROR, "Protokoll nicht schreibbar: %s" % type(exc).__name__)
                    raise
                return result
            except BaseException:
                self._restore(backup)
                raise
            finally:
                self._in_atomic = False
                self._pending = []
                self._new_order_ids = []

    return cast(F, wrapper)


class PaperExecutor:
    MODE = TradingMode.PAPER  # es gibt keinen anderen Modus

    def __init__(
        self,
        initial_equity: Decimal,
        risk_manager: RiskManager,
        validator: SnapshotValidator,
        event_log: EventLog,
        clock: Clock,
        kill_switch: Optional[KillSwitch] = None,
    ) -> None:
        if not is_positive_decimal(initial_equity):
            raise ValueError("Startkapital muss eine positive Decimal-Zahl sein.")
        self._risk = risk_manager
        self._limits = risk_manager.limits
        self._costs = risk_manager.costs  # eine einzige Kostenquelle fuer Risiko UND Ausfuehrung
        self._validator = validator
        self._log = event_log
        self._clock = clock
        self._lock = threading.RLock()
        self._pending: List[Tuple[EventType, Any]] = []
        self._new_order_ids: List[str] = []  # in der laufenden Operation neu vermerkte Order-IDs
        self._in_atomic = False
        # Ohne geteilten Not-Aus gilt ein eigener (nur fuer diesen Executor). Mehrere Executoren eines Prozesses
        # teilen sich EIN Objekt; mehrere Prozesse teilen ihn ueber die Datenbank (persistence/pg_executor.py).
        self._halt: KillSwitch = kill_switch if kill_switch is not None else InMemoryKillSwitch(clock)
        # Ist der gemeinsame Not-Aus nicht schreibbar, stoppt wenigstens dieser Executor (fail closed) - ohne dass
        # deswegen ein Stop-Loss oder eine Schliessung scheitert.
        self._local_halt: Optional[HaltStatus] = None

        self._balance = initial_equity
        self._positions: Dict[InstrumentId, Position] = {}
        self._marks: Dict[InstrumentId, Mark] = {}
        self._last_candle_close: Dict[InstrumentId, datetime] = {}
        self._seen_order_ids: Set[str] = set()
        self._closed: List[ClosedTrade] = []
        self._position_counter = 0
        self._peak_equity = initial_equity
        self._day: date = clock.now().date()
        self._day_start_equity = initial_equity

    # ------------------------------------------------------------------ Zustand

    @_locked
    def export_state(self) -> ExecutorState:
        return ExecutorState(
            balance=self._balance,
            positions=tuple(self._positions.values()),
            marks=dict(self._marks),
            last_candle_close=dict(self._last_candle_close),
            peak_equity=self._peak_equity,
            day=self._day,
            day_start_equity=self._day_start_equity,
            position_counter=self._position_counter,
        )

    @_locked
    def restore_state(self, state: ExecutorState, seen_order_ids: Tuple[str, ...] = ()) -> None:
        """Stellt einen gespeicherten Zustand wieder her (Neustart / Laden aus der Datenbank).
        `seen_order_ids`: bereits bekannte Order-IDs (Doppel-Order-Schutz)."""
        self._balance = state.balance
        self._positions = {p.instrument: p for p in state.positions}
        self._marks = dict(state.marks)
        self._last_candle_close = dict(state.last_candle_close)
        self._peak_equity = state.peak_equity
        self._day = state.day
        self._day_start_equity = state.day_start_equity
        self._position_counter = state.position_counter
        self._seen_order_ids = set(seen_order_ids)
        self._closed = []

    def _backup(self) -> Tuple[Any, ...]:
        return (
            self._balance, dict(self._positions), dict(self._marks), dict(self._last_candle_close),
            len(self._closed), self._position_counter, self._peak_equity,
            self._day, self._day_start_equity, self._validator.snapshot_state(),
        )

    def _restore(self, backup: Tuple[Any, ...]) -> None:
        (self._balance, self._positions, self._marks, self._last_candle_close,
         closed_len, self._position_counter, self._peak_equity, self._day, self._day_start_equity,
         validator_state) = backup
        del self._closed[closed_len:]
        self._seen_order_ids.difference_update(self._new_order_ids)
        self._validator.replace_state(validator_state)

    def _emit(self, event_type: EventType, payload: Any) -> None:
        self._pending.append((event_type, payload))

    # ------------------------------------------------------------------ Abfragen

    @property
    @_locked
    def closed_trades(self) -> Tuple[ClosedTrade, ...]:
        return tuple(self._closed)

    @property
    @_locked
    def open_positions(self) -> Tuple[Position, ...]:
        return tuple(self._positions.values())

    @property
    @_locked
    def balance(self) -> Decimal:
        """Realisiertes Guthaben (ohne unrealisierte Gewinne/Verluste)."""
        return self._balance

    @_locked
    def equity(self) -> Decimal:
        return self.portfolio_state().equity

    @_locked
    def portfolio_state(self) -> PortfolioState:
        now = self._clock.now()
        unrealized, exposure, stress, margin_used, open_risk, complete = self._valuate(now)
        equity = self._balance + unrealized
        if now.date() > self._day:
            # Tageswechsel: Was seit der letzten Bewertung verloren ging, zaehlt noch zum ALTEN Tag (konservativ) -
            # sonst fiele z. B. der Verlust der Kerze, die um Mitternacht schliesst, aus der Tagesbremse heraus.
            self._check_brakes(equity, self._day_start_equity, max(self._peak_equity, equity))
        self._roll_day(now.date(), equity)
        self._peak_equity = max(self._peak_equity, equity)
        state = PortfolioState(
            balance=self._balance,
            unrealized_pnl=unrealized,
            equity=equity,
            # Unrealisierte Gewinne zaehlen NICHT als freie Margin, Verluste schon (konservativ).
            free_margin=self._balance - margin_used + min(unrealized, ZERO),
            day_start_equity=self._day_start_equity,
            peak_equity=self._peak_equity,
            open_risk=open_risk,
            open_count=len(self._positions),
            open_keys=frozenset(self._positions),
            gross_exposure=exposure,
            stress_loss=stress,
            valuation_complete=complete,
        )
        self._check_brakes(equity, self._day_start_equity, self._peak_equity)
        return state

    def _valuate(self, now: datetime) -> Tuple[Decimal, Decimal, Decimal, Decimal, Decimal, bool]:
        """(unrealisiert, Exposure, Stress-Verlust, gebundene Margin, offenes Risiko, Bewertung vollstaendig)."""
        unrealized = exposure = stress = margin_used = open_risk = ZERO
        complete = True
        for p in self._positions.values():
            mark = self._marks.get(p.instrument)
            if mark is None or (now - mark.time).total_seconds() > self._limits.max_mark_age_s:
                complete = False  # Equity nicht sicher bestimmbar -> keine neuen Positionen
            price = mark.price if mark is not None else p.entry_price
            unrealized += p.side.sign * (price - p.entry_price) * p.quantity
            exposure += p.quantity * price
            stress += position_stress_loss(
                p.side, p.quantity, price, p.entry_price, p.margin, p.liquidation_price,
                self._limits.stress_shock, self._costs,
            )
            margin_used += p.margin
            open_risk += p.remaining_risk
        return unrealized, exposure, stress, margin_used, open_risk, complete

    def _check_brakes(self, equity: Decimal, day_start: Decimal, peak: Decimal) -> None:
        """Notbremse -> Not-Aus (bleibt bis zur bewussten Freigabe). Gleiche Definition wie in der Risikopruefung."""
        for brake in brake_reasons_for(equity, day_start, peak, self._limits):
            self._trip(HaltReason.DAILY_LOSS if brake is RejectReason.DAILY_LOSS_LIMIT else HaltReason.DRAWDOWN,
                       "Equity %s, Tagesbeginn %s, Hoechststand %s" % (equity, day_start, peak))

    # ------------------------------------------------------------------ Not-Aus

    def halt_status(self) -> HaltStatus:
        """Zustand des Not-Aus. Nicht lesbar = gestoppt (fail closed)."""
        if self._local_halt is not None:
            return self._local_halt
        try:
            status = self._halt.status()
        except Exception as exc:  # z. B. Datenbank nicht erreichbar
            return HaltStatus(True, None, None, "Not-Aus nicht lesbar: %s" % type(exc).__name__)
        return status if isinstance(status, HaltStatus) else HaltStatus(True, None, None, "Not-Aus ungueltig")

    @_atomic
    def halt(self, note: str = "") -> bool:
        """Not-Aus von Hand. Rueckgabe: True, wenn er dadurch neu ausgeloest wurde."""
        return self._trip(HaltReason.MANUAL, note)

    @_atomic
    def release_halt(self, operator: str, confirmation: str) -> None:
        """Hebt den Not-Aus bewusst auf (Name + Bestaetigungstext, siehe kill_switch.RELEASE_CONFIRMATION).

        Die Freigabe nimmt den bisherigen Verlust bewusst hin: Tagesbeginn und Hoechststand der Notbremsen werden auf
        die aktuelle Equity gesetzt. Sonst loeste dieselbe Bremse sofort wieder aus, und nach einem Drawdown waere eine
        Freigabe nie moeglich (Audit M14). Gemessen wird ab jetzt neu - mit denselben Grenzen.
        (Teilen sich mehrere Executoren einen Schalter, gilt das nur fuer diesen; die anderen loesen bei eigener
        Grenzverletzung wieder aus.)"""
        name = check_release(operator, confirmation)
        before = self.halt_status()
        self._halt.release(name, confirmation)
        self._local_halt = None
        now = self._clock.now()
        equity = self._balance + self._valuate(now)[0]
        self._day, self._day_start_equity, self._peak_equity = now.date(), equity, equity
        self._emit(EventType.HALT_RELEASED, {"operator": name, "previous_reason": before.reason,
                                             "previous_since": before.since, "rearmed_at_equity": equity})

    def _trip(self, reason: HaltReason, note: str) -> bool:
        """Loest den Not-Aus aus und protokolliert das (nur beim ersten Ausloesen)."""
        try:
            newly = self._halt.trip(reason, note)
        except Exception as exc:  # gemeinsamer Not-Aus nicht schreibbar: wenigstens hier stoppen, Schliessen nicht abbrechen
            newly = self._local_halt is None
            if newly:
                note = "%s (gemeinsamer Not-Aus nicht schreibbar: %s)" % (note, type(exc).__name__)
                self._local_halt = HaltStatus(True, reason, self._clock.now(), clean_note(note))
        if not newly:
            return False
        payload = {"reason": reason, "note": note}
        if self._in_atomic:
            self._emit(EventType.HALT_ACTIVATED, payload)
        else:
            self._log.append(EventType.HALT_ACTIVATED, payload)
        return True

    def _trip_silently(self, reason: HaltReason, note: str) -> None:
        try:
            self._halt.trip(reason, note)
        except Exception:  # noqa: BLE001 - der urspruengliche Fehler wird ohnehin weitergereicht
            if self._local_halt is None:
                self._local_halt = HaltStatus(True, reason, self._clock.now(), clean_note(note))

    def _halted_reasons(self) -> Optional[Tuple[RejectReason, ...]]:
        status = self.halt_status()
        if not status.halted:
            return None
        extra = _HALT_REJECT.get(status.reason) if status.reason is not None else None
        return (RejectReason.TRADING_HALTED,) + ((extra,) if extra is not None else ())

    def _check_data(self, reasons: Any) -> None:
        bad = sorted(r.value for r in reasons if r in FORMAL_DATA_ERRORS)
        if bad:
            self._trip(HaltReason.DATA_ERROR, "fehlerhafte Marktdaten: %s" % ", ".join(bad))

    # ------------------------------------------------------------------ Orders

    @_atomic
    def submit(self, intent: OrderIntent, snapshot: MarketSnapshot) -> ExecutionResult:
        """Fuehrt eine Order aus - oder lehnt sie begruendet ab. Beides wird protokolliert."""
        setup = intent.setup

        # 1. Doppel-Order-Schutz: jede client_order_id wird hoechstens EINMAL bearbeitet,
        #    auch wenn die erste Bearbeitung abgelehnt wurde (Wiederholung braucht neue ID).
        if intent.client_order_id in self._seen_order_ids:
            return self._reject(intent, (RejectReason.DUPLICATE_ORDER_ID,), None)
        self._seen_order_ids.add(intent.client_order_id)
        self._new_order_ids.append(intent.client_order_id)

        # 1b. Globaler Not-Aus: keine neue Position, egal was die Pruefungen danach sagen wuerden.
        halted = self._halted_reasons()
        if halted is not None:
            return self._reject(intent, halted, None)

        # 2. Marktdaten muessen zu genau diesem Instrument gehoeren und gueltig sein.
        if not is_instrument(setup.instrument) or not isinstance(setup.side, Side):
            return self._reject(intent, (RejectReason.INVALID_SYMBOL,), None)
        if snapshot.instrument != setup.instrument:
            return self._reject(intent, (RejectReason.SYMBOL_MISMATCH,), None)
        validation = self._validator.validate(snapshot)
        if not validation.ok:
            self._check_data(validation.reasons)
            return self._reject(intent, validation.reasons, None)
        self._marks[snapshot.instrument] = Mark(snapshot.mid, snapshot.timestamp)

        # 3. Unabhaengige Risikopruefung auf Basis des realistischen Einstiegskurses.
        quote = self._costs.quote(setup.side, snapshot)
        state = self.portfolio_state()  # kann eine Notbremse und damit den Not-Aus ausloesen
        halted = self._halted_reasons()
        if halted is not None:
            return self._reject(intent, halted, None)
        decision = self._risk.evaluate(setup, intent.quantity, intent.leverage, quote, state)
        if not decision.approved:
            return self._reject(intent, decision.reasons, decision)

        # 4. Ausfuehrung.
        self._balance -= decision.entry_fee
        self._position_counter += 1
        now = self._clock.now()
        position = Position(
            position_id="P%06d" % self._position_counter,
            setup_id=setup.setup_id,
            strategy=setup.strategy,
            instrument=setup.instrument,
            side=setup.side,
            quantity=intent.quantity,
            entry_price=quote.fill_price,
            stop_loss=setup.stop_loss,
            take_profit=setup.take_profit,
            leverage=intent.leverage,
            opened_at=now,
            margin=decision.margin_required,
            risk_at_open=decision.worst_case_loss,
            entry_fee=decision.entry_fee,
            liquidation_price=decision.liquidation_price,
            last_funding_ts=now,
        )
        self._positions[setup.instrument] = position
        self._emit(
            EventType.ORDER_FILLED,
            {
                "client_order_id": intent.client_order_id,
                "mode": self.MODE,
                "position": position,
                "risk": decision,
            },
        )
        self.portfolio_state()  # Hoechststand/Tageswechsel fortschreiben
        return ExecutionResult(
            OrderStatus.FILLED, intent.client_order_id, position=position, risk=decision
        )

    @_atomic
    def close_position(
        self,
        instrument: InstrumentId,
        snapshot: MarketSnapshot,
        reason: ExitReason,
        request_id: str,
    ) -> CloseResult:
        """Schliesst eine Position per Market-Order (Ausstiegssignal oder Eingriff).

        Ohne gueltige, aktuelle Marktdaten wird NICHT geschlossen (kein erfundener Kurs);
        die Position bleibt dann mit ihrem Stop-Loss bestehen."""

        def refuse(*reasons: RejectReason) -> CloseResult:
            self._emit(
                EventType.CLOSE_REJECTED,
                {"request_id": request_id, "instrument": getattr(instrument, "key", repr(instrument)),
                 "reasons": reasons},
            )
            return CloseResult(False, request_id, reasons)

        if reason not in _REQUESTED_EXITS:
            return refuse(RejectReason.INVALID_EXIT_REASON)
        position = self._positions.get(instrument) if is_instrument(instrument) else None
        if position is None:
            return refuse(RejectReason.NO_POSITION)
        if snapshot.instrument != instrument:
            return refuse(RejectReason.SYMBOL_MISMATCH)
        validation = self._validator.validate(snapshot)
        if not validation.ok:
            self._check_data(validation.reasons)
            return refuse(*validation.reasons)
        self._marks[instrument] = Mark(snapshot.mid, snapshot.timestamp)
        price = self._costs.market_exit_price(position.side, snapshot)
        trade = self._close(position, price, reason, request_id)
        self.portfolio_state()
        return CloseResult(True, request_id, trade=trade)

    # ------------------------------------------------------------------ Marktdaten

    @_atomic
    def on_candle(self, candle: Candle) -> List[ClosedTrade]:
        """Verarbeitet eine ABGESCHLOSSENE Kerze: Bewertung, Stop-Loss, Take-Profit, Liquidation.

        Fehlerhafte, doppelte, aeltere oder noch nicht abgeschlossene Kerzen werden protokolliert
        und ohne jede Wirkung verworfen (Rueckgabe: leere Liste)."""
        problems = list(candle_problems(candle))
        if not problems:
            if (candle.close_time - self._clock.now()).total_seconds() > FUTURE_TOLERANCE_S:
                problems.append(RejectReason.FUTURE_TIMESTAMP)  # noch nicht abgeschlossen
            last_close = self._last_candle_close.get(candle.instrument)
            if last_close is not None and candle.open_time < last_close:
                problems.append(
                    RejectReason.DUPLICATE_CANDLE
                    if candle.close_time == last_close
                    else RejectReason.OUT_OF_ORDER
                )
        if problems:
            self._emit(EventType.DATA_REJECTED, _describe_rejected("candle", candle, problems))
            self._check_data(problems)
            return []

        instrument = candle.instrument
        last_close = self._last_candle_close.get(instrument)
        if last_close is not None and candle.open_time > last_close:
            self._emit(EventType.DATA_GAP,
                       {"instrument": instrument.key, "missing_from": last_close, "missing_to": candle.open_time})
        previous = self._marks.get(instrument)
        if previous is not None and (
            max(abs(candle.high - previous.price), abs(candle.low - previous.price)) / previous.price
            > self._limits.max_price_jump
        ):
            # Auffaellig, aber NICHT verworfen: ein echter Crash muss Stops ausloesen koennen.
            self._emit(EventType.DATA_ANOMALY,
                       {"instrument": instrument.key, "open_time": candle.open_time,
                        "previous_price": previous.price, "high": candle.high, "low": candle.low})

        self._marks[instrument] = Mark(candle.close, candle.close_time)
        self._last_candle_close[instrument] = candle.close_time
        self._validator.observe(instrument, candle.close_time, candle.close)

        closed: List[ClosedTrade] = []
        position = self._positions.get(instrument)
        if position is not None and candle.close_time > position.opened_at:
            exit_ = self._check_exit(position, candle)
            if exit_ is not None:
                closed.append(self._close(position, exit_[0], exit_[1], None))

        self.portfolio_state()  # Hoechststand/Tageswechsel auch ohne Order aktuell halten
        return closed

    @_atomic
    def apply_funding(self, event: FundingEvent) -> Decimal:
        """Bucht eine Funding-Abrechnung. Rueckgabe: belasteter Betrag (negativ = Gutschrift, 0 = keine Wirkung).

        Gilt fuer die Position, die VOR dem Abrechnungszeitpunkt eroeffnet wurde; jedes Ereignis wirkt
        je Position hoechstens einmal (eine Wiederholung - z. B. nach einem Neustart - bleibt wirkungslos)."""
        problems = _funding_problems(event)
        if not problems and (event.time - self._clock.now()).total_seconds() > FUTURE_TOLERANCE_S:
            problems.append(RejectReason.FUTURE_TIMESTAMP)
        if problems:
            self._emit(EventType.DATA_REJECTED, _describe_rejected("funding", event, problems))
            self._check_data(problems)
            return ZERO
        position = self._positions.get(event.instrument)
        if position is None or event.time <= position.last_funding_ts:
            return ZERO
        # Positiver Satz: Long zahlt. Gerundet wird immer zu Ungunsten des Haendlers.
        cost = cost_up(position.side.sign * event.rate * position.quantity * event.mark_price)
        self._balance -= cost
        updated = dataclasses.replace(
            position, funding_paid=position.funding_paid + cost, last_funding_ts=event.time
        )
        self._positions[event.instrument] = updated
        self._emit(
            EventType.FUNDING_APPLIED,
            {"position_id": position.position_id, "instrument": event.instrument.key, "time": event.time,
             "rate": event.rate, "mark_price": event.mark_price, "cost": cost},
        )
        self.portfolio_state()
        return cost

    # ------------------------------------------------------------------ intern

    def _roll_day(self, today: date, equity_now: Decimal) -> None:
        # Nur VORWAERTS: Eine zurueckspringende oder falsch gehende Uhr (auch die eines anderen
        # Prozesses) darf die Tagesverlust-Notbremse nicht zuruecksetzen.
        if today > self._day:
            self._day = today
            self._day_start_equity = equity_now

    def _check_exit(
        self, pos: Position, candle: Candle
    ) -> Optional[Tuple[Decimal, ExitReason]]:
        side, stop, tp = pos.side, pos.stop_loss, pos.take_profit
        adverse, favorable = (candle.low, candle.high) if side is Side.LONG else (candle.high, candle.low)
        if candle.open_time < pos.opened_at:
            # Kerze begann vor dem Einstieg: Nur der Stop zaehlt (konservativ). Ob das Ziel NACH
            # dem Einstieg erreicht wurde, laesst sich aus der Kerze nicht ablesen.
            if not is_beyond(side, adverse, stop):
                return None
            raw = stop
        elif is_beyond(side, candle.open, stop):
            raw = candle.open  # Luecke ueber den Stop hinweg: schlechterer Eroeffnungskurs
        elif is_beyond(side, adverse, stop):
            raw = stop  # auch wenn zugleich das Ziel erreicht wurde: Stop zuerst
        elif is_beyond(side, tp, favorable):
            return tp, ExitReason.TAKE_PROFIT
        else:
            return None

        price = self._costs.stop_exit_price(side, raw)
        liq = pos.liquidation_price
        if liq is not None and is_beyond(side, price, liq):
            return liq, ExitReason.LIQUIDATION
        return price, ExitReason.STOP_LOSS

    def _close(
        self, pos: Position, exit_price: Decimal, reason: ExitReason, request_id: Optional[str]
    ) -> ClosedTrade:
        if reason is ExitReason.LIQUIDATION:
            # Isolierte Margin: Bei Liquidation ist die GESAMTE Margin der Position verloren
            # (der Rest oberhalb des Kursverlusts geht als Liquidationsgebuehr an die Boerse).
            gross, exit_fee = -pos.margin, ZERO
        else:
            gross = gain_down(pos.side.sign * (exit_price - pos.entry_price) * pos.quantity)
            exit_fee = self._costs.fee(
                pos.quantity * exit_price, taker=reason is not ExitReason.TAKE_PROFIT
            )
        self._balance += gross - exit_fee
        fees = pos.entry_fee + exit_fee
        trade = ClosedTrade(
            position_id=pos.position_id,
            setup_id=pos.setup_id,
            strategy=pos.strategy,
            instrument=pos.instrument,
            side=pos.side,
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            opened_at=pos.opened_at,
            closed_at=self._clock.now(),
            exit_reason=reason,
            gross_pnl=gross,
            fees=fees,
            funding=pos.funding_paid,
            net_pnl=gross - fees - pos.funding_paid,
            risk_at_open=pos.risk_at_open,
        )
        del self._positions[pos.instrument]
        self._closed.append(trade)
        self._emit(EventType.POSITION_CLOSED, {"trade": trade, "request_id": request_id, "mode": self.MODE})
        return trade

    def _reject(
        self, intent: OrderIntent, reasons: Tuple[RejectReason, ...], decision: Optional[RiskDecision]
    ) -> ExecutionResult:
        self._emit(
            EventType.ORDER_REJECTED,
            {
                "client_order_id": intent.client_order_id,
                "stage": "EXECUTOR",
                "setup": intent.setup,
                "quantity": intent.quantity,
                "leverage": intent.leverage,
                "reasons": reasons,
                "risk": decision,
            },
        )
        return ExecutionResult(
            OrderStatus.REJECTED, intent.client_order_id, reasons=reasons, risk=decision
        )


def _funding_problems(event: Any) -> List[RejectReason]:
    if not isinstance(event, FundingEvent):
        return [RejectReason.MISSING_DATA]
    problems: List[RejectReason] = []
    if not is_instrument(event.instrument):
        problems.append(RejectReason.INVALID_SYMBOL)
    elif event.instrument.market_type is not MarketType.FUTURES:
        problems.append(RejectReason.BAD_FUNDING)  # Funding gibt es nur bei Futures
    if not is_aware(event.time):
        problems.append(RejectReason.NAIVE_TIMESTAMP)
    if not is_finite_decimal(event.rate) or not is_positive_decimal(event.mark_price):
        problems.append(RejectReason.BAD_FUNDING)
    return problems


def _describe_rejected(kind: str, obj: Any, problems: List[RejectReason]) -> Dict[str, Any]:
    """Protokolltext fuer verworfene Daten - nur unkritische Textform, damit auch NaN & Co. darstellbar sind."""
    instrument = getattr(obj, "instrument", None)
    when = getattr(obj, "open_time", None) if kind == "candle" else getattr(obj, "time", None)
    return {
        "kind": kind,
        "instrument": instrument.key if isinstance(instrument, InstrumentId) else repr(instrument),
        "time": str(when),
        "reasons": tuple(problems),
    }
