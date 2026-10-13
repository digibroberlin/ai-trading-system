"""Gemeinsame pytest-Bausteine.

Datenbank-Tests brauchen eine laufende PostgreSQL-Instanz (docker-compose) und die Umgebungsvariablen
TEST_ADMIN_URL (Administrator, legt kurzlebige Test-Datenbanken an) und TEST_APP_PASSWORD
(Passwort der eingeschraenkten Rolle `trading_app`). Fehlen sie, werden diese Tests uebersprungen -
und das steht dann sichtbar im Testbericht ("skipped"), nicht als Erfolg.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Iterator, Optional

import pytest

ADMIN_URL = os.environ.get("TEST_ADMIN_URL")
APP_PASSWORD = os.environ.get("TEST_APP_PASSWORD")
HAVE_DB = bool(ADMIN_URL and APP_PASSWORD)
SCHEMA_SQL = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

requires_db = pytest.mark.skipif(
    not HAVE_DB, reason="PostgreSQL nicht konfiguriert (TEST_ADMIN_URL/TEST_APP_PASSWORD fehlen)"
)


class TestDatabase:
    """Eine frische, leere Datenbank mit dem Schema aus db/schema.sql."""

    __test__ = False  # kein Testfall fuer pytest

    def __init__(self, name: str, admin_url: str, app_url: str, admin_db_url: str) -> None:
        self.name = name
        self.admin_url = admin_db_url  # Administrator, auf diese Datenbank
        self.app_url = app_url  # eingeschraenkte Rolle trading_app


@pytest.fixture
def pg() -> Iterator[Optional[TestDatabase]]:
    if not HAVE_DB:
        pytest.skip("PostgreSQL nicht konfiguriert")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    assert ADMIN_URL and APP_PASSWORD
    name = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    admin_db_url = make_conninfo(ADMIN_URL, dbname=name)
    app_url = make_conninfo(ADMIN_URL, dbname=name, user="trading_app", password=APP_PASSWORD)
    try:
        with psycopg.connect(admin_db_url, autocommit=True) as conn:
            conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
            conn.execute(
                sql.SQL("ALTER ROLE trading_app LOGIN PASSWORD {}").format(sql.Literal(APP_PASSWORD))
            )
        yield TestDatabase(name, ADMIN_URL, app_url, admin_db_url)
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
