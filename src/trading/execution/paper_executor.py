"""Paper Executor: simuliert Orders, Stop-Loss, Take-Profit, Gebuehren, Slippage und Funding.

SICHERHEIT: Dieses Modul spricht mit keiner Boerse und importiert keine Netzwerk-Bibliothek
(ein Test stellt das sicher). Es gibt hier keinen Code-Pfad, der eine echte Order senden
koennte.

Wichtige Modellannahmen (bewusst konservativ):
* Einstieg per Market-Order: Long zum Ask, Short zum Bid, jeweils plus Slippage.
* Stop-Loss ist eine Market-Order mit (groesserer) Slippage. Bei einer Kurs-Luecke ueber den
  Stop hinweg wird zum (schlechteren) Eroeffnungskurs der Kerze ausgefuehrt.
* Wenn in EINER Kerze sowohl Stop als auch Take-Profit erreicht wurden, gilt der Stop
  (wir wissen nicht, was zuerst kam - wir nehmen den schlechteren Fall an).
* Take-Profit wird zu genau diesem Kurs ausgefuehrt (kein Kursvorteil bei Luecken), zur Maker-Gebuehr.
* Kerzen, die vor der Positionseroeffnung begannen, werden ignoriert (kein Blick in die Zukunft).
* Futures: isolierte Margin, Liquidation begrenzt den Verlust auf die Margin.
* Funding wird zu den ueblichen 8h-Zeitpunkten (00/08/16 UTC) berechnet. Erhaltenes Funding
  wird nur gutgeschrieben, wenn die Position die Kerze ueberlebt (konservativ).
"""

from __future__ import annotations

import dataclasses
import functools
import threading
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TypeVar, cast

from trading.audit.event_log import EventLog
from trading.audit.events import EventType
from trading.clock import Clock
from trading.config import TradingMode
from trading.domain.models import (
    Candle,
    ClosedTrade,
    ExecutionResult,
    ExitReason,
    MarketSnapshot,
    MarketType,
    OrderIntent,
    OrderStatus,
    PortfolioState,
    Position,
    RiskDecision,
    Side,
)
from trading.domain.reasons import RejectReason
from trading.execution.costs import funding_times_between
from trading.safety.data_validator import SnapshotValidator
from trading.safety.risk_manager import RiskManager

PositionKey = Tuple[str, MarketType]


@dataclasses.dataclass(frozen=True)
class ExecutorState:
    """Vollstaendiger, speicherbarer Zustand des Executors (fuer die Datenbank)."""

    balance: Decimal
    positions: Tuple[Position, ...]
    marks: Dict[str, Decimal]
    peak_equity: Decimal
    day: date
    day_start_equity: Decimal
    position_counter: int

F = TypeVar("F", bound=Callable[..., Any])


def _locked(fn: F) -> F:
    """Alle Zustandsaenderungen und -abfragen laufen nacheinander (ein Thread zur Zeit).

    Ohne diese Sperre koennten zwei gleichzeitige Anfragen beide das Portfolio pruefen,
    bevor eine von ihnen ihre Position eintraegt - und so das 2-%-Limit ueberschreiten.
    """

    @functools.wraps(fn)
    def wrapper(self: "PaperExecutor", *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return fn(self, *args, **kwargs)

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
    ) -> None:
        if initial_equity <= 0:
            raise ValueError("Startkapital muss positiv sein.")
        self._risk = risk_manager
        self._costs = risk_manager.costs  # eine einzige Kostenquelle fuer Risiko UND Ausfuehrung
        self._validator = validator
        self._log = event_log
        self._clock = clock

        self._balance = initial_equity
        self._positions = {}  # type: Dict[PositionKey, Position]
        self._seen_order_ids = set()  # type: Set[str]
        self._marks = {}  # type: Dict[str, Decimal]
        self._closed = []  # type: List[ClosedTrade]
        self._position_counter = 0
        self._lock = threading.RLock()

        self._peak_equity = initial_equity
        self._day = clock.now().date()  # type: date
        self._day_start_equity = initial_equity

    # ------------------------------------------------------------------ Zustand

    @_locked
    def export_state(self) -> ExecutorState:
        return ExecutorState(
            balance=self._balance,
            positions=tuple(dataclasses.replace(p) for p in self._positions.values()),
            marks=dict(self._marks),
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
        self._positions = {
            (p.symbol, p.market_type): dataclasses.replace(p) for p in state.positions
        }
        self._marks = dict(state.marks)
        self._peak_equity = state.peak_equity
        self._day = state.day
        self._day_start_equity = state.day_start_equity
        self._position_counter = state.position_counter
        self._seen_order_ids = set(seen_order_ids)
        self._closed = []

    # ------------------------------------------------------------------ Abfragen

    @property
    @_locked
    def closed_trades(self) -> Tuple[ClosedTrade, ...]:
        return tuple(self._closed)

    @property
    @_locked
    def open_positions(self) -> Tuple[Position, ...]:
        return tuple(dataclasses.replace(p) for p in self._positions.values())

    @property
    @_locked
    def balance(self) -> Decimal:
        """Realisiertes Guthaben (ohne unrealisierte Gewinne/Verluste)."""
        return self._balance

    @_locked
    def equity(self) -> Decimal:
        return self._balance + self._unrealized_total()

    @_locked
    def portfolio_state(self) -> PortfolioState:
        unreal = self._unrealized_total()
        equity = self._balance + unreal
        self._roll_day(equity)
        self._peak_equity = max(self._peak_equity, equity)
        margin_used = sum((p.margin for p in self._positions.values()), Decimal(0))
        # Unrealisierte Gewinne zaehlen NICHT als freie Margin, Verluste schon (konservativ).
        free_margin = self._balance - margin_used + min(unreal, Decimal(0))
        return PortfolioState(
            equity=equity,
            free_margin=free_margin,
            day_start_equity=self._day_start_equity,
            peak_equity=self._peak_equity,
            # Restrisiko: Die Einstiegsgebuehr ist bereits bezahlt (steckt schon im Eigenkapital)
            # und darf nicht ein zweites Mal als "noch moegliches" Risiko zaehlen.
            open_risk=sum(
                (p.risk_at_open - p.entry_fee for p in self._positions.values()), Decimal(0)
            ),
            open_count=len(self._positions),
            open_keys=frozenset(self._positions.keys()),
        )

    # ------------------------------------------------------------------ Orders

    @_locked
    def submit(self, intent: OrderIntent, snapshot: MarketSnapshot) -> ExecutionResult:
        """Fuehrt eine Order aus - oder lehnt sie begruendet ab. Beides wird protokolliert."""
        setup = intent.setup

        # 1. Doppel-Order-Schutz: jede client_order_id wird hoechstens EINMAL bearbeitet,
        #    auch wenn die erste Bearbeitung abgelehnt wurde (Wiederholung braucht neue ID).
        if intent.client_order_id in self._seen_order_ids:
            return self._reject(intent, (RejectReason.DUPLICATE_ORDER_ID,), None)
        self._seen_order_ids.add(intent.client_order_id)

        # 2. Marktdaten muessen zum Setup passen und gueltig sein.
        if snapshot.symbol != setup.symbol or snapshot.market_type != setup.market_type:
            return self._reject(intent, (RejectReason.SYMBOL_MISMATCH,), None)
        validation = self._validator.validate(snapshot)
        if not validation.ok:
            return self._reject(intent, validation.reasons, None)
        self._marks[snapshot.symbol] = snapshot.mid

        # 3. Unabhaengige Risikopruefung auf Basis des realistischen Einstiegskurses.
        fill_price = self._costs.entry_fill_price(setup.side, snapshot)
        portfolio = self.portfolio_state()
        decision = self._risk.evaluate(
            setup, intent.quantity, intent.leverage, fill_price, snapshot.funding_rate, portfolio
        )
        if not decision.approved:
            return self._reject(intent, decision.reasons, decision)

        # 4. Ausfuehrung.
        notional = intent.quantity * fill_price
        entry_fee = self._costs.fee(notional, taker=True)
        self._balance -= entry_fee
        self._position_counter += 1
        now = self._clock.now()
        position = Position(
            position_id="P%06d" % self._position_counter,
            setup_id=setup.setup_id,
            strategy=setup.strategy,
            symbol=setup.symbol,
            market_type=setup.market_type,
            side=setup.side,
            quantity=intent.quantity,
            entry_price=fill_price,
            stop_loss=setup.stop_loss,
            take_profit=setup.take_profit,
            leverage=intent.leverage,
            opened_at=now,
            margin=decision.margin_required,
            risk_at_open=decision.worst_case_loss,
            entry_fee=entry_fee,
            liquidation_price=decision.liquidation_price,
            last_funding_ts=now,
        )
        self._positions[(setup.symbol, setup.market_type)] = position
        self._log.append(
            EventType.ORDER_FILLED,
            {
                "client_order_id": intent.client_order_id,
                "mode": self.MODE,
                "position": position,
                "risk": decision,
            },
        )
        return ExecutionResult(
            OrderStatus.FILLED,
            intent.client_order_id,
            position=dataclasses.replace(position),
            risk=decision,
        )

    @_locked
    def on_candle(self, candle: Candle, funding_rate: Decimal = Decimal(0)) -> List[ClosedTrade]:
        """Verarbeitet eine abgeschlossene Kerze: Funding, Stop-Loss, Take-Profit."""
        self._marks[candle.symbol] = candle.close
        newly_closed = []  # type: List[ClosedTrade]

        for key, pos in list(self._positions.items()):
            if pos.symbol != candle.symbol or candle.open_time < pos.opened_at:
                continue

            exit_ = self._check_exit(pos, candle)

            funding_cost = Decimal(0)
            if pos.market_type is MarketType.FUTURES:
                n = len(funding_times_between(pos.last_funding_ts, candle.close_time))
                if n:
                    notional = pos.quantity * candle.close
                    funding_cost = pos.side.sign * funding_rate * notional * n
                    if exit_ is not None and funding_cost < 0:
                        funding_cost = Decimal(0)  # Gutschrift nur, wenn die Position ueberlebt
            pos.last_funding_ts = candle.close_time
            if funding_cost != 0:
                self._balance -= funding_cost
                pos.funding_paid += funding_cost
                self._log.append(
                    EventType.FUNDING_APPLIED,
                    {"position_id": pos.position_id, "cost": funding_cost, "rate": funding_rate},
                )

            if exit_ is not None:
                price, reason = exit_
                newly_closed.append(self._close(key, pos, price, reason))

        # Kennzahlen (Hoechststand, Tageswechsel) auch ohne neue Order aktuell halten.
        self.portfolio_state()
        return newly_closed

    # ------------------------------------------------------------------ intern

    def _unrealized_total(self) -> Decimal:
        total = Decimal(0)
        for p in self._positions.values():
            mark = self._marks.get(p.symbol, p.entry_price)
            total += p.side.sign * (mark - p.entry_price) * p.quantity
        return total

    def _roll_day(self, equity_now: Decimal) -> None:
        today = self._clock.now().date()
        if today != self._day:
            self._day = today
            self._day_start_equity = equity_now

    def _check_exit(
        self, pos: Position, candle: Candle
    ) -> Optional[Tuple[Decimal, ExitReason]]:
        stop, tp = pos.stop_loss, pos.take_profit
        if pos.side is Side.LONG:
            if candle.open <= stop:
                raw = candle.open  # Luecke ueber den Stop hinweg
            elif candle.low <= stop:
                raw = stop
            elif candle.high >= tp:
                return tp, ExitReason.TAKE_PROFIT
            else:
                return None
        else:
            if candle.open >= stop:
                raw = candle.open
            elif candle.high >= stop:
                raw = stop
            elif candle.low <= tp:
                return tp, ExitReason.TAKE_PROFIT
            else:
                return None

        price = self._costs.stop_exit_price(pos.side, raw)
        liq = pos.liquidation_price
        if liq is not None:
            beyond = price <= liq if pos.side is Side.LONG else price >= liq
            if beyond:
                return liq, ExitReason.LIQUIDATION
        return price, ExitReason.STOP_LOSS

    def _close(
        self, key: PositionKey, pos: Position, exit_price: Decimal, reason: ExitReason
    ) -> ClosedTrade:
        gross = pos.side.sign * (exit_price - pos.entry_price) * pos.quantity
        if reason is ExitReason.LIQUIDATION:
            gross = max(gross, -pos.margin)  # isolierte Margin: mehr als die Margin ist nicht weg
        exit_fee = self._costs.fee(
            pos.quantity * exit_price, taker=reason is not ExitReason.TAKE_PROFIT
        )
        self._balance += gross - exit_fee
        fees = pos.entry_fee + exit_fee
        trade = ClosedTrade(
            position_id=pos.position_id,
            setup_id=pos.setup_id,
            strategy=pos.strategy,
            symbol=pos.symbol,
            market_type=pos.market_type,
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
        del self._positions[key]
        self._closed.append(trade)
        self._log.append(EventType.POSITION_CLOSED, trade)
        return trade

    def _reject(
        self, intent: OrderIntent, reasons: Tuple[RejectReason, ...], decision: Optional[RiskDecision]
    ) -> ExecutionResult:
        self._log.append(
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
            OrderStatus.REJECTED,
            intent.client_order_id,
            reasons=reasons,
            risk=decision,
        )
