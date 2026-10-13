"""Paper Executor mit PostgreSQL als einziger Quelle der Wahrheit.

Wie mehrere Prozesse sicher zusammenarbeiten ("Teamrisiko"):

  1. Jede Aktion (Order, Kerze, Portfolio-Abfrage) ist EINE Datenbank-Transaktion.
  2. Gleich zu Beginn wird die Konto-Zeile gesperrt (`SELECT ... FOR UPDATE`). Alle anderen
     Prozesse warten dort in einer Schlange - fuer dieses Konto arbeitet immer nur einer.
  3. Unter der Sperre wird der aktuelle Zustand geladen und die BEREITS GETESTETE Engine
     (`PaperExecutor` samt unabhaengigem Risikomanager) entscheidet. Es gibt keine zweite,
     abweichende Rechenlogik.
  4. Zustand, Positionen, Order-ID, Handelsergebnisse UND Protokolleintraege werden in
     derselben Transaktion gespeichert. Stuerzt der Prozess vorher ab, passiert nichts
     (Rollback) - es bleiben keine halben Handel zurueck.
  5. Zweiter Gurt: Beim COMMIT prueft die Datenbank das 2-%-Limit erneut (Constraint-Trigger).

Ein Neustart ist daher trivial: Es gibt keinen Zustand im Prozess, der verloren gehen koennte.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from psycopg.rows import dict_row

from trading.audit.event_log import EventLog
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
    PortfolioState,
    Position,
    Side,
)
from trading.execution.paper_executor import ExecutorState, PaperExecutor
from trading.persistence.db import connect
from trading.persistence.pg_event_log import append_raw
from trading.safety.data_validator import SnapshotValidator
from trading.safety.risk_manager import RiskManager

FaultHook = Callable[[str], None]


class _CollectingLog(EventLog):
    """Sammelt die Ereignisse der Engine; sie werden danach in die DB-Kette uebernommen."""

    def _persist(self, event: Any) -> None:
        return None


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
        with self._transaction() as cur:
            acct = self._lock_account(cur)
            seen = self._order_seen(cur, intent.client_order_id)
            engine, sink, before = self._load(
                cur, acct, snapshot.symbol, seen=(intent.client_order_id,) if seen else ()
            )
            result = engine.submit(intent, snapshot)
            order = None if seen else (intent.client_order_id, result)
            self._persist(cur, acct, engine, sink, before, snapshot.symbol, order)
            return result

    def on_candle(
        self, candle: Candle, funding_rate: Decimal = Decimal(0)
    ) -> List[ClosedTrade]:
        with self._transaction() as cur:
            acct = self._lock_account(cur)
            engine, sink, before = self._load(cur, acct, candle.symbol, seen=())
            closed = engine.on_candle(candle, funding_rate)
            self._persist(cur, acct, engine, sink, before, candle.symbol, None)
            return closed

    def portfolio_state(self) -> PortfolioState:
        with self._transaction() as cur:
            acct = self._lock_account(cur)
            engine, sink, before = self._load(cur, acct, None, seen=())
            state = engine.portfolio_state()
            self._persist(cur, acct, engine, sink, before, None, None)  # speichert ggf. neuen Hoechststand
            return state

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

    def _ensure_account(self, initial_equity: Decimal) -> None:
        if initial_equity <= 0:
            raise ValueError("Startkapital muss positiv sein.")
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

    def _load(
        self, cur: Any, acct: Dict[str, Any], symbol: Optional[str], seen: Tuple[str, ...]
    ) -> Tuple[PaperExecutor, _CollectingLog, Dict[str, Position]]:
        positions = self._load_positions(cur)
        symbols = sorted({p.symbol for p in positions} | ({symbol} if symbol else set()))
        marks: Dict[str, Decimal] = {}
        last_ts: Optional[datetime] = None
        last_price: Optional[Decimal] = None
        if symbols:
            cur.execute(
                "SELECT symbol, mark, last_ts, last_price FROM market_state WHERE symbol = ANY(%s)",
                (symbols,),
            )
            for r in cur.fetchall():
                if r["mark"] is not None:
                    marks[r["symbol"]] = r["mark"]
                if r["symbol"] == symbol:
                    last_ts, last_price = r["last_ts"], r["last_price"]

        sink = _CollectingLog(self._clock)
        validator = SnapshotValidator(self._risk.limits, self._clock)
        if symbol is not None:
            validator.restore_state(symbol, last_ts, last_price)
        engine = PaperExecutor(acct["initial_equity"], self._risk, validator, sink, self._clock)
        engine.restore_state(
            ExecutorState(
                balance=acct["balance"],
                positions=tuple(positions),
                marks=marks,
                peak_equity=acct["peak_equity"],
                day=acct["day"],
                day_start_equity=acct["day_start_equity"],
                position_counter=int(acct["position_counter"]),
            ),
            seen,
        )
        self._validator = validator  # fuer _persist (letzter gueltiger Tick)
        return engine, sink, {p.position_id: p for p in positions}

    # ------------------------------------------------------------------ Speichern

    def _persist(
        self,
        cur: Any,
        acct: Dict[str, Any],
        engine: PaperExecutor,
        sink: _CollectingLog,
        before: Dict[str, Position],
        symbol: Optional[str],
        order: Optional[Tuple[str, ExecutionResult]],
    ) -> None:
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

        equity = engine.equity()
        new_vals = (after.balance, equity, after.peak_equity, after.day, after.day_start_equity,
                    after.position_counter)
        old_vals = (acct["balance"], acct["equity"], acct["peak_equity"], acct["day"],
                    acct["day_start_equity"], int(acct["position_counter"]))
        if new_vals != old_vals:
            cur.execute(
                "UPDATE accounts SET balance = %s, equity = %s, peak_equity = %s, day = %s,"
                " day_start_equity = %s, position_counter = %s, version = version + 1,"
                " updated_at = now() WHERE account_id = %s",
                (*new_vals, self._account_id),
            )

        for sym, mark in after.marks.items():
            ts, price = (None, None)
            if sym == symbol:
                ts, price = self._validator.export_state(sym)
            cur.execute(
                "INSERT INTO market_state (symbol, mark, last_ts, last_price) VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (symbol) DO UPDATE SET mark = EXCLUDED.mark,"
                " last_ts = COALESCE(EXCLUDED.last_ts, market_state.last_ts),"
                " last_price = COALESCE(EXCLUDED.last_price, market_state.last_price),"
                " updated_at = now()",
                (sym, mark, ts, price),
            )

        if order is not None:
            oid, result = order
            cur.execute(
                "INSERT INTO orders (account_id, client_order_id, status, reasons, created_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (self._account_id, oid, result.status.value,
                 ",".join(r.value for r in result.reasons), self._clock.now()),
            )

        for ev in sink.events():  # Protokoll in DERSELBEN Transaktion wie der Handel
            append_raw(cur, ev.ts, ev.event_type, ev.payload_json)

        if self._fault_hook is not None:
            self._fault_hook("before_commit")

    def _insert_position(self, cur: Any, p: Position) -> None:
        cur.execute(
            "INSERT INTO positions (account_id, position_id, status, setup_id, strategy, symbol,"
            " market_type, side, quantity, entry_price, stop_loss, take_profit, leverage, opened_at,"
            " margin, risk_at_open, entry_fee, liquidation_price, last_funding_ts, funding_paid)"
            " VALUES (%s, %s, 'OPEN', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self._account_id, p.position_id, p.setup_id, p.strategy, p.symbol, p.market_type.value,
             p.side.value, p.quantity, p.entry_price, p.stop_loss, p.take_profit, p.leverage,
             p.opened_at, p.margin, p.risk_at_open, p.entry_fee, p.liquidation_price,
             p.last_funding_ts, p.funding_paid),
        )

    def _update_position(self, cur: Any, p: Position) -> None:
        cur.execute(
            "UPDATE positions SET last_funding_ts = %s, funding_paid = %s"
            " WHERE account_id = %s AND position_id = %s",
            (p.last_funding_ts, p.funding_paid, self._account_id, p.position_id),
        )

    def _insert_closed_trade(self, cur: Any, t: ClosedTrade) -> None:
        cur.execute(
            "INSERT INTO closed_trades (account_id, position_id, setup_id, strategy, symbol,"
            " market_type, side, quantity, entry_price, exit_price, opened_at, closed_at, exit_reason,"
            " gross_pnl, fees, funding, net_pnl, risk_at_open)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self._account_id, t.position_id, t.setup_id, t.strategy, t.symbol, t.market_type.value,
             t.side.value, t.quantity, t.entry_price, t.exit_price, t.opened_at, t.closed_at,
             t.exit_reason.value, t.gross_pnl, t.fees, t.funding, t.net_pnl, t.risk_at_open),
        )


def _position_from_row(r: Dict[str, Any]) -> Position:
    return Position(
        position_id=r["position_id"], setup_id=r["setup_id"], strategy=r["strategy"],
        symbol=r["symbol"], market_type=MarketType(r["market_type"]), side=Side(r["side"]),
        quantity=r["quantity"], entry_price=r["entry_price"], stop_loss=r["stop_loss"],
        take_profit=r["take_profit"], leverage=r["leverage"], opened_at=r["opened_at"],
        margin=r["margin"], risk_at_open=r["risk_at_open"], entry_fee=r["entry_fee"],
        liquidation_price=r["liquidation_price"], last_funding_ts=r["last_funding_ts"],
        funding_paid=r["funding_paid"],
    )


def _trade_from_row(r: Dict[str, Any]) -> ClosedTrade:
    return ClosedTrade(
        position_id=r["position_id"], setup_id=r["setup_id"], strategy=r["strategy"],
        symbol=r["symbol"], market_type=MarketType(r["market_type"]), side=Side(r["side"]),
        quantity=r["quantity"], entry_price=r["entry_price"], exit_price=r["exit_price"],
        opened_at=r["opened_at"], closed_at=r["closed_at"], exit_reason=ExitReason(r["exit_reason"]),
        gross_pnl=r["gross_pnl"], fees=r["fees"], funding=r["funding"], net_pnl=r["net_pnl"],
        risk_at_open=r["risk_at_open"],
    )


__all__ = ["PostgresPaperExecutor"]
