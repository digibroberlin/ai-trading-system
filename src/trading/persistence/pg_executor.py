"""Paper Executor mit PostgreSQL als einziger Quelle der Wahrheit.

Wie mehrere Prozesse sicher zusammenarbeiten ("Teamrisiko"):

  1. Jede Aktion (Order, Schliessung, Kerze, Funding, Portfolio-Abfrage) ist EINE Datenbank-Transaktion.
  2. Gleich zu Beginn wird die Konto-Zeile gesperrt (`SELECT ... FOR UPDATE`). Alle anderen
     Prozesse warten dort in einer Schlange - fuer dieses Konto arbeitet immer nur einer.
  3. Unter der Sperre wird der aktuelle Zustand geladen, und die BEREITS GETESTETE Engine
     (`PaperExecutor` samt unabhaengigem Risikomanager) entscheidet. Es gibt keine zweite,
     abweichende Rechenlogik: alle Grenzen (Risiko, Exposure, Stress, Margin ...) werden damit atomar geprueft.
  4. Zustand, Positionen, Order-ID, Handelsergebnisse, Marktzustand UND Protokolleintraege werden in
     derselben Transaktion gespeichert. Stuerzt der Prozess vorher ab, passiert nichts
     (Rollback) - es bleiben keine halben Handel zurueck.
  5. Zweiter Gurt: Beim COMMIT prueft die Datenbank Gesamtrisiko und Brutto-Exposure erneut (Constraint-Trigger).

Ein Neustart ist daher trivial: Es gibt keinen Zustand im Prozess, der verloren gehen koennte. Auch der
Stand der Kerzen-Verarbeitung je Instrument liegt in der Datenbank - wiederholt gelieferte Kerzen und
Funding-Ereignisse bleiben nach einem Neustart wirkungslos.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, TypeVar

from psycopg.rows import dict_row

from trading.audit.event_log import EventLog
from trading.clock import Clock
from trading.config import TradingMode
from trading.domain.instrument import Exchange, InstrumentId, MarketType, is_instrument
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
    PortfolioState,
    Position,
    Side,
)
from trading.domain.numeric import is_positive_decimal
from trading.execution.paper_executor import ExecutorState, PaperExecutor
from trading.persistence.db import connect
from trading.persistence.pg_event_log import append_raw
from trading.safety.data_validator import SnapshotValidator
from trading.safety.risk_manager import RiskManager

T = TypeVar("T")
FaultHook = Callable[[str], None]
# Zustand eines Instruments, wie er in `instrument_state` steht: (Kurs, Zeit, Referenzkurs, Zeit, Kerzen-Stand)
InstrumentRow = Tuple[Optional[Decimal], Optional[datetime], Optional[Decimal], Optional[datetime],
                      Optional[datetime]]
_EMPTY_ROW: InstrumentRow = (None, None, None, None, None)


class _CollectingLog(EventLog):
    """Sammelt die Ereignisse der Engine; sie werden danach in die DB-Kette uebernommen."""

    def _persist(self, event: Any) -> None:
        return None


@dataclass
class _Loaded:
    account: Dict[str, Any]
    engine: PaperExecutor
    sink: _CollectingLog
    validator: SnapshotValidator
    positions: Dict[str, Position]
    instruments: Dict[InstrumentId, InstrumentRow]


class PostgresPaperExecutor:
    MODE = TradingMode.PAPER

    def __init__(
        self,
        url: str,
        account_id: str,
        initial_equity: Decimal,
        risk_manager: RiskManager,
        clock: Clock,
        fault_hook: Optional[FaultHook] = None,
    ) -> None:
        self._account_id = account_id
        self._risk = risk_manager
        self._clock = clock
        self._fault_hook = fault_hook  # nur fuer Absturz-Tests
        self._conn = connect(url)
        self._conn_lock = threading.RLock()
        self._ensure_account(initial_equity)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ oeffentliche API

    def submit(self, intent: OrderIntent, snapshot: MarketSnapshot) -> ExecutionResult:
        return self._run(snapshot.instrument, lambda engine: engine.submit(intent, snapshot), intent)

    def close_position(
        self, instrument: InstrumentId, snapshot: MarketSnapshot, reason: ExitReason, request_id: str
    ) -> CloseResult:
        return self._run(
            instrument, lambda engine: engine.close_position(instrument, snapshot, reason, request_id)
        )

    def on_candle(self, candle: Candle) -> List[ClosedTrade]:
        return self._run(getattr(candle, "instrument", None), lambda engine: engine.on_candle(candle))

    def apply_funding(self, event: FundingEvent) -> Decimal:
        return self._run(getattr(event, "instrument", None), lambda engine: engine.apply_funding(event))

    def portfolio_state(self) -> PortfolioState:
        # Schreibt nur, wenn sich Hoechststand oder Tageswechsel geaendert haben.
        return self._run(None, lambda engine: engine.portfolio_state())

    def equity(self) -> Decimal:
        return self.portfolio_state().equity

    @property
    def balance(self) -> Decimal:
        with self._transaction() as cur:
            return Decimal(self._read_account(cur)["balance"])

    @property
    def open_positions(self) -> Tuple[Position, ...]:
        with self._transaction() as cur:
            return tuple(self._load_positions(cur))

    @property
    def closed_trades(self) -> Tuple[ClosedTrade, ...]:
        with self._transaction() as cur:
            cur.execute(
                "SELECT * FROM closed_trades WHERE account_id = %s ORDER BY closed_at, position_id",
                (self._account_id,),
            )
            return tuple(_trade_from_row(r) for r in cur.fetchall())

    # ------------------------------------------------------------------ Transaktion und Sperre

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        with self._conn_lock, self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            yield cur

    def _run(
        self,
        instrument: Any,
        operation: Callable[[PaperExecutor], T],
        order: Optional[OrderIntent] = None,
    ) -> T:
        """Sperren -> laden -> Engine entscheidet -> Differenz und Protokoll speichern -> COMMIT."""
        with self._transaction() as cur:
            account = self._lock_account(cur)
            seen = order is not None and self._order_seen(cur, order.client_order_id)
            loaded = self._load(cur, account, instrument,
                                (order.client_order_id,) if order is not None and seen else ())
            result = operation(loaded.engine)
            new_order = None
            if order is not None and not seen:
                assert isinstance(result, ExecutionResult)
                new_order = (order.client_order_id, result)
            self._persist(cur, loaded, new_order)
            return result

    def _ensure_account(self, initial_equity: Decimal) -> None:
        if not is_positive_decimal(initial_equity):
            raise ValueError("Startkapital muss eine positive Decimal-Zahl sein.")
        with self._transaction() as cur:
            cur.execute(
                "INSERT INTO accounts (account_id, initial_equity, balance, equity, peak_equity,"
                " day, day_start_equity) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (account_id) DO NOTHING",
                (self._account_id, initial_equity, initial_equity, initial_equity, initial_equity,
                 self._clock.now().date(), initial_equity),
            )

    def _lock_account(self, cur: Any) -> Dict[str, Any]:
        """Der Kern der Absicherung: Zeilensperre auf dem Konto bis zum Ende der Transaktion."""
        cur.execute("SELECT * FROM accounts WHERE account_id = %s FOR UPDATE", (self._account_id,))
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("Konto %r existiert nicht." % self._account_id)
        if row["mode"] != "paper":
            raise RuntimeError("Konto ist nicht im PAPER-Modus.")  # kann per CHECK nicht vorkommen
        return dict(row)

    def _read_account(self, cur: Any) -> Dict[str, Any]:
        cur.execute("SELECT * FROM accounts WHERE account_id = %s", (self._account_id,))
        row = cur.fetchone()
        assert row is not None
        return dict(row)

    # ------------------------------------------------------------------ Laden

    def _order_seen(self, cur: Any, client_order_id: str) -> bool:
        cur.execute(
            "SELECT 1 FROM orders WHERE account_id = %s AND client_order_id = %s",
            (self._account_id, client_order_id),
        )
        return cur.fetchone() is not None

    def _load_positions(self, cur: Any) -> List[Position]:
        cur.execute(
            "SELECT * FROM positions WHERE account_id = %s AND status = 'OPEN' ORDER BY position_id",
            (self._account_id,),
        )
        return [_position_from_row(r) for r in cur.fetchall()]

    def _load(self, cur: Any, account: Dict[str, Any], instrument: Any, seen: Tuple[str, ...]) -> _Loaded:
        positions = self._load_positions(cur)
        wanted: Set[InstrumentId] = {p.instrument for p in positions}
        if is_instrument(instrument):
            wanted.add(instrument)

        instruments: Dict[InstrumentId, InstrumentRow] = {}
        for inst in sorted(wanted, key=lambda i: i.key):
            cur.execute(
                "SELECT mark, mark_ts, ref_price, ref_ts, last_candle_close FROM instrument_state"
                " WHERE exchange = %s AND market_type = %s AND symbol = %s",
                (inst.exchange.value, inst.market_type.value, inst.symbol),
            )
            r = cur.fetchone()
            instruments[inst] = _EMPTY_ROW if r is None else (
                r["mark"], r["mark_ts"], r["ref_price"], r["ref_ts"], r["last_candle_close"])

        validator = SnapshotValidator(self._risk.limits, self._clock)
        marks: Dict[InstrumentId, Mark] = {}
        candle_state: Dict[InstrumentId, datetime] = {}
        for inst, (mark, mark_ts, ref_price, ref_ts, last_close) in instruments.items():
            if mark is not None and mark_ts is not None:
                marks[inst] = Mark(mark, mark_ts)
            if ref_price is not None and ref_ts is not None:
                validator.restore_state(inst, (ref_ts, ref_price))
            if last_close is not None:
                candle_state[inst] = last_close

        sink = _CollectingLog(self._clock)
        engine = PaperExecutor(account["initial_equity"], self._risk, validator, sink, self._clock)
        engine.restore_state(
            ExecutorState(
                balance=account["balance"],
                positions=tuple(positions),
                marks=marks,
                last_candle_close=candle_state,
                peak_equity=account["peak_equity"],
                day=account["day"],
                day_start_equity=account["day_start_equity"],
                position_counter=int(account["position_counter"]),
            ),
            seen,
        )
        return _Loaded(account, engine, sink, validator, {p.position_id: p for p in positions}, instruments)

    # ------------------------------------------------------------------ Speichern

    def _persist(
        self, cur: Any, loaded: _Loaded, order: Optional[Tuple[str, ExecutionResult]]
    ) -> None:
        engine, account, before = loaded.engine, loaded.account, loaded.positions
        after = engine.export_state()
        after_by_id = {p.position_id: p for p in after.positions}
        closed = engine.closed_trades
        closed_at = {t.position_id: t.closed_at for t in closed}

        for pid, p in after_by_id.items():
            if pid not in before:
                self._insert_position(cur, p)
            elif p != before[pid]:
                self._update_position(cur, p)
        for pid in before:
            if pid not in after_by_id:
                cur.execute(
                    "UPDATE positions SET status = 'CLOSED', closed_at = %s"
                    " WHERE account_id = %s AND position_id = %s",
                    (closed_at.get(pid, self._clock.now()), self._account_id, pid),
                )
        for t in closed:
            self._insert_closed_trade(cur, t)

        new_vals = (after.balance, engine.equity(), after.peak_equity, after.day,
                    after.day_start_equity, after.position_counter)
        old_vals = (account["balance"], account["equity"], account["peak_equity"], account["day"],
                    account["day_start_equity"], int(account["position_counter"]))
        if new_vals != old_vals:
            cur.execute(
                "UPDATE accounts SET balance = %s, equity = %s, peak_equity = %s, day = %s,"
                " day_start_equity = %s, position_counter = %s, version = version + 1,"
                " updated_at = now() WHERE account_id = %s",
                (*new_vals, self._account_id),
            )

        for inst, old_row in loaded.instruments.items():
            mark = after.marks.get(inst)
            ref = loaded.validator.export_state(inst)
            new_row: InstrumentRow = (
                mark.price if mark else None, mark.time if mark else None,
                ref[1] if ref else None, ref[0] if ref else None,
                after.last_candle_close.get(inst),
            )
            if new_row != old_row:
                cur.execute(
                    "INSERT INTO instrument_state (exchange, market_type, symbol, mark, mark_ts,"
                    " ref_price, ref_ts, last_candle_close) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (exchange, market_type, symbol) DO UPDATE SET mark = EXCLUDED.mark,"
                    " mark_ts = EXCLUDED.mark_ts, ref_price = EXCLUDED.ref_price, ref_ts = EXCLUDED.ref_ts,"
                    " last_candle_close = EXCLUDED.last_candle_close, updated_at = now()",
                    (inst.exchange.value, inst.market_type.value, inst.symbol, *new_row),
                )

        if order is not None:
            oid, result = order
            cur.execute(
                "INSERT INTO orders (account_id, client_order_id, status, reasons, created_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (self._account_id, oid, result.status.value,
                 ",".join(r.value for r in result.reasons), self._clock.now()),
            )

        for ev in loaded.sink.events():  # Protokoll in DERSELBEN Transaktion wie der Handel
            append_raw(cur, ev.ts, ev.event_type, ev.payload_json)

        if self._fault_hook is not None:
            self._fault_hook("before_commit")

    def _insert_position(self, cur: Any, p: Position) -> None:
        cur.execute(
            "INSERT INTO positions (account_id, position_id, status, setup_id, strategy, exchange, symbol,"
            " market_type, side, quantity, entry_price, stop_loss, take_profit, leverage, opened_at,"
            " margin, risk_at_open, entry_fee, liquidation_price, last_funding_ts, funding_paid)"
            " VALUES (%s, %s, 'OPEN', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self._account_id, p.position_id, p.setup_id, p.strategy, p.instrument.exchange.value,
             p.instrument.symbol, p.instrument.market_type.value, p.side.value, p.quantity, p.entry_price,
             p.stop_loss, p.take_profit, p.leverage, p.opened_at, p.margin, p.risk_at_open, p.entry_fee,
             p.liquidation_price, p.last_funding_ts, p.funding_paid),
        )

    def _update_position(self, cur: Any, p: Position) -> None:
        cur.execute(
            "UPDATE positions SET last_funding_ts = %s, funding_paid = %s"
            " WHERE account_id = %s AND position_id = %s",
            (p.last_funding_ts, p.funding_paid, self._account_id, p.position_id),
        )

    def _insert_closed_trade(self, cur: Any, t: ClosedTrade) -> None:
        cur.execute(
            "INSERT INTO closed_trades (account_id, position_id, setup_id, strategy, exchange, symbol,"
            " market_type, side, quantity, entry_price, exit_price, opened_at, closed_at, exit_reason,"
            " gross_pnl, fees, funding, net_pnl, risk_at_open)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self._account_id, t.position_id, t.setup_id, t.strategy, t.instrument.exchange.value,
             t.instrument.symbol, t.instrument.market_type.value, t.side.value, t.quantity, t.entry_price,
             t.exit_price, t.opened_at, t.closed_at, t.exit_reason.value, t.gross_pnl, t.fees, t.funding,
             t.net_pnl, t.risk_at_open),
        )


def _instrument_from_row(r: Dict[str, Any]) -> InstrumentId:
    return InstrumentId(Exchange(r["exchange"]), MarketType(r["market_type"]), r["symbol"])


def _position_from_row(r: Dict[str, Any]) -> Position:
    return Position(
        position_id=r["position_id"], setup_id=r["setup_id"], strategy=r["strategy"],
        instrument=_instrument_from_row(r), side=Side(r["side"]),
        quantity=r["quantity"], entry_price=r["entry_price"], stop_loss=r["stop_loss"],
        take_profit=r["take_profit"], leverage=r["leverage"], opened_at=r["opened_at"],
        margin=r["margin"], risk_at_open=r["risk_at_open"], entry_fee=r["entry_fee"],
        liquidation_price=r["liquidation_price"], last_funding_ts=r["last_funding_ts"],
        funding_paid=r["funding_paid"],
    )


def _trade_from_row(r: Dict[str, Any]) -> ClosedTrade:
    return ClosedTrade(
        position_id=r["position_id"], setup_id=r["setup_id"], strategy=r["strategy"],
        instrument=_instrument_from_row(r), side=Side(r["side"]),
        quantity=r["quantity"], entry_price=r["entry_price"], exit_price=r["exit_price"],
        opened_at=r["opened_at"], closed_at=r["closed_at"], exit_reason=ExitReason(r["exit_reason"]),
        gross_pnl=r["gross_pnl"], fees=r["fees"], funding=r["funding"], net_pnl=r["net_pnl"],
        risk_at_open=r["risk_at_open"],
    )


__all__ = ["PostgresPaperExecutor"]
