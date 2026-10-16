"""PostgreSQL-Executor: gleiche Ergebnisse wie die Speicher-Engine, atomar, protokolliert."""

import random
from datetime import timedelta
from decimal import Decimal

import pytest
# Ohne psycopg (z. B. Python 3.9, Kern-Pruefung) sichtbar ueberspringen statt beim Einsammeln abzubrechen.
psycopg = pytest.importorskip("psycopg", reason="psycopg nicht installiert - Datenbank-Tests uebersprungen")
from conftest import requires_db
from helpers import D, FUT, NO_SLIP, T0, Env, inst, make_candle, make_setup, snapshot
from stubs import StubKibo, StubMomo, StubYuzu

from trading.agents import AkiAgent, MarketContext, RinConfig, RuleBasedRin, RuleBasedSora, SizingPolicy
from trading.audit.events import EventType
from trading.clock import FixedClock
from trading.domain.models import ExitReason, FundingEvent, MarketType, OrderIntent, OrderStatus, Side
from trading.domain.reasons import RejectReason as R
from trading.domain.universe import TradingUniverse
from trading.execution.costs import CostModel
from trading.persistence.pg_event_log import PostgresEventLog
from trading.persistence.pg_executor import PostgresPaperExecutor
from trading.pipeline import TradingPipeline
from trading.safety.limits import DEFAULT_LIMITS
from trading.safety.risk_manager import RiskManager

pytestmark = requires_db
FUTM = {"market_type": MarketType.FUTURES}


def pg_exec(pg, clock=None, fault_hook=None, account="main", costs=NO_SLIP, equity=D(10000)):
    clock = clock or FixedClock(T0)
    ex = PostgresPaperExecutor(pg.app_url, account, equity, RiskManager(DEFAULT_LIMITS, costs), clock, fault_hook)
    return ex, clock


def intent(oid, symbol="BTC-USD", mt=MarketType.SPOT, side=Side.LONG, qty=D(9), lev=D(1), **kw):
    """Kleine Position mit weitem Stop (9 Stueck zu 100, Stop 5 % entfernt) - mehrere passen unter alle Grenzen."""
    kw.setdefault("stop", D(95) if side is Side.LONG else D(105))
    kw.setdefault("tp", D(110) if side is Side.LONG else D(90))
    return OrderIntent(oid, make_setup(oid, symbol=symbol, market_type=mt, side=side, **kw), qty, lev)


def snap(clock, symbol="BTC-USD", **kw):
    return snapshot(symbol=symbol, ts=clock.now(), **kw)


def feed(ex, clock, candle):
    if candle.close_time > clock.now():
        clock.set(candle.close_time)
    return ex.on_candle(candle)


def scenario(ex, clock):
    """Gemischter Ablauf mit allen Operationen; liefert alles, was zwischen Speicher- und DB-Executor
    identisch sein muss."""
    out = [
        ex.submit(intent("a"), snap(clock)),  # Spot Long
        ex.submit(intent("a"), snap(clock)),  # doppelte ID
        ex.submit(intent("b", "ETH-USD", MarketType.FUTURES, lev=D(2)), snap(clock, "ETH-USD", **FUT)),
        ex.submit(intent("c", "XRP-USD", MarketType.FUTURES, Side.SHORT, lev=D(2)),
                  snapshot(symbol="XRP-USD", ts=T0 - timedelta(seconds=99), **FUT)),  # veraltet
        ex.submit(intent("d", qty=D(100)), snap(clock)),  # zu gross + schon eine Position
        ex.submit(intent("e", "SOL-USD", MarketType.FUTURES, Side.SHORT, lev=D(2)), snap(clock, "SOL-USD", **FUT)),
    ]
    closed = feed(ex, clock, make_candle(T0, D(100), D(111), D(100), D(110)))  # BTC Spot: Take-Profit
    closed += feed(ex, clock, make_candle(T0, D(100), D(100), D(100), D(100), symbol="ETH-USD", **FUTM))
    replay = feed(ex, clock, make_candle(T0, D(100), D(100), D(90), D(91), symbol="ETH-USD", **FUTM))  # Wiederholung
    clock.set(T0 + timedelta(hours=4))
    eth, sol = inst("ETH-USD", MarketType.FUTURES), inst("SOL-USD", MarketType.FUTURES)
    funding = [ex.apply_funding(FundingEvent(eth, clock.now(), D("0.0001"), D(100))),
               ex.apply_funding(FundingEvent(eth, clock.now(), D("0.0001"), D(100))),  # Wiederholung
               ex.apply_funding(FundingEvent(sol, clock.now(), D("0.0001"), D(100)))]
    closed += feed(ex, clock, make_candle(clock.now(), D(100), D(100), D(94), D("94.5"), symbol="ETH-USD", **FUTM))
    close = ex.close_position(sol, snap(clock, "SOL-USD", mid=D(98), half_spread=D("0.02"), **FUT),
                              ExitReason.SIGNAL, "x1")
    nothing = ex.close_position(sol, snap(clock, "SOL-USD", mid=D(98), **FUT), ExitReason.SIGNAL, "x2")
    return out, closed, replay, funding, close, nothing


def summary(ex, run):
    out, closed, replay, funding, close, nothing = run
    st = ex.portfolio_state()
    return {
        "results": [(r.status, r.reasons, r.position.position_id if r.position else None) for r in out],
        "closed": [(t.position_id, t.instrument, t.exit_reason, t.exit_price, t.net_pnl, t.fees, t.funding)
                   for t in closed],
        "replay": replay, "funding": funding,
        "close": (close.closed, close.reasons, close.trade.net_pnl, close.trade.exit_price),
        "nothing": (nothing.closed, nothing.reasons),
        "balance": ex.balance, "equity": ex.equity(),
        "state": (st.free_margin, st.peak_equity, st.open_risk, st.gross_exposure, st.stress_loss,
                  st.open_count, st.valuation_complete, st.day_start_equity),
        "all_closed": [(t.position_id, t.net_pnl, t.exit_reason) for t in ex.closed_trades],
    }


def test_postgres_executor_gives_identical_results_to_the_in_memory_engine(pg):
    env = Env()
    expected = summary(env.executor, scenario(env.executor, env.clock))
    ex, clock = pg_exec(pg)
    got = summary(ex, scenario(ex, clock))
    assert got == expected
    # ... und es gab wirklich etwas zu vergleichen:
    assert [r[0] for r in got["results"]] == [OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.FILLED,
                                             OrderStatus.REJECTED, OrderStatus.REJECTED, OrderStatus.FILLED]
    assert [c[2] for c in got["closed"]] == [ExitReason.TAKE_PROFIT, ExitReason.STOP_LOSS]
    assert got["replay"] == [] and got["funding"] == [D("0.09"), D(0), D("-0.09")]
    assert got["close"][0] and got["nothing"] == (False, (R.NO_POSITION,))
    assert PostgresEventLog(pg.app_url).verify().ok


def test_exact_decimals_survive_the_database_roundtrip(pg):
    ex, clock = pg_exec(pg, costs=CostModel())
    mem = Env(costs=CostModel())
    args = dict(entry=D("100.123456789"), stop=D("95.0123"), tp=D("113.7"))
    for target, c in ((ex, clock), (mem.executor, mem.clock)):
        r = target.submit(intent("a", **args), snap(c, mid=D("100.123456789"), half_spread=D("0.0137")))
        assert r.status is OrderStatus.FILLED
    assert ex.open_positions == mem.executor.open_positions
    assert ex.balance == mem.executor.balance and ex.portfolio_state() == mem.executor.portfolio_state()


def test_everything_is_stored_in_the_database(pg):
    ex, clock = pg_exec(pg)
    ex.submit(intent("a"), snap(clock))
    ex.submit(intent("a"), snap(clock))  # Duplikat
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT status, exchange, market_type, symbol, quantity FROM positions").fetchall() == \
            [("OPEN", "synthetic", "spot", "BTC-USD", D(9))]
        assert c.execute("SELECT client_order_id, status FROM orders").fetchall() == [("a", "filled")]
        types = [r[0] for r in c.execute("SELECT event_type FROM event_log ORDER BY seq").fetchall()]
        assert types == ["ORDER_FILLED", "ORDER_REJECTED"]  # auch die Blockade steht im Protokoll
        bal, eq, ver = c.execute("SELECT balance, equity, version FROM accounts").fetchone()
        assert bal == eq == ex.balance == D("9999.46") and ver == 1
        assert c.execute("SELECT exchange, market_type, symbol, mark, mark_ts, ref_price FROM instrument_state"
                         ).fetchall() == [("synthetic", "spot", "BTC-USD", D(100), T0, D(100))]
    assert PostgresEventLog(pg.app_url).verify().ok


def test_blocked_orders_are_logged_with_reasons_and_setup(pg):
    ex, clock = pg_exec(pg)
    r = ex.submit(intent("stale"), snapshot(ts=T0 - timedelta(seconds=99)))
    assert r.status is OrderStatus.REJECTED and R.STALE_DATA in r.reasons
    ev = PostgresEventLog(pg.app_url).of_type(EventType.ORDER_REJECTED)[0].payload
    assert ev["reasons"] == ["STALE_DATA"] and ev["setup"]["setup_id"] == "stale"
    assert ev["setup"]["instrument"]["exchange"] == "synthetic"
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT status, reasons FROM orders").fetchall() == [("rejected", "STALE_DATA")]


def test_failure_before_commit_rolls_back_everything_and_does_not_consume_the_order_id(pg):
    def boom(stage):
        raise RuntimeError("Absturz vor dem Commit")

    ex_bad, clock = pg_exec(pg, fault_hook=boom)
    with pytest.raises(RuntimeError):
        ex_bad.submit(intent("x"), snap(clock))
    with psycopg.connect(pg.admin_url) as c:
        for table in ("positions", "orders", "event_log", "instrument_state"):
            assert c.execute("SELECT count(*) FROM " + table).fetchone() == (0,), table
        assert c.execute("SELECT balance, version FROM accounts").fetchone() == (D(10000), 0)
    ex_ok, _ = pg_exec(pg)
    assert ex_ok.submit(intent("x"), snap(clock)).status is OrderStatus.FILLED  # gleiche ID, jetzt erfolgreich


def test_failure_before_commit_rolls_back_closes_candles_and_funding(pg):
    ex, clock = pg_exec(pg)
    ex.submit(intent("f", mt=MarketType.FUTURES, lev=D(2)), snap(clock, **FUT))
    clock.set(T0 + timedelta(hours=4))

    def boom(stage):
        raise RuntimeError("Absturz vor dem Commit")

    bad, _ = pg_exec(pg, clock, fault_hook=boom)
    btc = inst("BTC-USD", MarketType.FUTURES)
    with pytest.raises(RuntimeError):
        bad.on_candle(make_candle(clock.now() - timedelta(minutes=1), D(100), D(100), D(94), D(95), **FUTM))
    with pytest.raises(RuntimeError):
        bad.apply_funding(FundingEvent(btc, clock.now(), D("0.0001"), D(100)))
    with pytest.raises(RuntimeError):
        bad.close_position(btc, snap(clock, **FUT), ExitReason.SIGNAL, "x")
    (pos,) = ex.open_positions
    assert pos.funding_paid == 0 and ex.closed_trades == () and ex.balance == D("9999.46")
    assert len(PostgresEventLog(pg.app_url).events()) == 1  # nur der urspruengliche Fill


def test_closing_positions_is_persisted(pg):
    ex, clock = pg_exec(pg)
    ex.submit(intent("a"), snap(clock))
    (t,) = feed(ex, clock, make_candle(T0, D(100), D(111), D(100), D(110)))
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT status, closed_at FROM positions").fetchall() == [("CLOSED", t.closed_at)]
        assert c.execute("SELECT exchange, market_type, net_pnl, exit_reason FROM closed_trades").fetchall() == \
            [("synthetic", "spot", t.net_pnl, "take_profit")]
        assert c.execute("SELECT last_candle_close, mark FROM instrument_state").fetchall() == \
            [(T0 + timedelta(minutes=1), D(110))]
    assert ex.open_positions == () and ex.closed_trades == (t,)
    assert ex.balance == D(10000) + t.net_pnl


def test_signal_close_and_funding_are_persisted(pg):
    ex, clock = pg_exec(pg)
    btc = inst("BTC-USD", MarketType.FUTURES)
    ex.submit(intent("f", mt=MarketType.FUTURES, lev=D(2)), snap(clock, **FUT))
    clock.set(T0 + timedelta(hours=4))
    assert ex.apply_funding(FundingEvent(btc, clock.now(), D("0.0001"), D(100))) == D("0.09")
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT funding_paid, last_funding_ts FROM positions").fetchall() == [(D("0.09"), clock.now())]
    res = ex.close_position(btc, snap(clock, mid=D(102), **FUT), ExitReason.SIGNAL, "sig-1")
    assert res.closed and res.trade.funding == D("0.09")
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT exit_reason, funding, net_pnl FROM closed_trades").fetchall() == \
            [("signal", D("0.09"), res.trade.net_pnl)]
    log = PostgresEventLog(pg.app_url)
    assert [e.event_type for e in log.events()] == ["ORDER_FILLED", "FUNDING_APPLIED", "POSITION_CLOSED"]
    assert log.events()[-1].payload["request_id"] == "sig-1"


def test_spot_and_futures_of_the_same_pair_are_separate_in_the_database(pg):
    ex, clock = pg_exec(pg)
    assert ex.submit(intent("s"), snap(clock)).status is OrderStatus.FILLED
    assert ex.submit(intent("f", mt=MarketType.FUTURES, lev=D(2)), snap(clock, **FUT)).status is OrderStatus.FILLED
    closed = feed(ex, clock, make_candle(T0, D(100), D(100), D(94), D(95)))  # SPOT-Kerze reisst den Stop
    assert [t.instrument.market_type for t in closed] == [MarketType.SPOT]
    assert [p.instrument.market_type for p in ex.open_positions] == [MarketType.FUTURES]
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT market_type, mark FROM instrument_state ORDER BY market_type").fetchall() == \
            [("futures", D(100)), ("spot", D(95))]


def test_limits_hold_through_the_database_path(pg):
    ex, clock = pg_exec(pg)
    results = [ex.submit(intent("o%d" % i, "S%d-USD" % i, MarketType.FUTURES, lev=D(2)),
                         snap(clock, "S%d-USD" % i, **FUT)) for i in range(5)]
    assert [r.status for r in results] == [OrderStatus.FILLED] * 4 + [OrderStatus.REJECTED]
    assert results[4].reasons == (R.PORTFOLIO_RISK_TOO_HIGH,)
    ex2, clock2 = pg_exec(pg, account="b")
    assert ex2.submit(intent("big", qty=D(40), stop=D(99), tp=D(102)), snap(clock2)).status is OrderStatus.FILLED
    r = ex2.submit(intent("more", "ETH-USD", qty=D(10), stop=D(99), tp=D(102)), snap(clock2, "ETH-USD"))
    assert r.reasons == (R.STRESS_LOSS_TOO_HIGH,)  # 4,02 % + 1,0 % > 5 %


def test_database_belt_never_contradicts_the_application_decision(pg):
    """Der Trigger beim COMMIT rechnet Gesamtrisiko und Exposure nach. Weicht er auch nur um eine
    Rundungsstelle von der Anwendung ab, wuerde ein freigegebener Trade mit einem Datenbankfehler enden."""
    rng = random.Random(11)
    ex, clock = pg_exec(pg, costs=CostModel(), equity=D(50000))
    sora = RuleBasedSora(SizingPolicy.from_limits(DEFAULT_LIMITS), CostModel())
    filled = 0
    base = {"T%d-USD" % k: D(str(round(rng.uniform(5, 500), 4))) for k in range(7)}
    for i in range(120):
        clock.advance(5)  # 10 Minuten insgesamt: Bewertungskurse bleiben aktuell (Grenze 15 Minuten)
        sym = "T%d-USD" % rng.randrange(7)
        mt = rng.choice([MarketType.SPOT, MarketType.FUTURES])
        side = Side.LONG if mt is MarketType.SPOT else rng.choice([Side.LONG, Side.SHORT])
        price = (base[sym] * D(str(round(rng.uniform(0.97, 1.03), 5)))).quantize(D("0.0001"))
        dist = D(str(round(rng.uniform(0.008, 0.09), 4)))
        setup = make_setup("r%d" % i, symbol=sym, market_type=mt, side=side, entry=price,
                           stop=price * (1 - side.sign * dist), tp=price * (1 + side.sign * dist * 2))
        s = snapshot(symbol=sym, mid=price, half_spread=price * D("0.0003"), ts=clock.now(), market_type=mt,
                     funding=D("0.00013") if mt is MarketType.FUTURES else None)
        instrument = setup.instrument
        if any(p.instrument == instrument for p in ex.open_positions):
            ex.close_position(instrument, s, ExitReason.MANUAL, "c%d" % i)  # darf keinen DB-Fehler ausloesen
            continue
        p = sora.size(setup, MarketContext(s), ex.portfolio_state())
        if p.approve:
            r = ex.submit(OrderIntent(setup.setup_id, setup, p.quantity, p.leverage), s)  # kein CheckViolation
            filled += r.status is OrderStatus.FILLED
            assert r.status is OrderStatus.FILLED, r.reasons
    st = ex.portfolio_state()
    assert filled > 25 and st.open_risk / st.equity <= D("0.02") and st.gross_exposure / st.equity <= D(1)
    with psycopg.connect(pg.admin_url) as c:  # die Datenbank sieht exakt dieselben Zahlen
        db = c.execute(
            "SELECT (SELECT coalesce(sum(risk_at_open - entry_fee), 0) FROM positions WHERE status='OPEN'),"
            " (SELECT coalesce(sum(p.quantity * s.mark), 0) FROM positions p JOIN instrument_state s"
            "    ON (s.exchange, s.market_type, s.symbol) = (p.exchange, p.market_type, p.symbol)"
            "   WHERE p.status='OPEN'), (SELECT equity FROM accounts)").fetchone()
    assert db == (st.open_risk, st.gross_exposure, st.equity)


def test_peak_equity_is_persisted_by_portfolio_state(pg):
    ex, clock = pg_exec(pg)
    ex.submit(intent("a"), snap(clock))
    feed(ex, clock, make_candle(T0, D(100), D(104), D(100), D(104)))  # +36 unrealisiert
    state = ex.portfolio_state()
    assert state.peak_equity == state.equity == D("10035.46")
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT peak_equity, equity FROM accounts").fetchone() == (state.peak_equity, state.equity)


def test_two_executor_instances_share_one_truth(pg):
    ex1, clock = pg_exec(pg)
    ex2, _ = pg_exec(pg)
    assert ex1.submit(intent("a"), snap(clock)).status is OrderStatus.FILLED
    assert ex2.submit(intent("a"), snap(clock)).reasons == (R.DUPLICATE_ORDER_ID,)
    assert ex2.submit(intent("b"), snap(clock)).reasons == (R.DUPLICATE_POSITION,)
    assert ex1.open_positions == ex2.open_positions and len(ex2.open_positions) == 1


def test_process_with_a_wrong_clock_cannot_reset_the_daily_loss_limit(pg):
    """Audit H4, ueber die Datenbank: Prozess A hat das Tageslimit erreicht; Prozess B mit falscher Uhr fragt ab."""
    clock = FixedClock(T0)
    a, _ = pg_exec(pg, clock)
    for i in range(7):
        assert a.submit(intent("t%d" % i, qty=D(40), stop=D(99), tp=D(102)), snap(clock)).status is OrderStatus.FILLED
        feed(a, clock, make_candle(clock.now(), D(100), D(100), D(98), D("98.5")))
        clock.advance(120)
    assert a.submit(intent("blocked", "ETH-USD"), snap(clock, "ETH-USD")).reasons == (R.TRADING_HALTED, R.DAILY_LOSS_LIMIT)
    b, _ = pg_exec(pg, FixedClock(T0 - timedelta(days=1)))
    b.portfolio_state()
    with psycopg.connect(pg.admin_url) as c:
        assert c.execute("SELECT day, day_start_equity FROM accounts").fetchone() == (T0.date(), D(10000))
    assert a.submit(intent("still", "SOL-USD"), snap(clock, "SOL-USD")).reasons == (R.TRADING_HALTED, R.DAILY_LOSS_LIMIT)


def test_application_role_is_used_never_the_administrator(pg):
    ex, _ = pg_exec(pg)
    with ex._transaction() as cur:
        cur.execute("SELECT current_user, (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS su")
        row = cur.fetchone()
    assert row == {"current_user": "trading_app", "su": False}


# ------------------------------------------------------------------ Pipeline gegen PostgreSQL


def make_pipeline(pg, setups, clock, exits=()):
    ex, _ = pg_exec(pg, clock)
    log = PostgresEventLog(pg.app_url, clock)
    rin = RuleBasedRin(TradingUniverse.of(spot=["BTC-USD"], futures=["BTC-USD", "ETH-USD"]), clock, RinConfig(), NO_SLIP)
    sora = RuleBasedSora(SizingPolicy.from_limits(DEFAULT_LIMITS), NO_SLIP)
    pipe = TradingPipeline(StubMomo(setups, exits), rin, StubKibo(), StubYuzu(), sora, AkiAgent(log), ex)
    return pipe, ex, log


def test_full_pipeline_with_postgres_executor_and_postgres_aki_log(pg):
    from trading.agents import ExitSignal

    clock = FixedClock(T0)
    signal = ExitSignal("exit-1", inst("BTC-USD"), "test", T0, "fertig")
    pipe, ex, log = make_pipeline(pg, [make_setup("p1", tp=D("102.5")), make_setup("p2", symbol="ZZZ-USD")],
                                  clock, exits=[signal])
    out = pipe.run_cycle([MarketContext(snap(clock))])
    assert [o.executed for o in out] == [True, False] and out[1].reasons == (R.NO_MARKET_DATA,)
    assert ex.open_positions[0].risk_at_open <= D(40)
    clock.advance(60)
    (sig,) = pipe.propose_exits([MarketContext(snap(clock))], ex.open_positions)
    assert pipe.process_exit(sig, MarketContext(snap(clock, mid=D(101)))).closed and ex.open_positions == ()
    types = [e.event_type for e in log.events()]
    # erst werden alle Vorschlaege dokumentiert, dann wird einer nach dem anderen geprueft und ausgefuehrt
    assert types[:3] == ["SETUP_PROPOSED", "SETUP_PROPOSED", "AGENT_OPINION"] and "SIZING_PROPOSED" in types
    assert types.count("ORDER_FILLED") == 1 and types.count("SETUP_BLOCKED") == 1
    assert types[-2:] == ["EXIT_SIGNAL", "POSITION_CLOSED"]
    assert log.verify().ok


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


