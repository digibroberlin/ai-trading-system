"""Verbindungsaufbau zur PostgreSQL-Datenbank."""

from __future__ import annotations

from typing import Any

import psycopg

# UTC erzwingen (einheitliche Zeitstempel); kein endloses Warten auf Sperren (fail closed).
# idle_in_transaction_session_timeout: Haengt ein Prozess MITTEN in einer Transaktion (eingefroren, Debugger, abgerissene
# Verbindung), beendet der Server die Sitzung nach 15 s und gibt ihre Sperren frei - sonst blockierte sie u. a. die
# Not-Aus-Zeile und damit den Not-Aus selbst und alle anderen Konten. TCP-Keepalive erkennt tote Verbindungen in ~1 min.
_OPTIONS = ("-c timezone=UTC -c lock_timeout=10000 -c statement_timeout=30000"
            " -c idle_in_transaction_session_timeout=15000"
            " -c tcp_keepalives_idle=30 -c tcp_keepalives_interval=10 -c tcp_keepalives_count=3")


def connect(url: str, autocommit: bool = False) -> "psycopg.Connection[Any]":
    return psycopg.connect(url, autocommit=autocommit, options=_OPTIONS)
