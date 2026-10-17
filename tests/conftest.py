"""Gemeinsame pytest-Bausteine.

Datenbank-Tests brauchen eine laufende PostgreSQL-Instanz (docker-compose) und die Umgebungsvariablen
TEST_ADMIN_URL (Administrator, legt kurzlebige Test-Datenbanken an) und TEST_APP_PASSWORD
(Passwort der eingeschraenkten Rolle `trading_app`). Fehlen sie, werden diese Tests uebersprungen -
und das steht dann sichtbar im Testbericht ("skipped"), nicht als Erfolg.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Optional

import pytest

ADMIN_URL = os.environ.get("TEST_ADMIN_URL")
APP_PASSWORD = os.environ.get("TEST_APP_PASSWORD")
HAVE_DB = bool(ADMIN_URL and APP_PASSWORD)
MIGRATIONS = Path(__file__).resolve().parent.parent / "db" / "migrations"

requires_db = pytest.mark.skipif(
    not HAVE_DB, reason="PostgreSQL nicht konfiguriert (TEST_ADMIN_URL/TEST_APP_PASSWORD fehlen)"
)


class TestDatabase:
    """Eine frische, kurzlebige Datenbank. Das Schema entsteht ueber den Migrations-Laeufer."""

    __test__ = False  # kein Testfall fuer pytest

    def __init__(self, name: str, app_url: str, admin_db_url: str) -> None:
        self.name = name
        self.admin_url = admin_db_url  # Administrator, auf diese Datenbank
        self.app_url = app_url  # eingeschraenkte Rolle trading_app

    def create_account(self, account: str = "main", equity: Any = None, day: Any = None) -> None:
        """Konto als Administrator anlegen (die Anwendung darf das nicht, Migration 005). Ohne Protokolleintrag -
        reine Testvorbereitung; der echte Weg `trading.persistence.accounts.create_account` hat eigene Tests.
        Besteht das Konto schon, bleibt es unveraendert."""
        import psycopg

        equity = Decimal(10000) if equity is None else equity
        day = date(2025, 1, 1) if day is None else day
        with psycopg.connect(self.admin_url) as c:
            c.execute(
                "INSERT INTO accounts (account_id, initial_equity, balance, equity, peak_equity, day, day_start_equity)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (account_id) DO NOTHING",
                (account, equity, equity, equity, equity, day, equity),
            )


ROLE_LOCK_KEY = 7_460_123  # beliebig, nur fuer die Testumgebung


def _database(target: Optional[int]) -> Iterator[TestDatabase]:
    if not HAVE_DB:
        pytest.skip("PostgreSQL nicht konfiguriert")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    from trading.persistence.migrate import migrate

    assert ADMIN_URL and APP_PASSWORD
    name = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    admin_db_url = make_conninfo(ADMIN_URL, dbname=name)
    app_url = make_conninfo(ADMIN_URL, dbname=name, user="trading_app", password=APP_PASSWORD)
    try:
        # Die Rolle trading_app gilt fuer den ganzen Server: Laufen mehrere Testlaeufe gleichzeitig (Gegenprobe in
        # Gruppen), duerfen sie sie nicht gleichzeitig aendern ("tuple concurrently updated"). Die Sperre liegt in der
        # gemeinsamen Verwaltungs-Datenbank, damit sie ueber alle Test-Datenbanken hinweg wirkt.
        with psycopg.connect(ADMIN_URL, autocommit=True) as lock:
            lock.execute("SELECT pg_advisory_lock(%s)", (ROLE_LOCK_KEY,))
            migrate(admin_db_url, MIGRATIONS, target=target)
            with psycopg.connect(admin_db_url, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("ALTER ROLE trading_app LOGIN PASSWORD {}").format(sql.Literal(APP_PASSWORD))
                )
            lock.execute("SELECT pg_advisory_unlock(%s)", (ROLE_LOCK_KEY,))
        yield TestDatabase(name, app_url, admin_db_url)
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def pg() -> Iterator[TestDatabase]:
    """Datenbank auf dem aktuellen Schema-Stand (alle Migrationen)."""
    yield from _database(None)


@pytest.fixture
def pg_v1() -> Iterator[TestDatabase]:
    """Datenbank auf dem Stand von Migration 001 (so sah sie vor Stufe 1 aus) - fuer Migrationstests."""
    yield from _database(1)
