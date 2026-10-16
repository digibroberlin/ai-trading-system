"""Wiederverbindung nach Verbindungsabbruch (AP-11, Audit H7).

Vorher scheiterte nach einem Abbruch jeder weitere Aufruf, bis der Prozess neu gestartet wurde. Hier wird die Sitzung
des Prozesses vom Server aus beendet (`pg_terminate_backend`) - so wie bei einem Datenbank-Neustart oder Netzwerkfehler.
"""

from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg", reason="psycopg nicht installiert - Datenbank-Tests uebersprungen")
from conftest import requires_db  # noqa: E402
from test_pg_executor import intent, pg_exec, snap  # noqa: E402

from trading.audit.events import EventType  # noqa: E402
from trading.domain.models import OrderStatus  # noqa: E402
from trading.persistence.db import DatabaseUnavailableError  # noqa: E402
from trading.persistence.kill_switch import PostgresKillSwitch  # noqa: E402
from trading.persistence.pg_event_log import PostgresEventLog  # noqa: E402
from trading.safety.kill_switch import HaltReason  # noqa: E402

pytestmark = requires_db


def kill(pg, keeper):
    """Beendet die Server-Sitzung dieser Verbindung (wie ein Abbruch von aussen)."""
    pid = keeper.get().info.backend_pid
    with psycopg.connect(pg.admin_url, autocommit=True) as c:
        assert c.execute("SELECT pg_terminate_backend(%s)", (pid,)).fetchone()[0] is True


def orders(pg):
    with psycopg.connect(pg.admin_url) as c:
        return c.execute("SELECT client_order_id, status FROM orders ORDER BY client_order_id").fetchall()


def unreachable(*_args):
    raise psycopg.OperationalError("Server nicht erreichbar (Test)")


def test_executor_recovers_after_the_connection_was_cut(pg):
    """Abbruch zwischen zwei Aktionen: Die naechste Aktion verbindet neu und gelingt - ohne Prozess-Neustart."""
    ex, clock = pg_exec(pg)
    assert ex.submit(intent("a"), snap(clock)).status is OrderStatus.FILLED
    kill(pg, ex._db)
    assert ex.submit(intent("b", symbol="ETH-USD"), snap(clock, symbol="ETH-USD")).status is OrderStatus.FILLED
    assert ex._db.reconnects == 1
    assert orders(pg) == [("a", "filled"), ("b", "filled")]
    assert len(ex.open_positions) == 2


def test_every_operation_recovers_not_only_orders(pg):
    ex, clock = pg_exec(pg)
    for read in (lambda: ex.portfolio_state(), lambda: ex.balance, lambda: ex.open_positions,
                 lambda: ex.closed_trades, lambda: ex.halt_status()):
        kill(pg, ex._db)
        value = read()
        assert value is not None
    status = ex.halt_status()
    assert not status.halted


def test_cut_during_commit_is_reported_and_never_repeated_silently(pg):
    """Reisst die Verbindung NACH der Entscheidung ab, ist der Ausgang ungewiss: kein stilles Wiederholen, der Fehler geht
    an den Aufrufer. Nichts ist gespeichert; dieselbe Order-ID erneut gesendet wird genau einmal ausgefuehrt."""
    holder = {}

    def cut(stage):
        if stage == "before_commit" and not holder.get("done"):
            holder["done"] = True
            kill(pg, holder["ex"]._db)

    ex, clock = pg_exec(pg, fault_hook=cut)
    holder["ex"] = ex
    with pytest.raises(psycopg.OperationalError):
        ex.submit(intent("a"), snap(clock))
    assert orders(pg) == []
    first = ex.submit(intent("a"), snap(clock))
    again = ex.submit(intent("a"), snap(clock))
    assert first.status is OrderStatus.FILLED and again.replayed and again.position == first.position
    assert orders(pg) == [("a", "filled")]
    assert len(ex.open_positions) == 1 and ex._db.reconnects == 1


def test_permanently_unreachable_database_blocks_with_bounded_retries(pg):
    """Bleibt der Server weg: begrenzte Versuche mit Wartezeit, dann ein klarer Fehler - nie ein Handel, nie endlos."""
    ex, clock = pg_exec(pg)
    waits = []
    ex._db._connector, ex._db._sleep = unreachable, waits.append
    kill(pg, ex._db)
    with pytest.raises(DatabaseUnavailableError):
        ex.submit(intent("a"), snap(clock))
    assert waits == [0.5, 1.0, 2.0]
    assert ex.halt_status().halted  # Zustand nicht lesbar -> gilt als gestoppt
    assert orders(pg) == []


def test_closed_executor_never_reconnects(pg):
    ex, clock = pg_exec(pg)
    ex.close()
    with pytest.raises(DatabaseUnavailableError):
        ex.submit(intent("a"), snap(clock))
    assert orders(pg) == []


def test_event_log_recovers_on_the_next_call(pg):
    """Ein Protokolleintrag, der beim Abbruch lief, wird nicht still wiederholt (er koennte schon gespeichert sein);
    der naechste Aufruf verbindet neu."""
    log = PostgresEventLog(pg.app_url)
    log.append(EventType.HALT_ACTIVATED, {"probe": 1})
    kill(pg, log._db)
    with pytest.raises(psycopg.OperationalError):
        log.append(EventType.HALT_ACTIVATED, {"probe": 2})
    log.append(EventType.HALT_ACTIVATED, {"probe": 3})
    assert [e.payload_json for e in log.events()] == ['{"probe":1}', '{"probe":3}']
    assert log.verify().ok
    log.close()


def test_kill_switch_status_fails_closed_then_recovers(pg):
    switch = PostgresKillSwitch(pg.app_url)
    assert not switch.status().halted
    kill(pg, switch._db)
    assert switch.status().halted  # in dem Moment nicht lesbar -> gestoppt
    assert not switch.status().halted  # neu verbunden: wahrer Zustand
    switch.close()


def test_external_halt_still_works_after_the_connection_was_cut(pg):
    """Der Not-Aus von aussen darf an einer abgerissenen Verbindung nicht scheitern."""
    switch = PostgresKillSwitch(pg.app_url)
    kill(pg, switch._db)
    assert switch.trip(HaltReason.MANUAL, "Test") is True
    assert switch.status().halted and switch.status().reason is HaltReason.MANUAL
    switch.close()


def test_errors_without_connection_loss_are_not_retried(pg):
    """Ein Fehler bei intakter Verbindung (z. B. Sperr-Zeitlimit, weil ein anderer Prozess das Konto haelt) ist kein
    Fall fuer die Wiederverbindung: Er geht sofort an den Aufrufer, nichts wird gespeichert."""
    ex, clock = pg_exec(pg)
    calls = []
    original = ex._lock_account

    def busy_once(cur):
        calls.append(1)
        if len(calls) == 1:
            raise psycopg.errors.LockNotAvailable("Konto gesperrt (Test)")
        return original(cur)

    ex._lock_account = busy_once
    with pytest.raises(psycopg.errors.LockNotAvailable):
        ex.submit(intent("a"), snap(clock))
    assert calls == [1] and orders(pg) == [] and ex._db.reconnects == 0
    assert ex.submit(intent("a"), snap(clock)).status is OrderStatus.FILLED
