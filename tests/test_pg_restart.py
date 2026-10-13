"""Neustart: Offene Paper-Positionen, alle Zaehler und der Stand der Marktdaten-Verarbeitung
muessen aus der Datenbank wiederkehren."""

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import psycopg
from conftest import requires_db
from helpers import D, FUT, NO_SLIP, T0, Env, inst, make_candle, make_setup, snapshot

from trading.audit.events import EventType
from trading.clock import FixedClock
from trading.domain.models import ExitReason, FundingEvent, MarketType, OrderIntent, OrderStatus, Side
from trading.domain.reasons import RejectReason as R
from trading.persistence.pg_event_log import PostgresEventLog
from trading.persistence.pg_executor import PostgresPaperExecutor
from trading.safety.limits import DEFAULT_LIMITS
from trading.safety.risk_manager import RiskManager

pytestmark = requires_db
FUTM = {"market_type": MarketType.FUTURES}
ROOT = Path(__file__).resolve().parent.parent
BTC, ETH = inst("BTC-USD", MarketType.SPOT), inst("ETH-USD", MarketType.FUTURES)


def new_executor(pg, clock):
    """Jeder Aufruf = frischer Executor mit NEUER Verbindung und NEUEM Objektzustand (wie nach einem Neustart)."""
    return PostgresPaperExecutor(pg.app_url, "main", D(10000), RiskManager(DEFAULT_LIMITS, NO_SLIP), clock)


def order(oid, symbol, mt, side=Side.LONG, **kw):
    """Kleine Position (9 Stueck zu 100, Stop 5 % entfernt)."""
    kw.setdefault("stop", D(95) if side is Side.LONG else D(105))
    kw.setdefault("tp", D(110) if side is Side.LONG else D(90))
    return OrderIntent(oid, make_setup(oid, symbol=symbol, market_type=mt, side=side, **kw), D(9),
                       D(1) if mt is MarketType.SPOT else D(2))


def feed(ex, clock, candle):
    if candle.close_time > clock.now():
        clock.set(candle.close_time)
    return ex.on_candle(candle)


def phase1(ex, clock):
    """Zwei offene Positionen (Spot-Long BTC, Futures-Short ETH), Kursbewegung, eine Funding-Abrechnung."""
    r1 = ex.submit(order("a", "BTC-USD", MarketType.SPOT), snapshot(symbol="BTC-USD", ts=clock.now()))
    r2 = ex.submit(order("b", "ETH-USD", MarketType.FUTURES, Side.SHORT),
                   snapshot(symbol="ETH-USD", ts=clock.now(), **FUT))
    assert r1.status is r2.status is OrderStatus.FILLED
    feed(ex, clock, make_candle(T0, D(100), D(102), D("99.5"), D(101)))
    feed(ex, clock, make_candle(T0, D(100), D("100.4"), D("99.2"), D("99.4"), symbol="ETH-USD", **FUTM))
    clock.set(T0 + timedelta(hours=4))
    assert ex.apply_funding(FundingEvent(ETH, clock.now(), D("0.0001"), D("99.4"))) == D("-0.08946")


def phase2(ex, clock):
    """Nach dem Neustart: wiederholt gelieferte Daten bleiben wirkungslos; BTC erreicht das Ziel,
    ETH wird ausgestoppt."""
    assert feed(ex, clock, make_candle(T0, D(100), D(100), D(90), D(91))) == []  # alte Kerze noch einmal
    assert ex.apply_funding(FundingEvent(ETH, T0 + timedelta(hours=4), D("0.0001"), D("99.4"))) == 0  # dito
    out = feed(ex, clock, make_candle(clock.now(), D(101), D(111), D(101), D(110)))
    out += feed(ex, clock, make_candle(clock.now(), D("99.4"), D(106), D("99.4"), D("105.5"), symbol="ETH-USD", **FUTM))
    return out


def test_open_positions_and_account_survive_a_restart_exactly(pg):
    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    phase1(a, clock)
    before = (a.open_positions, a.balance, a.portfolio_state())
    a.close()  # "Neustart": Verbindung weg, Objekt weg

    b = new_executor(pg, FixedClock(clock.now()))
    assert b.open_positions == before[0] and {p.instrument for p in before[0]} == {BTC, ETH}
    assert b.balance == before[1]
    # Eigenkapital, Buchgewinn, Margin, Risiko, Exposure, Stress, Hoechststand - alles identisch:
    assert b.portfolio_state() == before[2]
    assert before[2].unrealized_pnl == D(9) + D("5.4") and before[2].valuation_complete is False
    assert [p.funding_paid for p in b.open_positions if p.instrument == ETH] == [D("-0.08946")]


def test_run_with_a_restart_in_the_middle_equals_an_uninterrupted_run(pg):
    env = Env()  # Kontrolllauf im Speicher ohne Unterbrechung
    phase1(env.executor, env.clock)
    control = phase2(env.executor, env.clock)

    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    phase1(a, clock)
    a.close()
    b = new_executor(pg, clock)  # Neustart zwischen Phase 1 und 2
    closed = phase2(b, clock)

    def facts(trades):
        return [(t.position_id, t.instrument, t.exit_reason, t.exit_price, t.net_pnl, t.fees, t.funding) for t in trades]

    assert facts(closed) == facts(control) and len(closed) == 2
    assert [t.exit_reason for t in closed] == [ExitReason.TAKE_PROFIT, ExitReason.STOP_LOSS]
    assert b.balance == env.executor.balance and b.open_positions == ()
    assert b.portfolio_state() == env.executor.portfolio_state()


def test_replayed_candles_and_funding_after_a_restart_have_no_effect(pg):
    """Audit H3: Nach einem Neustart liefert ein Datenstrom oft die letzten Daten noch einmal."""
    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    phase1(a, clock)
    state = (a.balance, a.open_positions)
    a.close()
    b = new_executor(pg, clock)
    assert b.on_candle(make_candle(T0, D(100), D(100), D(90), D(91))) == []  # haette den Stop gerissen
    assert b.on_candle(make_candle(T0, D(100), D(120), D(100), D(119), symbol="ETH-USD", **FUTM)) == []
    assert b.apply_funding(FundingEvent(ETH, T0 + timedelta(hours=4), D("0.0001"), D("99.4"))) == 0
    assert b.apply_funding(FundingEvent(ETH, T0 + timedelta(hours=1), D("0.01"), D("99.4"))) == 0
    assert (b.balance, b.open_positions) == state
    rejected = [tuple(e.payload["reasons"]) for e in PostgresEventLog(pg.app_url).of_type(EventType.DATA_REJECTED)]
    assert rejected == [("DUPLICATE_CANDLE",), ("DUPLICATE_CANDLE",)]
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT market_type, last_candle_close FROM instrument_state ORDER BY market_type"
                         ).fetchall() == [("futures", T0 + timedelta(minutes=1)), ("spot", T0 + timedelta(minutes=1))]


def test_order_ids_stay_blocked_after_a_restart(pg):
    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    a.submit(order("a", "BTC-USD", MarketType.SPOT), snapshot(ts=clock.now()))
    a.close()
    b = new_executor(pg, clock)
    again = b.submit(order("a", "BTC-USD", MarketType.SPOT), snapshot(ts=clock.now()))
    assert again.reasons == (R.DUPLICATE_ORDER_ID,) and len(b.open_positions) == 1


def test_price_jump_filter_keeps_its_memory_across_a_restart(pg):
    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    assert a.submit(order("a", "BTC-USD", MarketType.SPOT), snapshot(ts=clock.now(), mid=D(100))).status is OrderStatus.FILLED
    a.close()
    b = new_executor(pg, clock)
    r = b.submit(order("z", "BTC-USD", MarketType.SPOT, entry=D(130), stop=D(124), tp=D(145)),
                 snapshot(ts=clock.now(), mid=D(130)))  # +30 % gegenueber dem letzten gueltigen Kurs
    assert R.PRICE_JUMP in r.reasons
    # Derselbe Sprung im FUTURES-Markt des Paars ist kein Sprung: anderes Instrument, eigene Referenz.
    f = b.submit(order("y", "BTC-USD", MarketType.FUTURES, entry=D(130), stop=D(124), tp=D(145)),
                 snapshot(ts=clock.now(), mid=D(130), **FUT))
    assert R.PRICE_JUMP not in f.reasons


def test_peak_equity_and_marks_survive(pg):
    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    a.submit(order("a", "BTC-USD", MarketType.SPOT), snapshot(ts=clock.now()))
    feed(a, clock, make_candle(T0, D(100), D(104), D(100), D(104)))  # +36 unrealisiert
    peak = a.portfolio_state().peak_equity
    assert peak == D("10035.46")
    feed(a, clock, make_candle(clock.now(), D(104), D(104), D(98), D(98)))  # Kurs faellt wieder
    a.close()
    b = new_executor(pg, clock)
    state = b.portfolio_state()
    assert state.peak_equity == peak and state.equity == D("9981.46")  # Hoechststand bleibt erhalten


def test_a_fresh_os_process_sees_the_same_state(pg):
    clock = FixedClock(T0)
    a = new_executor(pg, clock)
    phase1(a, clock)
    expected = [[p.position_id, p.symbol, str(p.quantity), str(p.entry_price), str(p.risk_at_open),
                 str(p.entry_fee)] for p in a.open_positions]
    balance = a.balance
    a.close()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")]),
               PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, str(ROOT / "tests" / "pg_workers.py"), "state",
                          json.dumps(dict(url=pg.app_url, account="main"))],
                         capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert sorted(got["positions"]) == sorted(expected) and len(expected) == 2
    assert D(got["balance"]) == balance


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
    assert events[-1].seq == last.seq + 1 and events[-1].prev_hash == last.hash
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
