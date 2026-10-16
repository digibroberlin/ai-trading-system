"""Verbindungsaufbau zur PostgreSQL-Datenbank - mit Wiederverbindung nach einem Abbruch (Audit H7)."""

from __future__ import annotations

import time
from typing import Any, Callable, Optional, Sequence

import psycopg

# UTC erzwingen (einheitliche Zeitstempel); kein endloses Warten auf Sperren (fail closed).
# idle_in_transaction_session_timeout: Haengt ein Prozess MITTEN in einer Transaktion (eingefroren, Debugger, abgerissene
# Verbindung), beendet der Server die Sitzung nach 15 s und gibt ihre Sperren frei - sonst blockierte sie u. a. die
# Not-Aus-Zeile und damit den Not-Aus selbst und alle anderen Konten. TCP-Keepalive erkennt tote Verbindungen in ~1 min.
_OPTIONS = ("-c timezone=UTC -c lock_timeout=10000 -c statement_timeout=30000"
            " -c idle_in_transaction_session_timeout=15000"
            " -c tcp_keepalives_idle=30 -c tcp_keepalives_interval=10 -c tcp_keepalives_count=3")
CONNECT_TIMEOUT_S = 5  # ein nicht erreichbarer Server haelt den Prozess nicht unbegrenzt fest
RETRY_DELAYS_S = (0.5, 1.0, 2.0)  # Wartezeiten zwischen den Verbindungsversuchen (insgesamt 4 Versuche)


class DatabaseUnavailableError(RuntimeError):
    """Die Datenbank ist nach mehreren Versuchen nicht erreichbar - die Aktion findet nicht statt (fail closed)."""


def connect(url: str, autocommit: bool = False) -> "psycopg.Connection[Any]":
    return psycopg.connect(url, autocommit=autocommit, options=_OPTIONS, connect_timeout=CONNECT_TIMEOUT_S)


class ConnectionKeeper:
    """Haelt EINE Verbindung und baut sie neu auf, wenn sie abgerissen ist (Server-Neustart, Netzwerk, beendete Sitzung).

    Vorher scheiterte nach einem Abbruch jeder weitere Aufruf bis zum Neustart des Prozesses. Jetzt prueft `get()` vor
    jeder Transaktion, ob die Verbindung noch gilt, und verbindet sonst neu - mit Wartezeiten, aber begrenzt: Ist der
    Server dauerhaft weg, kommt `DatabaseUnavailableError` (nie endloses Warten, nie Handeln ohne Datenbank).
    Eine Transaktion, die beim Abbruch lief, wird NICHT still wiederholt - das entscheidet der Aufrufer.
    """

    def __init__(
        self,
        url: str,
        autocommit: bool = False,
        delays: Sequence[float] = RETRY_DELAYS_S,
        sleep: Callable[[float], None] = time.sleep,
        connector: Optional[Callable[[str, bool], "psycopg.Connection[Any]"]] = None,
    ) -> None:
        self._url = url
        self._autocommit = autocommit
        self._delays = tuple(delays)
        self._sleep = sleep
        self._connector = connector if connector is not None else connect
        self._conn: Optional["psycopg.Connection[Any]"] = None
        self._closed = False  # bewusst geschlossen: dann nie wieder verbinden
        self.reconnects = 0  # wie oft neu verbunden wurde (fuer Tests und Ueberwachung)
        self.get()

    def is_lost(self) -> bool:
        """Ist die Verbindung unbrauchbar (abgerissen oder geschlossen)?"""
        return self._conn is None or self._conn.closed or self._conn.broken

    def get(self) -> "psycopg.Connection[Any]":
        if self._closed:
            raise DatabaseUnavailableError("Verbindung wurde geschlossen.")
        if self.is_lost():
            self._reconnect()
        assert self._conn is not None
        return self._conn

    def close(self) -> None:
        self._closed = True
        if self._conn is not None:
            self._conn.close()

    def _reconnect(self) -> None:
        first = self._conn is None
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 - die alte Verbindung ist ohnehin kaputt
                pass
            self._conn = None
        if first:  # beim Start nicht warten: falsche Adresse oder falsches Passwort sofort melden
            self._conn = self._connector(self._url, self._autocommit)
            return
        last: Optional[BaseException] = None
        for attempt in range(len(self._delays) + 1):
            if attempt > 0:
                self._sleep(self._delays[attempt - 1])
            try:
                self._conn = self._connector(self._url, self._autocommit)
            except psycopg.OperationalError as exc:  # Server (noch) nicht erreichbar
                last = exc
                continue
            self.reconnects += 1
            return
        raise DatabaseUnavailableError(
            "Datenbank nach %d Versuchen nicht erreichbar: %s" % (len(self._delays) + 1, type(last).__name__)
        ) from last
