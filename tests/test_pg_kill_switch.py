"""Globaler Not-Aus in PostgreSQL (AP-07): dauerhaft, fuer alle Konten und Prozesse, Datenbank-Gurte, nur ein
Administrator hebt ihn auf."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Ohne psycopg (z. B. Python 3.9, Kern-Pruefung) sichtbar ueberspringen statt beim Einsammeln abzubrechen.
psycopg = pytest.importorskip("psycopg", reason="psycopg nicht installiert - Datenbank-Tests uebersprungen")
from conftest import requires_db  # noqa: E402
from helpers import D, T0, make_candle  # noqa: E402
from test_pg_executor import feed, intent, pg_exec, snap  # noqa: E402

from psycopg import errors as E  # noqa: E402
from trading.audit.events import EventType  # noqa: E402
from trading.domain.models import ExitReason, OrderStatus  # noqa: E402
from trading.domain.reasons import RejectReason as R  # noqa: E402
from trading.persistence.kill_switch import PostgresKillSwitch  # noqa: E402
from trading.persistence.pg_event_log import PostgresEventLog  # noqa: E402
from trading.persistence.pg_executor import ProtocolWriteError  # noqa: E402
from trading.safety.kill_switch import RELEASE_CONFIRMATION, HaltReason, KillSwitchError  # noqa: E402

pytestmark = requires_db
ROOT = Path(__file__).resolve().parent.parent


def admin(pg):
    return psycopg.connect(pg.admin_url, autocommit=True)


def app(pg):
    return psycopg.connect(pg.app_url, autocommit=True)


def halt_row(pg):
    with admin(pg) as c:
        return c.execute("SELECT halted, reason, note, released_by FROM trading_halt").fetchone()


def release(pg, name="Eigner"):
    switch = PostgresKillSwitch(pg.admin_url)
    switch.release(name, RELEASE_CONFIRMATION)
    switch.close()


# ------------------------------------------------------------------ Datenbank-Gurte


def test_fresh_database_is_not_halted(pg):
    assert halt_row(pg) == (False, None, "", None)


def test_application_role_can_halt_but_never_release(pg):
    with app(pg) as c:
        c.execute("UPDATE trading_halt SET halted = true, reason = 'MANUAL', since = now(), note = 'x'")
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("UPDATE trading_halt SET halted = false")
        with pytest.raises(E.InsufficientPrivilege):  # auch nicht ueber die Freigabe-Spalten
            c.execute("UPDATE trading_halt SET halted = false, released_by = 'app', released_at = now()")
        with pytest.raises(E.InsufficientPrivilege):  # erster Grund bleibt stehen
            c.execute("UPDATE trading_halt SET reason = 'DATA_ERROR'")
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("UPDATE trading_halt SET note = 'anders'")
        for stmt in ("DELETE FROM trading_halt", "INSERT INTO trading_halt (id, halted) VALUES (1, false)",
                     "TRUNCATE trading_halt"):
            with pytest.raises(E.InsufficientPrivilege):
                c.execute(stmt)
    assert halt_row(pg) == (True, "MANUAL", "x", None)


@pytest.mark.parametrize("stmt", ["DELETE FROM trading_halt", "INSERT INTO trading_halt (id, halted) VALUES (2, false)",
                                  "TRUNCATE trading_halt"])
def test_even_the_administrator_cannot_delete_insert_or_truncate_the_halt_row(pg, stmt):
    """Die Trigger (nicht nur fehlende Rechte) verhindern das Loeschen/Neuanlegen - sonst liesse sich der Not-Aus
    ohne Namen 'aufheben', indem man die Zeile loescht und mit halted=false neu einfuegt."""
    with app(pg) as c:
        c.execute("UPDATE trading_halt SET halted = true, reason = 'MANUAL', since = now()")
    with admin(pg) as c, pytest.raises(E.InsufficientPrivilege, match="nicht erlaubt"):
        c.execute(stmt)
    assert halt_row(pg)[0] is True


def test_second_release_needs_its_own_name_and_time(pg):
    """Nach einer ersten Freigabe stehen Name und Zeitpunkt in der Zeile - eine spaetere Freigabe darf sie nicht erben."""
    ex, clock = pg_exec(pg)
    ex.halt("eins")
    release(pg)
    ex.halt("zwei")
    with admin(pg) as c, pytest.raises(E.CheckViolation):
        c.execute("UPDATE trading_halt SET halted = false")  # alte released_by/released_at wuerden sonst gelten
    with admin(pg) as c, pytest.raises(E.CheckViolation):  # Zeitpunkt vor dem Ausloesen
        c.execute("UPDATE trading_halt SET halted = false, released_by = 'X', released_at = since - interval '1 s'")
    assert halt_row(pg)[0] is True
    release(pg, "Zweite Person")
    assert halt_row(pg) == (False, None, "", "Zweite Person")


def test_halt_needs_reason_and_time(pg):
    with app(pg) as c, pytest.raises(E.CheckViolation):
        c.execute("UPDATE trading_halt SET halted = true")


def test_administrator_releases_only_with_a_name(pg):
    with app(pg) as c:
        c.execute("UPDATE trading_halt SET halted = true, reason = 'MANUAL', since = now()")
    with admin(pg) as c:
        with pytest.raises(E.CheckViolation):
            c.execute("UPDATE trading_halt SET halted = false")
        with pytest.raises(E.CheckViolation):
            c.execute("UPDATE trading_halt SET halted = false, released_by = '  ', released_at = now()")
        c.execute("UPDATE trading_halt SET halted = false, released_by = 'Eigner', released_at = now()")
    assert halt_row(pg) == (False, None, "", "Eigner")


def test_database_refuses_new_positions_while_halted_even_without_the_engine(pg):
    ex, clock = pg_exec(pg)
    assert ex.submit(intent("a"), snap(clock)).status is OrderStatus.FILLED
    ex.halt("Test")
    with app(pg) as c, pytest.raises(E.CheckViolation, match="Not-Aus"):
        c.execute(
            "INSERT INTO positions (account_id, position_id, status, setup_id, strategy, exchange, symbol, market_type,"
            " side, quantity, entry_price, stop_loss, take_profit, leverage, opened_at, margin, risk_at_open, entry_fee,"
            " last_funding_ts) VALUES ('main','P9','OPEN','s','t','synthetic','X-USD','spot','long',1,100,99,102,1,"
            " now(),100,1.1,0.1,now())")
    with app(pg) as c:  # Schliessen bleibt erlaubt
        c.execute("UPDATE positions SET status = 'CLOSED', closed_at = now() WHERE position_id = 'P000001'")


# ------------------------------------------------------------------ Executor ueber die Datenbank


def test_halt_applies_to_every_account_and_every_process_and_survives_restart(pg):
    a, clock = pg_exec(pg, account="main")
    b, _ = pg_exec(pg, clock, account="second")
    assert a.submit(intent("a1"), snap(clock)).status is OrderStatus.FILLED
    assert a.halt("Wartung") is True and b.halt("noch einmal") is False
    assert b.submit(intent("b1"), snap(clock)).reasons == (R.TRADING_HALTED,)
    restarted, _ = pg_exec(pg, clock, account="main")
    status = restarted.halt_status()
    assert status.halted and status.reason is HaltReason.MANUAL and status.note == "Wartung"
    assert restarted.submit(intent("a2", "ETH-USD"), snap(clock, "ETH-USD")).reasons == (R.TRADING_HALTED,)
    # Schliessen geht weiter: Ausstiegssignal und Stop-Loss.
    assert restarted.close_position(intent("x").setup.instrument, snap(clock), ExitReason.SIGNAL, "exit").closed
    log = PostgresEventLog(pg.app_url)
    assert [e.payload["reason"] for e in log.of_type(EventType.HALT_ACTIVATED)] == ["MANUAL"]
    assert log.verify().ok


def test_application_can_not_release_through_the_executor(pg):
    ex, clock = pg_exec(pg)
    ex.halt("Test")
    with pytest.raises(RuntimeError, match="Administrator"):
        ex._run(None, lambda engine: engine.release_halt("App", RELEASE_CONFIRMATION))
    assert halt_row(pg)[0] is True


def test_administrator_release_is_logged_and_resumes_trading(pg):
    ex, clock = pg_exec(pg)
    ex.halt("Test")
    with pytest.raises(KillSwitchError):  # ohne Bestaetigung keine Freigabe
        PostgresKillSwitch(pg.admin_url).release("Eigner", "ja")
    with pytest.raises(E.InsufficientPrivilege):  # mit der Anwendungsrolle auch MIT Bestaetigung nicht
        PostgresKillSwitch(pg.app_url).release("Eigner", RELEASE_CONFIRMATION)
    release(pg)
    assert halt_row(pg) == (False, None, "", "Eigner")
    assert ex.submit(intent("a"), snap(clock)).status is OrderStatus.FILLED
    released = PostgresEventLog(pg.app_url).of_type(EventType.HALT_RELEASED)
    assert released[0].payload["operator"] == "Eigner" and released[0].payload["previous_reason"] == "MANUAL"


def test_data_error_trips_the_global_halt_in_the_same_transaction(pg):
    ex, clock = pg_exec(pg)
    clock.set(T0)
    import dataclasses
    bad = dataclasses.replace(make_candle(T0, D(100), D(101), D(99), D(100)), high=D("NaN"))
    clock.advance(120)
    assert ex.on_candle(bad) == []
    assert halt_row(pg)[:2] == (True, "DATA_ERROR")
    log = PostgresEventLog(pg.app_url)
    assert [e.event_type for e in log.events()][-2:] == ["DATA_REJECTED", "HALT_ACTIVATED"]
    assert log.verify().ok


def test_protocol_write_failure_rolls_back_and_trips_the_halt(pg):
    ex, clock = pg_exec(pg)
    with admin(pg) as c:  # Protokoll kann ORDER_FILLED nicht mehr speichern (simulierter Defekt)
        c.execute("ALTER TABLE event_log ADD CONSTRAINT simulated_defect CHECK (event_type <> 'ORDER_FILLED')")
    with pytest.raises(ProtocolWriteError):
        ex.submit(intent("a"), snap(clock))
    assert ex.open_positions == ()
    assert halt_row(pg)[:2] == (True, "PROTOCOL_ERROR")
    log = PostgresEventLog(pg.app_url)
    assert [e.event_type for e in log.events()] == ["HALT_ACTIVATED"]
    with admin(pg) as c:
        c.execute("ALTER TABLE event_log DROP CONSTRAINT simulated_defect")
    assert ex.submit(intent("b"), snap(clock)).reasons == (R.TRADING_HALTED,)


def test_halt_is_stored_even_when_the_protocol_refuses_everything(pg):
    ex, clock = pg_exec(pg)
    with admin(pg) as c:
        c.execute("ALTER TABLE event_log ADD CONSTRAINT simulated_defect CHECK (false)")
    with pytest.raises(ProtocolWriteError):
        ex.submit(intent("a"), snap(clock))
    assert halt_row(pg)[:2] == (True, "PROTOCOL_ERROR")


def test_damaged_protocol_found_by_verification_trips_the_halt(pg):
    ex, clock = pg_exec(pg)
    assert ex.submit(intent("a"), snap(clock)).status is OrderStatus.FILLED
    log = PostgresEventLog(pg.app_url)
    assert ex.verify_protocol(log) is True and halt_row(pg)[0] is False
    with admin(pg) as c:
        c.execute("ALTER TABLE event_log DISABLE TRIGGER ALL")
        c.execute("UPDATE event_log SET payload_json = '{}' WHERE seq = 1")
        c.execute("ALTER TABLE event_log ENABLE TRIGGER ALL")
    assert ex.verify_protocol(log) is False
    assert halt_row(pg)[:2] == (True, "PROTOCOL_ERROR")


def test_unreachable_database_counts_as_halted(pg):
    switch = PostgresKillSwitch(pg.app_url)
    switch.close()
    assert switch.status().halted
    ex, _ = pg_exec(pg)
    ex.close()
    assert ex.halt_status().halted


def test_command_line_tool(pg):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1",
               DATABASE_URL=pg.app_url, ADMIN_DATABASE_URL=pg.admin_url)

    def cli(*args, **extra):
        return subprocess.run([sys.executable, "-m", "trading.persistence.kill_switch", *args],
                              env=dict(env, **extra), capture_output=True, text=True, timeout=60)

    assert cli("status").returncode == 0
    halted = cli("halt", "Test", "von", "Hand")
    assert halted.returncode == 0 and "GESTOPPT" in halted.stdout and "Test von Hand" in halted.stdout
    assert cli("status").returncode == 1
    refused = cli("release", "Eigner", "ja")
    assert refused.returncode == 2 and "FEHLER" in refused.stderr and "Traceback" not in refused.stderr
    assert cli("release", "Eigner", RELEASE_CONFIRMATION, ADMIN_DATABASE_URL=pg.app_url).returncode == 2
    assert cli("release", "Eigner").returncode == 2
    assert halt_row(pg)[0] is True
    done = cli("release", "Eigner", RELEASE_CONFIRMATION)
    assert done.returncode == 0 and "frei" in done.stdout
    assert halt_row(pg)[0] is False
    broken = cli("halt", "x", DATABASE_URL="postgresql://trading_app@127.0.0.1:1/nirgends")
    assert broken.returncode == 2 and "Traceback" not in broken.stderr


# ------------------------------------------------------------------ mehrere Prozesse


@pytest.mark.parametrize("run", range(3))
def test_no_process_opens_a_position_after_the_halt_is_committed(pg, run):
    """Sechs Prozesse handeln auf sechs Konten (je 4 Orders - jede haette Platz), zwei weitere loesen gleichzeitig den
    Not-Aus aus, sobald 3 Positionen offen sind. Die Protokoll-Reihenfolge ist die Commit-Reihenfolge: Vor dem Not-Aus
    gab es Fills, danach darf es keinen ORDER_FILLED mehr geben, und jede spaetere Ablehnung nennt nur TRADING_HALTED."""
    accounts = ["k%d" % i for i in range(6)]
    for account in accounts:
        pg_exec(pg, account=account)[0].close()
    orders = [dict(url=pg.app_url, account=acc, qty=1, specs=[["%s-%d" % (acc, i), "H%d-USD" % i] for i in range(4)])
              for acc in accounts]
    halts = [dict(url=pg.app_url, account=accounts[0], after_fills=3, specs=[])] * 2
    res = _run_mixed(orders, halts)
    assert sorted(r["halt"] for r in res if "halt" in r) == [False, True]  # genau einer loest aus
    events = PostgresEventLog(pg.app_url).events()
    halt_seq = [e.seq for e in events if e.event_type == "HALT_ACTIVATED"]
    assert len(halt_seq) == 1
    fills_before = [e for e in events if e.event_type == "ORDER_FILLED" and e.seq < halt_seq[0]]
    assert len(fills_before) >= 3  # der Wettlauf hat wirklich stattgefunden
    fills_after = [e for e in events if e.event_type == "ORDER_FILLED" and e.seq > halt_seq[0]]
    assert fills_after == []
    rejected_after = [json.loads(e.payload_json)["reasons"] for e in events
                      if e.event_type == "ORDER_REJECTED" and e.seq > halt_seq[0]]
    assert len(rejected_after) == 24 - len(fills_before)  # alle uebrigen Orders kamen nach dem Not-Aus
    assert all(r == ["TRADING_HALTED"] for r in rejected_after)
    with admin(pg) as c:
        assert c.execute("SELECT count(*) FROM positions").fetchone()[0] == len(
            [e for e in events if e.event_type == "ORDER_FILLED"])
    assert PostgresEventLog(pg.app_url).verify().ok


def _run_mixed(orders, halts):
    import time

    from test_pg_multiprocess import spawn
    start_at = time.time() + 4.0
    procs = [spawn("order", dict(a, start_at=start_at)) for a in orders]
    procs += [spawn("halt", dict(a, start_at=start_at)) for a in halts]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, "Worker fehlgeschlagen: %s %s" % (out, err[-2000:])
        results += [json.loads(line) for line in out.splitlines() if line.strip()]
    return results


def test_stop_loss_still_works_while_halted(pg):
    ex, clock = pg_exec(pg)
    assert ex.submit(intent("a"), snap(clock)).status is OrderStatus.FILLED
    ex.halt("Test")
    closed = feed(ex, clock, make_candle(T0, D(100), D(100), D(94), D(95)))
    assert [t.exit_reason for t in closed] == [ExitReason.STOP_LOSS]


def test_missing_halt_row_counts_as_halted(pg):
    """Hat ein Superuser die Zeile entfernt (Trigger abgeschaltet), gilt der Handel als gestoppt - Programm UND Datenbank."""
    ex, clock = pg_exec(pg)
    with admin(pg) as c:
        c.execute("ALTER TABLE trading_halt DISABLE TRIGGER ALL")
        c.execute("DELETE FROM trading_halt")
        c.execute("ALTER TABLE trading_halt ENABLE TRIGGER ALL")
    assert ex.halt_status().halted
    assert ex.submit(intent("a"), snap(clock)).reasons == (R.TRADING_HALTED,)
    with app(pg) as c, pytest.raises(E.CheckViolation, match="Not-Aus"):
        c.execute(
            "INSERT INTO positions (account_id, position_id, status, setup_id, strategy, exchange, symbol, market_type,"
            " side, quantity, entry_price, stop_loss, take_profit, leverage, opened_at, margin, risk_at_open, entry_fee,"
            " last_funding_ts) VALUES ('main','P9','OPEN','s','t','synthetic','X-USD','spot','long',1,100,99,102,1,"
            " now(),100,1.1,0.1,now())")


def test_release_with_a_missing_row_fails_loudly_and_logs_nothing(pg):
    with admin(pg) as c:
        c.execute("ALTER TABLE trading_halt DISABLE TRIGGER ALL")
        c.execute("DELETE FROM trading_halt")
        c.execute("ALTER TABLE trading_halt ENABLE TRIGGER ALL")
    with pytest.raises(KillSwitchError, match="fehlt"):
        release(pg)
    assert PostgresEventLog(pg.app_url).of_type(EventType.HALT_RELEASED) == ()


def test_release_rearms_the_brakes_of_every_account(pg):
    """Freigabe nimmt den bisherigen Verlust bewusst hin: Tagesbeginn und Hoechststand aller Konten = aktuelle Equity.
    Sonst loeste dieselbe Bremse sofort wieder aus."""
    ex, clock = pg_exec(pg)
    other, _ = pg_exec(pg, clock, account="second")
    with admin(pg) as c:
        c.execute("UPDATE accounts SET peak_equity = 11200")  # 10,7 % unter dem Hoechststand
    ex.portfolio_state()
    assert ex.halt_status().reason is HaltReason.DRAWDOWN
    release(pg)
    with admin(pg) as c:
        rows = c.execute("SELECT account_id, peak_equity, day_start_equity FROM accounts ORDER BY 1").fetchall()
    assert rows == [("main", D(10000), D(10000)), ("second", D(10000), D(10000))]
    assert ex.submit(intent("a"), snap(clock)).status is OrderStatus.FILLED
    assert not ex.halt_status().halted


def test_every_connection_limits_hanging_transactions(pg):
    ex, _ = pg_exec(pg)
    with ex._transaction() as cur:
        cur.execute("SHOW idle_in_transaction_session_timeout")
        assert cur.fetchone()["idle_in_transaction_session_timeout"] == "15s"


def test_hanging_transaction_cannot_block_the_external_halt_for_long(pg):
    """Ein Prozess haengt MITTEN in einer Transaktion (haelt Konto- und Not-Aus-Zeile). Der Server beendet seine Sitzung
    nach 15 s; der Not-Aus von aussen gelingt danach (mit Wiederholung). Dauert rund 15-25 s."""
    import threading
    import time

    release_hang = threading.Event()

    def hang(stage):
        if stage == "before_commit":
            release_hang.wait(40)

    stuck, clock = pg_exec(pg, fault_hook=hang)
    t = threading.Thread(target=lambda: _swallow(lambda: stuck.submit(intent("a"), snap(clock))))
    t.start()
    time.sleep(0.5)
    switch = PostgresKillSwitch(pg.app_url)
    started = time.monotonic()
    assert switch.trip(HaltReason.MANUAL, "Notfall") is True
    assert time.monotonic() - started < 35
    switch.close()
    release_hang.set()
    t.join(10)
    assert halt_row(pg)[:2] == (True, "MANUAL")
    with admin(pg) as c:
        assert c.execute("SELECT count(*) FROM positions").fetchone()[0] == 0  # die haengende Order kam nie an


def _swallow(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001 - die beendete Sitzung meldet einen Verbindungsfehler; das ist hier erwartet
        pass
