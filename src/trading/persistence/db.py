"""Verbindungsaufbau zur PostgreSQL-Datenbank."""

from __future__ import annotations

from typing import Any

import psycopg

# UTC erzwingen (einheitliche Zeitstempel); kein endloses Warten auf Sperren (fail closed).
_OPTIONS = "-c timezone=UTC -c lock_timeout=10000 -c statement_timeout=30000"


def connect(url: str, autocommit: bool = False) -> "psycopg.Connection[Any]":
    return psycopg.connect(url, autocommit=autocommit, options=_OPTIONS)
