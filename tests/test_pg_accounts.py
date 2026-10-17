"""Rechte der Anwendungsrolle am Konto (AP-10, Audit M2 Rest, Migration 005).

Konten legt nur ein Administrator an (jedes Konto hat ein eigenes 2-%-Budget), und jede Aenderung des Kontostands durch
die Anwendung muss plausibel sein: ein Versionsschritt, Tag und Positionszaehler nur vorwaerts, Tagesstart-Equity fest,
Hoechststand faellt nie und liegt nie unter der Equity.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg", reason="psycopg nicht installiert - Datenbank-Tests uebersprungen")
from conftest import requires_db  # noqa: E402
from helpers import D, T0  # noqa: E402
from test_pg_executor import intent, snap  # noqa: E402

from psycopg import errors as E  # noqa: E402
from trading.audit.events import EventType  # noqa: E402
from trading.clock import FixedClock  # noqa: E402
from trading.execution.costs import CostModel  # noqa: E402
from trading.persistence.accounts import AccountError, AccountMissingError, create_account  # noqa: E402
from trading.persistence.pg_event_log import PostgresEventLog  # noqa: E402
from trading.persistence.pg_executor import PostgresPaperExecutor  # noqa: E402
from trading.safety.limits import DEFAULT_LIMITS  # noqa: E402
from trading.safety.risk_manager import RiskManager  # noqa: E402

pytestmark = requires_db
ROOT = Path(__file__).resolve().parent.parent
NO_SLIP = CostModel(slippage_bps=D(0), stop_slippage_bps=D(0))


def admin(pg):
    return psycopg.connect(pg.admin_url, autocommit=True)


def app(pg):
    return psycopg.connect(pg.app_url, autocommit=True)


def account_rows(pg):
    with admin(pg) as c:
        return c.execute("SELECT account_id, initial_equity, balance, version FROM accounts ORDER BY 1").fetchall()


def executor(pg, account="main", clock=None):
    return PostgresPaperExecutor(pg.app_url, account, RiskManager(DEFAULT_LIMITS, NO_SLIP), clock or FixedClock(T0))


# ------------------------------------------------------------------ Konten nur administrativ


def test_application_role_cannot_create_accounts(pg):
    """Ein zweites Konto haette ein eigenes 2-%-Risikobudget - die Anwendung darf es nicht anlegen."""
    with app(pg) as c:
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("INSERT INTO accounts (account_id, initial_equity, balance, equity, peak_equity, day,"
                      " day_start_equity) VALUES ('zweites', 10000, 10000, 10000, 10000, '2025-01-01', 10000)")
    assert account_rows(pg) == []


def test_executor_refuses_to_start_without_an_account_and_creates_none(pg):
    with pytest.raises(AccountMissingError, match="Administrator"):
        executor(pg, "fehlt")
    assert account_rows(pg) == []


def test_create_account_as_administrator_is_logged_and_idempotent(pg):
    clock = FixedClock(T0)
    assert create_account(pg.admin_url, "main", D(10000), clock) is True
    assert create_account(pg.admin_url, "main", D(999999), clock) is False  # bestehendes Konto bleibt unveraendert
    assert account_rows(pg) == [("main", D(10000), D(10000), 0)]
    with admin(pg) as c:
        assert c.execute("SELECT day, day_start_equity, peak_equity, mode FROM accounts").fetchone() == (
            date(2025, 1, 1), D(10000), D(10000), "paper")
    log = PostgresEventLog(pg.app_url)
    (created,) = log.of_type(EventType.ACCOUNT_CREATED)
    assert created.payload == {"account_id": "main", "initial_equity": "10000", "mode": "paper"}
    assert len(log.events()) == 1 and log.verify().ok
    log.close()


def test_application_role_cannot_use_create_account(pg):
    with pytest.raises(E.InsufficientPrivilege):
        create_account(pg.app_url, "main", D(10000), FixedClock(T0))
    assert account_rows(pg) == []
    log = PostgresEventLog(pg.app_url)
    assert log.events() == ()  # kein Protokolleintrag ohne Konto (eine Transaktion)
    log.close()


@pytest.mark.parametrize("account_id", ["", " main", "a b", "x" * 65, "-start", "ä", None, 5])
def test_create_account_rejects_bad_names(pg, account_id):
    with pytest.raises(AccountError):
        create_account(pg.admin_url, account_id, D(10000), FixedClock(T0))
    assert account_rows(pg) == []


@pytest.mark.parametrize("equity", [D(0), D(-1), D("NaN"), D("Infinity"), 10000.0, 10000, None])
def test_create_account_rejects_bad_initial_equity(pg, equity):
    with pytest.raises(AccountError):
        create_account(pg.admin_url, "main", equity, FixedClock(T0))
    assert account_rows(pg) == []


def test_executor_trades_on_an_administratively_created_account(pg):
    create_account(pg.admin_url, "main", D(10000), FixedClock(T0))
    clock = FixedClock(T0)
    ex = executor(pg, clock=clock)
    assert ex.submit(intent("o1"), snap(clock)).status.value == "filled"
    assert ex.verify_protocol(PostgresEventLog(pg.app_url))
    assert ex.halt_status().halted is False
    (row,) = account_rows(pg)
    assert row[0] == "main" and row[2] < D(10000) and row[3] == 1
    ex.close()


def test_command_line_tool(pg):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1",
               DATABASE_URL=pg.app_url, ADMIN_DATABASE_URL=pg.admin_url)

    def cli(*args, **extra):
        return subprocess.run([sys.executable, "-m", "trading.persistence.accounts", *args],
                              env=dict(env, **extra), capture_output=True, text=True, timeout=60)

    done = cli("create", "demo-1", "2500.50")
    assert done.returncode == 0 and "angelegt" in done.stdout
    again = cli("create", "demo-1", "1")
    assert again.returncode == 0 and "bestand schon" in again.stdout
    refused = cli("create", "zweites", "1000", ADMIN_DATABASE_URL=pg.app_url)
    assert refused.returncode == 2 and "FEHLER" in refused.stderr and "Traceback" not in refused.stderr
    for bad in (("create", "x", "viel"), ("create", "x", "-5"), ("create", "x"), ("loeschen", "demo-1"), ()):
        result = cli(*bad)
        assert result.returncode == 2 and "Traceback" not in result.stderr
    listed = cli("list")
    assert listed.returncode == 0 and "demo-1" in listed.stdout and "2500.50" in listed.stdout
    assert account_rows(pg) == [("demo-1", D("2500.50"), D("2500.50"), 0)]


# ------------------------------------------------------------------ Plausibilitaet jeder Kontoaenderung

GOOD = "version = version + 1"


def _prepared(pg):
    pg.create_account("main", D(10000), date(2025, 1, 2))
    with admin(pg) as c:  # Ausgangslage: Equity unter dem Hoechststand, Positionszaehler > 0
        c.execute("UPDATE accounts SET equity = 9900, balance = 9900, peak_equity = 10100, position_counter = 3")


@pytest.mark.parametrize("change", [
    "version = version + 2",
    "balance = 1, version = version",
    "balance = 1, version = version - 1",
    "position_counter = 2, " + GOOD,
    "day = '2025-01-01', " + GOOD,
    "day_start_equity = 9000, " + GOOD,
    "peak_equity = 10099, " + GOOD,
    "equity = 10200, " + GOOD,  # Hoechststand unter der Equity
    "equity = 10200, peak_equity = 10150, " + GOOD,
])
def test_application_role_cannot_make_implausible_account_changes(pg, change):
    _prepared(pg)
    before = account_rows(pg)
    with app(pg) as c:
        with pytest.raises(E.CheckViolation):
            c.execute("UPDATE accounts SET " + change + " WHERE account_id = 'main'")
    assert account_rows(pg) == before


@pytest.mark.parametrize("change", [
    "balance = 9800, equity = 9800, " + GOOD,
    "position_counter = 4, " + GOOD,
    "day = '2025-01-03', day_start_equity = 9900, " + GOOD,  # neuer Tag: neue Tagesstart-Equity
    "equity = 10200, peak_equity = 10200, " + GOOD,
    "version = version",  # reine Sperre (Risiko-Gurt aus Migration 002)
])
def test_application_role_can_make_plausible_account_changes(pg, change):
    _prepared(pg)
    with app(pg) as c:
        c.execute("UPDATE accounts SET " + change + " WHERE account_id = 'main'")


def test_administrator_may_rearm_the_brakes(pg):
    """Die Freigabe des Not-Aus (Administrator) setzt Hoechststand und Tagesstart bewusst auf die aktuelle Equity."""
    _prepared(pg)
    with admin(pg) as c:
        c.execute("UPDATE accounts SET peak_equity = equity, day_start_equity = equity, version = version + 1")
        assert c.execute("SELECT peak_equity, day_start_equity FROM accounts").fetchone() == (D(9900), D(9900))


def test_guard_does_not_depend_on_a_specific_application_role_name(pg):
    """Auch eine andere Rolle ohne Administratorrechte (z. B. Mitglied von trading_app) unterliegt der Pruefung."""
    _prepared(pg)
    from psycopg import sql

    role = sql.Identifier("t_other_writer_" + uuid.uuid4().hex[:8])  # Rollen gelten serverweit: je Lauf ein eigener Name
    with admin(pg) as c:
        c.execute(sql.SQL("CREATE ROLE {} NOLOGIN IN ROLE trading_app").format(role))
        try:
            c.execute(sql.SQL("SET ROLE {}").format(role))
            with pytest.raises(E.CheckViolation):
                c.execute("UPDATE accounts SET peak_equity = 9950, " + GOOD)
            c.execute("RESET ROLE")
        finally:
            c.execute("RESET ROLE")
            c.execute(sql.SQL("DROP ROLE {}").format(role))
