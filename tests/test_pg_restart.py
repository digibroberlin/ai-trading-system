"""Neustart: Offene Paper-Positionen und alle Zaehler muessen aus der Datenbank wiederkehren."""

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import psycopg
from conftest import requires_db
from helpers import D, NO_SLIP, T0, Env, flat_candle, make_candle, make_setup, snapshot

from trading.audit.events import EventType
from trading.clock import FixedClock
from trading.domain.models import MarketType, OrderIntent, OrderStatus, Side
from trading.domain.reasons import RejectReason as R
from trading.persistence.pg_event_log import PostgresEventLog
from trading.persistence.pg_executor import PostgresPaperExecutor
from trading.safety.limits import DEFAULT_LIMITS
from trading.safety.risk_manager import RiskManager

pytestmark = requires_db
FUT = {"market_type": MarketType.FUTURES, "funding": D("0.0001")}
ROOT = Path(__file__).resolve().parent.parent


def new_executor(pg, clock):
    """Jeder Aufruf = frischer Executor mit NEUER Verbindung und NEUEM Objektzustand (wie nach einem Neustart)."""
    return PostgresPaperExecutor(pg.app_url, "main", D(10000), RiskManager(DEFAULT_LIMITS, NO_SLIP), clock)


def order(oid, symbol, mt, side=Side.LONG, **kw):
    if side is Side.SHORT:
        kw.setdefault("entry", D(100)); kw.setdefault("stop", D(101)); kw.setdefault("tp", D(98))
    return OrderIntent(oid, make_setup(oid, symbol=symbol, market_type=mt, side=side, **kw), D(40),
                       D(1) if mt is MarketType.SPOT else D(2))


def phase1(ex, clock):
    """Zwei offene Positionen (Spot-Long und Futures-Short), danach Kursbewegung ohne Ausloesung."""
    r1 = ex.submit(order("a", "BTC-USD", MarketType.SPOT), snapshot(symbol="BTC-USD", ts=clock.now()))
    r2 = ex.submit(order("b", "ETH-USD", MarketType.FUTURES, Side.SHORT),
                   snapshot(symbol="ETH-USD", ts=clock.now(), **FUT))
    assert r1.status is r2.status is OrderStatus.FILLED
    ex.on_candle(make_candle(clock.now(), D(100), D("100.8"), D("99.5"), D("100.5")))
    ex.on_candle(make_candle(clock.now(), D(100), D("100.4"), D("99.2"), D("99.4"), symbol="ETH-USD"), D("0.0001"))


def phase2(ex, clock):
    """Nach dem Neustart: BTC erreicht das Ziel, ETH wird ausgestoppt - inkl. Funding-Zeitpunkt."""
    out = []
    out += ex.on_candle(make_candle(clock.now(), D(100), D(103), D(100), D(102)))
    out += ex.on_candle(flat_candle(clock.now(), D(100), symbol="ETH-USD", interval_s=4 * 3600), D("0.0001"))
    clock.advance(4 * 3600)
    out += ex.on_candle(make_candle(clock.now(), D(100), D(101.5), D(100), D(101), symbol="ETH-USD"), D("0.0001"))
    return out


def test_open_positions_and_account_survive_a_restart_exactly(pg):
    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    phase1(a, clock)
    before = (a.open_positions, a.balance, a.portfolio_state())
    a.close()  # "Neustart": Verbindung weg, Objekt weg

    b = new_executor(pg, FixedClock(T0))
    assert b.open_positions == before[0] and len(before[0]) == 2
    assert b.balance == before[1]
    assert b.portfolio_state() == before[2]  # Eigenkapital, Margin, offenes Risiko, Hoechststand, Positionsschluessel


def test_run_with_a_restart_in_the_middle_equals_an_uninterrupted_run(pg):
    # Kontrolllauf im Speicher ohne Unterbrechung
    env = Env()
    phase1(env.executor, env.clock)
    control_closed = phase2(env.executor, env.clock)

    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    phase1(a, clock)
    a.close()
    b = new_executor(pg, clock)  # Neustart zwischen Phase 1 und 2
    closed = phase2(b, clock)

    assert [(t.position_id, t.exit_reason, t.exit_price, t.net_pnl, t.fees, t.funding) for t in closed] == \
           [(t.position_id, t.exit_reason, t.exit_price, t.net_pnl, t.fees, t.funding) for t in control_closed]
    assert b.balance == env.executor.balance and b.open_positions == ()
    assert len(closed) == 2


def test_order_ids_stay_blocked_after_a_restart(pg):
    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    a.submit(order("a", "BTC-USD", MarketType.SPOT), snapshot(ts=clock.now()))
    a.close()
    b = new_executor(pg, clock)
    again = b.submit(order("a", "BTC-USD", MarketType.SPOT), snapshot(ts=clock.now()))
    assert again.reasons == (R.DUPLICATE_ORDER_ID,)
    assert len(b.open_positions) == 1


def test_price_jump_filter_keeps_its_memory_across_a_restart(pg):
    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    assert a.submit(order("a", "BTC-USD", MarketType.SPOT), snapshot(ts=clock.now(), mid=D(100))).status is OrderStatus.FILLED
    a.close()
    b = new_executor(pg, clock)
    r = b.submit(order("z", "BTC-USD", MarketType.SPOT, entry=D(130), stop=D(128), tp=D(135)),
                 snapshot(ts=clock.now(), mid=D(130)))  # +30 % gegenueber dem letzten gueltigen Tick
    assert R.PRICE_JUMP in r.reasons


def test_peak_equity_and_marks_survive(pg):
    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    a.submit(order("a", "BTC-USD", MarketType.SPOT), snapshot(ts=clock.now()))
    a.on_candle(make_candle(clock.now(), D(100), D(101.5), D(99.5), D(101)))  # +40 unrealisiert
    peak = a.portfolio_state().peak_equity
    assert peak > D(10000)
    a.on_candle(make_candle(clock.now(), D(101), D(101), D(99.5), D(99.5)))  # Kurs faellt wieder
    a.close()
    b = new_executor(pg, clock)
    state = b.portfolio_state()
    assert state.peak_equity == peak and state.equity < peak  # Hoechststand bleibt erhalten


def test_a_fresh_os_process_sees_the_same_state(pg):
    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    phase1(a, clock)
    expected = [[p.position_id, p.symbol, str(p.quantity), str(p.entry_price), str(p.risk_at_open),
                 str(p.entry_fee)] for p in a.open_positions]
    a.close()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")]),
               PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, str(ROOT / "tests" / "pg_workers.py"), "state",
                          json.dumps(dict(url=pg.app_url, account="main"))],
                         capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert sorted(got["positions"]) == sorted(expected)
    assert D(got["balance"]) == new_executor(pg, clock).balance


def test_event_chain_stays_valid_and_continues_across_restarts(pg):
    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    a.submit(order("a", "BTC-USD", MarketType.SPOT), snapshot(ts=clock.now()))
    last = PostgresEventLog(pg.app_url).events()[-1]
    a.close()
    b = new_executor(pg, clock)
    b.submit(order("b", "ETH-USD", MarketType.FUTURES), snapshot(symbol="ETH-USD", ts=clock.now(), **FUT))
    log = PostgresEventLog(pg.app_url, clock)
    events = log.events()
    assert events[len(events) - 1].seq == last.seq + 1 and events[-1].prev_hash == last.hash
    assert log.verify().ok and len(log.of_type(EventType.ORDER_FILLED)) == 2


def test_account_is_not_reset_when_the_system_starts_again_with_another_initial_equity(pg):
    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    a.submit(order("a", "BTC-USD", MarketType.SPOT), snapshot(ts=clock.now()))
    balance = a.balance
    a.close()
    # Neustart mit "falschem" Startkapital: darf das bestehende Konto NICHT ueberschreiben.
    b = PostgresPaperExecutor(pg.app_url, "main", D(999999), RiskManager(DEFAULT_LIMITS, NO_SLIP), clock)
    assert b.balance == balance and len(b.open_positions) == 1
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT initial_equity FROM accounts").fetchone() == (D(10000),)
