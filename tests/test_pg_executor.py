"""PostgreSQL-Executor: gleiche Ergebnisse wie die Speicher-Engine, atomar, protokolliert."""

import dataclasses
from datetime import timedelta
from decimal import Decimal

import psycopg
import pytest
from conftest import requires_db
from helpers import D, NO_SLIP, T0, Env, flat_candle, make_candle, make_setup, snapshot
from stubs import StubKibo, StubMomo, StubYuzu

from trading.agents import AkiAgent, MarketContext, RinConfig, RuleBasedRin, RuleBasedSora, SizingPolicy
from trading.audit.events import EventType
from trading.clock import FixedClock
from trading.domain.models import MarketType, OrderIntent, OrderStatus, Side
from trading.domain.reasons import RejectReason as R
from trading.domain.universe import TradingUniverse
from trading.persistence.pg_event_log import PostgresEventLog
from trading.persistence.pg_executor import PostgresPaperExecutor
from trading.pipeline import TradingPipeline
from trading.safety.limits import DEFAULT_LIMITS
from trading.safety.risk_manager import RiskManager

pytestmark = requires_db

FUT = {"market_type": MarketType.FUTURES, "funding": D("0.0001")}


def pg_exec(pg, clock=None, fault_hook=None, account="main"):
    clock = clock or FixedClock(T0)
    return PostgresPaperExecutor(pg.app_url, account, D(10000), RiskManager(DEFAULT_LIMITS, NO_SLIP), clock, fault_hook), clock


def intent(oid, symbol="BTC-USD", mt=MarketType.SPOT, side=Side.LONG, qty=D(40), lev=D(1), **kw):
    if side is Side.SHORT:
        kw.setdefault("entry", D(100)); kw.setdefault("stop", D(101)); kw.setdefault("tp", D(98))
    return OrderIntent(oid, make_setup(oid, symbol=symbol, market_type=mt, side=side, **kw), qty, lev)


def snap(clock, symbol="BTC-USD", **kw):
    return snapshot(symbol=symbol, ts=clock.now(), **kw)


def scenario(ex, clock):
    """Ein gemischter Ablauf; liefert alles, was zwischen Speicher- und DB-Executor identisch sein muss."""
    out = []
    out.append(ex.submit(intent("a"), snap(clock)))  # Spot Long
    out.append(ex.submit(intent("a"), snap(clock)))  # doppelte ID
    out.append(ex.submit(intent("b", "ETH-USD", MarketType.FUTURES, lev=D(2)), snap(clock, "ETH-USD", **FUT)))
    out.append(ex.submit(intent("c", "XRP-USD", MarketType.FUTURES, Side.SHORT, lev=D(2)),
                         snapshot(symbol="XRP-USD", ts=T0 - timedelta(seconds=99), **FUT)))  # veraltet
    out.append(ex.submit(intent("d", qty=D(100)), snap(clock)))  # zu gross / Duplikat
    closed = ex.on_candle(make_candle(clock.now(), D(100), D(103), D(100), D(102)))  # BTC: Take-Profit
    closed += ex.on_candle(flat_candle(clock.now(), D(100), symbol="ETH-USD", interval_s=4 * 3600), D("0.0001"))
    clock.advance(4 * 3600)
    closed += ex.on_candle(make_candle(clock.now(), D(100), D(100), D(98), D(98.5), symbol="ETH-USD"), D("0.0001"))
    return out, closed


def summary(ex, out, closed):
    st = ex.portfolio_state()
    return {
        "results": [(r.status, r.reasons, r.position.position_id if r.position else None) for r in out],
        "closed": [(t.position_id, t.exit_reason, t.net_pnl, t.fees, t.funding) for t in closed],
        "balance": ex.balance, "equity": ex.equity(),
        "state": (st.free_margin, st.peak_equity, st.open_risk, st.open_count),
        "all_closed": [(t.position_id, t.net_pnl) for t in ex.closed_trades],
    }


def test_postgres_executor_gives_identical_results_to_the_in_memory_engine(pg):
    env = Env()
    mem_out, mem_closed = scenario(env.executor, env.clock)
    ex, clock = pg_exec(pg)
    db_out, db_closed = scenario(ex, clock)
    assert summary(ex, db_out, db_closed) == summary(env.executor, mem_out, mem_closed)
    # und es gab wirklich etwas zu vergleichen
    assert [r.status for r in db_out][:3] == [OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.FILLED]
    assert len(db_closed) == 2


def test_exact_decimals_survive_the_database_roundtrip(pg):
    ex, clock = pg_exec(pg)
    mem = Env()
    r1 = ex.submit(intent("a", entry=D("100.123456789"), stop=D("99.0123"), tp=D("103")), snap(clock, mid=D("100.123456789")))
    mem.executor.submit(intent("a", entry=D("100.123456789"), stop=D("99.0123"), tp=D("103")), snap(mem.clock, mid=D("100.123456789")))
    assert r1.status is OrderStatus.FILLED
    assert ex.open_positions == mem.executor.open_positions
    assert ex.balance == mem.executor.balance


def test_everything_is_stored_in_the_database(pg):
    ex, clock = pg_exec(pg)
    ex.submit(intent("a"), snap(clock))
    ex.submit(intent("a"), snap(clock))  # Duplikat
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT status, symbol, quantity FROM positions").fetchall() == [("OPEN", "BTC-USD", D(40))]
        assert c.execute("SELECT client_order_id, status FROM orders").fetchall() == [("a", "filled")]
        types = [r[0] for r in c.execute("SELECT event_type FROM event_log ORDER BY seq").fetchall()]
        assert types == ["ORDER_FILLED", "ORDER_REJECTED"]  # auch die Blockade steht im Protokoll
        bal, ver = c.execute("SELECT balance, version FROM accounts").fetchone()
        assert bal == ex.balance == D("9997.6") and ver == 1
    assert PostgresEventLog(pg.app_url).verify().ok


def test_blocked_orders_are_logged_with_reasons_and_setup(pg):
    ex, clock = pg_exec(pg)
    r = ex.submit(intent("stale"), snapshot(ts=T0 - timedelta(seconds=99)))
    assert r.status is OrderStatus.REJECTED and R.STALE_DATA in r.reasons
    ev = PostgresEventLog(pg.app_url).of_type(EventType.ORDER_REJECTED)[0].payload
    assert ev["reasons"] == ["STALE_DATA"] and ev["setup"]["setup_id"] == "stale"
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT status, reasons FROM orders").fetchall() == [("rejected", "STALE_DATA")]


def test_failure_before_commit_rolls_back_everything_and_does_not_consume_the_order_id(pg):
    def boom(stage):
        raise RuntimeError("Absturz vor dem Commit")

    ex_bad, clock = pg_exec(pg, fault_hook=boom)
    with pytest.raises(RuntimeError):
        ex_bad.submit(intent("x"), snap(clock))
    with psycopg.connect(pg.admin_url) as c:
        for table in ("positions", "orders", "event_log"):
            assert c.execute("SELECT count(*) FROM " + table).fetchone() == (0,), table
        assert c.execute("SELECT balance, version FROM accounts").fetchone() == (D(10000), 0)
    ex_ok, _ = pg_exec(pg)
    assert ex_ok.submit(intent("x"), snap(clock)).status is OrderStatus.FILLED  # gleiche ID, jetzt erfolgreich


def test_closing_positions_is_persisted(pg):
    ex, clock = pg_exec(pg)
    ex.submit(intent("a"), snap(clock))
    (t,) = ex.on_candle(make_candle(clock.now(), D(100), D(103), D(100), D(102)))
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT status FROM positions").fetchall() == [("CLOSED",)]
        assert c.execute("SELECT net_pnl, exit_reason FROM closed_trades").fetchall() == [(t.net_pnl, "take_profit")]
    assert ex.open_positions == () and ex.closed_trades == (t,)
    assert ex.balance == D(10000) + t.net_pnl


def test_peak_equity_is_persisted_by_portfolio_state(pg):
    ex, clock = pg_exec(pg)
    ex.submit(intent("a"), snap(clock))
    ex.on_candle(make_candle(clock.now(), D(100), D("101.5"), D("99.5"), D(101)))  # +40 unrealisiert
    state = ex.portfolio_state()
    assert state.peak_equity == state.equity > D(10000)
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT peak_equity FROM accounts").fetchone() == (state.peak_equity,)


def test_two_executor_instances_share_one_truth(pg):
    ex1, clock = pg_exec(pg)
    ex2, _ = pg_exec(pg)
    assert ex1.submit(intent("a"), snap(clock)).status is OrderStatus.FILLED
    assert ex2.submit(intent("a"), snap(clock)).reasons == (R.DUPLICATE_ORDER_ID,)
    assert ex2.submit(intent("b"), snap(clock)).reasons == (R.DUPLICATE_POSITION,)
    assert ex1.open_positions == ex2.open_positions and len(ex2.open_positions) == 1


def test_application_role_is_used_never_the_administrator(pg):
    ex, _ = pg_exec(pg)
    with ex._transaction() as cur:
        cur.execute("SELECT current_user, (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS su")
        row = cur.fetchone()
    assert row == {"current_user": "trading_app", "su": False}


# ------------------------------------------------------------------ Pipeline gegen PostgreSQL


def make_pipeline(pg, setups, clock):
    ex, _ = pg_exec(pg, clock)
    log = PostgresEventLog(pg.app_url, clock)
    rin = RuleBasedRin(TradingUniverse.of(spot=["BTC-USD"], futures=["BTC-USD", "ETH-USD"]), clock, RinConfig(), NO_SLIP)
    sora = RuleBasedSora(SizingPolicy.from_limits(DEFAULT_LIMITS), NO_SLIP)
    return TradingPipeline(StubMomo(setups), rin, StubKibo(), StubYuzu(), sora, AkiAgent(log), ex), ex, log


def test_full_pipeline_with_postgres_executor_and_postgres_aki_log(pg):
    clock = FixedClock(T0)
    pipe, ex, log = make_pipeline(pg, [make_setup("p1", tp=D("102.5")),
                                       make_setup("p2", symbol="ZZZ-USD")], clock)
    out = pipe.run_cycle([MarketContext(snap(clock))])
    assert [o.executed for o in out] == [True, False] and out[1].reasons == (R.NO_MARKET_DATA,)
    types = [e.event_type for e in log.events()]
    assert types[:2] == ["SETUP_PROPOSED", "AGENT_OPINION"] and "SIZING_PROPOSED" in types
    assert types.count("ORDER_FILLED") == 1 and types.count("SETUP_BLOCKED") == 1
    assert log.verify().ok
    assert ex.open_positions[0].risk_at_open <= D(40)


def test_database_error_during_execution_blocks_the_trade_instead_of_crashing(pg):
    clock = FixedClock(T0)
    pipe, ex, log = make_pipeline(pg, [make_setup("p1", tp=D("102.5"))], clock)

    calls = []

    def boom(stage):  # 1. Aufruf = Portfolio-Abfrage durch SORA (ok), 2. Aufruf = die Ausfuehrung (faellt aus)
        calls.append(stage)
        if len(calls) >= 2:
            raise RuntimeError("DB weg")

    ex._fault_hook = boom
    (o,) = pipe.run_cycle([MarketContext(snap(clock))])
    assert not o.executed and o.stage == "EXECUTOR" and o.reasons == (R.EXECUTION_ERROR,)
    ex._fault_hook = None
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT count(*) FROM positions").fetchone() == (0,)
    assert log.of_type(EventType.SYSTEM_ERROR) and log.verify().ok
