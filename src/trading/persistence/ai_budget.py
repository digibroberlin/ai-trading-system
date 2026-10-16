"""KI-Budget in PostgreSQL (Migration 006): ueberlebt Neustarts, gilt fuer alle Prozesse.

Zwei Grenzen gelten gleichzeitig - die strengere gewinnt:
  1. `AIBudget` der Anwendung (Konfiguration, Standard 0),
  2. `ai_budget_limits` in der Datenbank (nur ein Administrator setzt sie, Standard 0; die Datenbank prueft sie bei jeder
     Reservierung selbst, auch wenn die Anwendung falsch konfiguriert waere).
Grenzen setzen und eine Sperre aufheben: `set_limits_as_admin` / `unblock_as_admin` (Administrator-Verbindung).
"""

from __future__ import annotations

import secrets
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Any, Iterator, Optional, Tuple

import psycopg

from trading.ai.budget import AIBudget, BudgetError, BudgetLedger, BudgetStatus, Reservation, check_amount
from trading.clock import Clock
from trading.persistence.db import ConnectionKeeper, DatabaseUnavailableError, connect

ZERO = Decimal(0)


def _spent(cur: Any, day: date) -> Tuple[Decimal, Decimal]:
    cur.execute(
        "SELECT COALESCE(SUM(COALESCE(actual, reserved)) FILTER (WHERE day = %s), 0),"
        " COALESCE(SUM(COALESCE(actual, reserved)), 0) FROM ai_budget_entries"
        " WHERE day >= date_trunc('month', %s::date)::date"
        " AND day < (date_trunc('month', %s::date) + interval '1 month')::date",
        (day, day, day),
    )
    row = cur.fetchone()
    return Decimal(row[0]), Decimal(row[1])


class PostgresBudgetLedger(BudgetLedger):
    def __init__(self, url: str, budget: AIBudget, clock: Clock) -> None:
        self._db = ConnectionKeeper(url)  # verbindet nach einem Abbruch neu (Audit H7); sonst BudgetError = kein Aufruf
        self._budget = budget
        self._clock = clock

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        conn = self._db.get()
        with conn.transaction(), conn.cursor() as cur:
            yield cur

    def reserve(self, amount: Decimal, agent: str, model: str) -> Reservation:
        check_amount(amount)
        now = self._clock.now()
        day = now.date()
        if amount > self._budget.per_call_usd:
            raise BudgetError("Aufruf (%s USD) ueber dem Einzelbudget der Anwendung." % amount)
        rid = "%s-%s" % (now.strftime("%Y%m%dT%H%M%S"), secrets.token_hex(6))
        try:
            with self._tx() as cur:
                cur.execute("SELECT blocked, block_reason FROM ai_budget_limits WHERE id = 1 FOR UPDATE")
                lim = cur.fetchone()
                if lim is None or lim[0]:
                    raise BudgetError("KI-Budget gesperrt: %s" % ((lim[1] if lim else "") or "Zeile fehlt"))
                today, month = _spent(cur, day)
                if today + amount > self._budget.daily_usd or month + amount > self._budget.monthly_usd:
                    raise BudgetError("Budget der Anwendung reicht nicht (heute %s, Monat %s, Aufruf %s)."
                                      % (today, month, amount))
                cur.execute(
                    "INSERT INTO ai_budget_entries (reservation_id, created_at, day, agent, model, reserved)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (rid, now, day, agent, model, amount),
                )
        except (psycopg.Error, DatabaseUnavailableError) as exc:  # Gurt der Datenbank oder keine Verbindung: kein Aufruf
            raise BudgetError("Reservierung verweigert: %s" % type(exc).__name__) from exc
        return Reservation(rid, amount, day, (day.year, day.month), agent, model)

    def settle(self, reservation: Reservation, actual: Optional[Decimal]) -> None:
        cost = reservation.amount if actual is None else actual
        check_amount(cost)
        with self._tx() as cur:
            cur.execute(
                "UPDATE ai_budget_entries SET actual = %s, settled_at = %s WHERE reservation_id = %s AND actual IS NULL",
                (cost, self._clock.now(), reservation.reservation_id),
            )
            if cur.rowcount != 1:
                raise BudgetError("Reservierung %s unbekannt oder schon abgerechnet." % reservation.reservation_id)
            if cost > reservation.amount:
                cur.execute(
                    "UPDATE ai_budget_limits SET blocked = true, block_reason = %s WHERE id = 1",
                    (("tatsaechliche Kosten %s ueber der Reservierung %s" % (cost, reservation.amount))[:500],),
                )

    def block(self, reason: str) -> None:
        with self._tx() as cur:
            cur.execute("UPDATE ai_budget_limits SET blocked = true, block_reason = %s WHERE id = 1 AND NOT blocked",
                        ((reason or "gesperrt")[:500],))

    def status(self) -> BudgetStatus:
        day = self._clock.now().date()
        try:
            with self._tx() as cur:
                cur.execute("SELECT per_call_usd, daily_usd, monthly_usd, blocked, block_reason"
                            " FROM ai_budget_limits WHERE id = 1")
                lim = cur.fetchone()
                today, month = _spent(cur, day)
        except (psycopg.Error, DatabaseUnavailableError) as exc:
            return BudgetStatus(day, ZERO, ZERO, AIBudget(), True, "nicht lesbar: %s" % type(exc).__name__)
        if lim is None:
            return BudgetStatus(day, today, month, AIBudget(), True, "Zeile fehlt")
        effective = AIBudget(min(self._budget.per_call_usd, lim[0]), min(self._budget.daily_usd, lim[1]),
                             min(self._budget.monthly_usd, lim[2]))
        return BudgetStatus(day, today, month, effective, bool(lim[3]), lim[4] or "")


def set_limits_as_admin(admin_url: str, budget: AIBudget, approved_by: str) -> None:
    """Setzt die Datenbank-Grenzen (nur nach Freigabe des Eigners; Name der Freigabe ist Pflicht)."""
    name = (approved_by or "").strip()
    if not name:
        raise BudgetError("Grenzen nur mit Namen der Freigabe.")
    with connect(admin_url) as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "UPDATE ai_budget_limits SET per_call_usd = %s, daily_usd = %s, monthly_usd = %s, approved_by = %s"
            " WHERE id = 1",
            (budget.per_call_usd, budget.daily_usd, budget.monthly_usd, name),
        )


def unblock_as_admin(admin_url: str, approved_by: str) -> None:
    name = (approved_by or "").strip()
    if not name:
        raise BudgetError("Entsperren nur mit Namen.")
    with connect(admin_url) as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("UPDATE ai_budget_limits SET blocked = false, block_reason = '', approved_by = %s WHERE id = 1",
                    (name,))


__all__ = ["PostgresBudgetLedger", "set_limits_as_admin", "unblock_as_admin"]
