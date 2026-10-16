"""Mehrere echte Betriebssystem-Prozesse greifen gleichzeitig auf EIN Konto zu (Teamrisiko-Limit)."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
# Ohne psycopg (z. B. Python 3.9, Kern-Pruefung) sichtbar ueberspringen statt beim Einsammeln abzubrechen.
psycopg = pytest.importorskip("psycopg", reason="psycopg nicht installiert - Datenbank-Tests uebersprungen")
from conftest import requires_db
from helpers import D, T0
from pg_workers import make_executor

from trading.audit.events import EventType
from trading.persistence.pg_event_log import PostgresEventLog

pytestmark = requires_db
REPEATS = 3
ROOT = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")]),
           PYTHONDONTWRITEBYTECODE="1")


def spawn(kind, args):
    return subprocess.Popen([sys.executable, str(ROOT / "tests" / "pg_workers.py"), kind, json.dumps(args)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV)


def run_workers(kind, arg_list, lead_time=4.0, timeout=120):
    """Startet je Eintrag einen eigenen Prozess; alle beginnen zur selben Uhrzeit. Liefert alle Meldungen."""
    start_at = time.time() + lead_time
    procs = [spawn(kind, dict(a, start_at=start_at)) for a in arg_list]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=timeout)
        assert p.returncode == 0, "Worker fehlgeschlagen: %s %s" % (out, err[-2000:])
        results += [json.loads(line) for line in out.splitlines() if line.strip()]
    return results


def init_account(pg):
    pg.create_account("main")
    make_executor(pg.app_url, "main").close()


def db_facts(pg):
    with psycopg.connect(pg.admin_url) as c:
        acc = c.execute("SELECT equity, max_portfolio_risk FROM accounts").fetchone()
        remaining = c.execute(
            "SELECT COALESCE(SUM(risk_at_open - entry_fee), 0), count(*) FROM positions WHERE status='OPEN'"
        ).fetchone()
        orders = c.execute("SELECT count(*) FROM orders").fetchone()[0]
    return {"equity": acc[0], "cap": acc[1], "remaining_risk": remaining[0], "open": remaining[1], "orders": orders}


@pytest.mark.parametrize("run", range(REPEATS))
def test_twelve_processes_never_exceed_the_two_percent_team_limit(pg, run):
    init_account(pg)
    res = run_workers("order", [dict(url=pg.app_url, account="main", specs=[["o%d" % i, "S%d-USD" % i]])
                                for i in range(12)])
    assert len(res) == 12 and len({r["pid"] for r in res}) == 12  # wirklich 12 verschiedene Prozesse
    assert len([r for r in res if r["status"] == "filled"]) == 4
    rejected = [r for r in res if r["status"] == "rejected"]
    assert len(rejected) == 8 and all(r["reasons"] == ["PORTFOLIO_RISK_TOO_HIGH"] for r in rejected)
    f = db_facts(pg)
    assert f["open"] == 4 and f["orders"] == 12
    assert f["remaining_risk"] <= f["cap"] * f["equity"]
    log = PostgresEventLog(pg.app_url)
    assert len(log.of_type(EventType.ORDER_FILLED)) == 4 and len(log.of_type(EventType.ORDER_REJECTED)) == 8
    assert log.verify().ok


@pytest.mark.parametrize("run", range(REPEATS))
def test_same_order_id_from_many_processes_executes_exactly_once(pg, run):
    init_account(pg)
    res = run_workers("order", [dict(url=pg.app_url, account="main", specs=[["same", "BTC-USD"]])
                                for _ in range(8)])
    # Genau ein Prozess handelt; die 7 anderen bekommen dessen Ergebnis (dieselbe Position) als Wiederholung.
    assert sorted(r["replayed"] for r in res) == [False] + [True] * 7
    assert all(r["status"] == "filled" and r["reasons"] == [] and r["position"] == "P000001" for r in res)
    assert db_facts(pg)["open"] == 1 and db_facts(pg)["orders"] == 1
    log = PostgresEventLog(pg.app_url)
    assert len(log.of_type(EventType.ORDER_FILLED)) == 1 and len(log.of_type(EventType.ORDER_REPLAYED)) == 7


@pytest.mark.parametrize("run", range(REPEATS))
def test_same_market_from_many_processes_opens_exactly_one_position(pg, run):
    init_account(pg)
    res = run_workers("order", [dict(url=pg.app_url, account="main", specs=[["id%d" % i, "BTC-USD"]])
                                for i in range(8)])
    assert sorted(r["status"] for r in res) == ["filled"] + ["rejected"] * 7
    assert all(r["reasons"] == ["DUPLICATE_POSITION"] for r in res if r["status"] == "rejected")


def test_many_orders_per_process_in_parallel_keep_all_invariants(pg):
    init_account(pg)
    res = run_workers("order", [dict(url=pg.app_url, account="main",
                                     specs=[["p%d-o%d" % (p, i), "M%d-USD" % (p * 10 + i)] for i in range(5)])
                                for p in range(4)])
    assert len(res) == 20 and len([r for r in res if r["status"] == "filled"]) == 4
    f = db_facts(pg)
    assert f["orders"] == 20 and f["remaining_risk"] <= f["cap"] * f["equity"]
    assert PostgresEventLog(pg.app_url).verify().ok


def test_full_pipelines_in_separate_processes_share_the_risk_budget_and_one_log(pg):
    init_account(pg)
    res = run_workers("pipeline", [dict(url=pg.app_url, account="main", symbol="P%d-USD" % i)
                                   for i in range(8)])
    assert len(res) == 8
    executed = [r for r in res if r["executed"]]
    f = db_facts(pg)
    # Jede Pipeline plant 0,4 % Risiko; fuenf passen unter ALLE Grenzen, dann greift das Positionslimit.
    # (Frueher stand hier nur "zwischen 1 und 5" - das verdeckte, was wirklich geschah.)
    assert len(executed) == 5 and f["open"] == 5
    assert f["remaining_risk"] <= f["cap"] * f["equity"]
    for r in res:
        assert r["executed"] or r["reasons"], "jede Blockade braucht einen Grund: %r" % (r,)
    log = PostgresEventLog(pg.app_url)
    assert len(log.of_type(EventType.SETUP_PROPOSED)) == 8
    assert log.verify().ok  # AKI-Eintraege und Executor-Eintraege liegen in EINER gueltigen Kette


@pytest.mark.parametrize("run", range(REPEATS))
def test_competing_processes_shrink_into_the_remaining_budget_instead_of_being_rejected(pg, run):
    """Audit M8: Ziel 0,45 % je Trade. Vier passen voll, der fuenfte bekommt das Restbudget in KLEINERER Groesse -
    weil SORA in der Transaktion mit gesperrter Konto-Zeile rechnet. (Vorher: 2 von 8 Pipelines fuehrten aus.)"""
    init_account(pg)
    res = run_workers("pipeline", [dict(url=pg.app_url, account="main", symbol="Q%d-USD" % i,
                                        risk_per_trade="0.0045", slow_sora=0.05) for i in range(8)])
    executed = [r for r in res if r["executed"]]
    assert len(executed) == 5, res
    risks = sorted(D(r["risk_pct"]) for r in executed)
    assert all(x > D("0.0044") for x in risks[1:]) and D("0.001") < risks[0] < D("0.0025"), risks
    blocked = [r for r in res if not r["executed"]]
    assert all(r["stage"] == "SORA" and r["reasons"] == ["TOO_MANY_POSITIONS"] for r in blocked), blocked
    f = db_facts(pg)
    assert f["open"] == 5 and f["orders"] == 5  # keine abgelehnte Order mehr beim Risikomanager
    assert f["remaining_risk"] <= f["cap"] * f["equity"]
    log = PostgresEventLog(pg.app_url)
    assert len(log.of_type(EventType.SIZING_PROPOSED)) == 8 and not log.of_type(EventType.ORDER_REJECTED)
    assert log.verify().ok


# ------------------------------------------------------------------ Absturz


def test_process_killed_before_commit_leaves_no_trace_and_releases_the_lock(pg, tmp_path):
    init_account(pg)
    ready = tmp_path / "ready"
    p = spawn("crash", dict(url=pg.app_url, account="main", hang=True, ready_file=str(ready)))
    deadline = time.time() + 60
    while not ready.exists() and time.time() < deadline and p.poll() is None:
        time.sleep(0.1)
    assert ready.exists(), "Worker hat den Punkt vor dem Commit nicht erreicht"
    os.kill(p.pid, signal.SIGKILL)  # harter Absturz mitten in der Transaktion
    p.wait(timeout=30)
    assert p.returncode == -signal.SIGKILL
    f = db_facts(pg)
    assert f["open"] == 0 and f["orders"] == 0
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT count(*) FROM event_log").fetchone() == (0,)
        assert c.execute("SELECT balance, version FROM accounts").fetchone() == (D(10000), 0)
    # Die Sperre ist frei und die Order-ID nicht verbraucht: ein neuer Prozess kann sofort handeln.
    from helpers import make_setup, snapshot
    from trading.domain.models import MarketType, OrderIntent
    ex = make_executor(pg.app_url, "main")
    setup = make_setup("crash-1", symbol="CRSH-USD", market_type=MarketType.FUTURES, stop=D(95), tp=D(110))
    t0 = time.time()
    res = ex.submit(OrderIntent("crash-1", setup, D(9), D(2)),
                    snapshot(symbol="CRSH-USD", ts=T0, market_type=MarketType.FUTURES, funding=D("0.0001")))
    assert res.status.value == "filled" and time.time() - t0 < 10


def test_committed_trade_survives_the_process_that_made_it(pg, tmp_path):
    init_account(pg)
    p = spawn("crash", dict(url=pg.app_url, account="main", hang=False, ready_file=str(tmp_path / "x")))
    out, err = p.communicate(timeout=60)
    assert p.returncode == 0, err
    f = db_facts(pg)
    assert f["open"] == 1 and f["orders"] == 1
    assert PostgresEventLog(pg.app_url).verify().ok


# ------------------------------------------------------------------ Stufe 1: neue Operationen aus mehreren Prozessen


def open_base_position(pg):
    from helpers import FUT, make_setup, snapshot
    from trading.domain.models import MarketType, OrderIntent

    pg.create_account("main")
    ex = make_executor(pg.app_url, "main")
    setup = make_setup("base", market_type=MarketType.FUTURES, stop=D(95), tp=D(110))
    assert ex.submit(OrderIntent("base", setup, D(9), D(2)), snapshot(ts=T0, **FUT)).status.value == "filled"
    ex.close()


def run_operation(pg, op, n=8):
    return [r["result"] for r in run_workers("operation", [dict(url=pg.app_url, account="main", op=op)
                                                           for _ in range(n)])]


@pytest.mark.parametrize("run", range(REPEATS))
def test_same_funding_event_from_many_processes_is_charged_once(pg, run):
    open_base_position(pg)
    results = run_operation(pg, "funding")
    assert sorted(D(r) for r in results) == [D(0)] * 7 + [D("0.09")]  # 9 x 100 x 0,01 %, genau einmal
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT funding_paid FROM positions").fetchone() == (D("0.09"),)
        assert c.execute("SELECT balance FROM accounts").fetchone() == (D(10000) - D("0.54") - D("0.09"),)
    assert len(PostgresEventLog(pg.app_url).of_type(EventType.FUNDING_APPLIED)) == 1


@pytest.mark.parametrize("run", range(REPEATS))
def test_same_candle_from_many_processes_stops_out_once(pg, run):
    open_base_position(pg)
    results = run_operation(pg, "candle")
    assert sorted(results) == [0] * 7 + [1]
    f = db_facts(pg)
    assert f["open"] == 0
    log = PostgresEventLog(pg.app_url)
    assert len(log.of_type(EventType.POSITION_CLOSED)) == 1
    assert [tuple(e.payload["reasons"]) for e in log.of_type(EventType.DATA_REJECTED)] == [("DUPLICATE_CANDLE",)] * 7
    assert log.verify().ok


@pytest.mark.parametrize("run", range(REPEATS))
def test_close_requests_from_many_processes_close_once(pg, run):
    open_base_position(pg)
    results = run_operation(pg, "close")
    assert sorted(r[0] for r in results) == [False] * 7 + [True]
    assert all(r[1] == ["NO_POSITION"] for r in results if not r[0])
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT count(*) FROM closed_trades").fetchone() == (1,)
        net = c.execute("SELECT net_pnl FROM closed_trades").fetchone()[0]
        assert c.execute("SELECT balance FROM accounts").fetchone() == (D(10000) + net,)
