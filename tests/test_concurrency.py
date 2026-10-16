"""Gleichzeitige Trade-Anfragen: Limits und Doppel-Order-Schutz muessen auch unter Parallelitaet halten."""

import itertools
import threading
import time
from typing import List, Sequence

import pytest
from helpers import D, FUT, Env, T0, inst, make_candle, make_setup, minutes
from stubs import StubKibo, StubYuzu

from trading.agents import (
    AkiAgent,
    MarketContext,
    MomoAgent,
    RinConfig,
    RuleBasedRin,
    RuleBasedSora,
    SizingPolicy,
    SoraConfig,
)
from trading.audit.events import EventType
from trading.domain.models import ExitReason, FundingEvent, MarketType, OrderStatus, TradeSetup
from trading.domain.reasons import RejectReason as R
from trading.domain.universe import TradingUniverse
from trading.pipeline import TradingPipeline
from trading.safety.limits import DEFAULT_LIMITS

N_PARALLEL_REPEATS = 5  # mehrere Durchlaeufe, weil Race Conditions nicht jedes Mal auftreten


def run_parallel(n: int, fn) -> List[object]:
    """Startet n Threads gleichzeitig (Barriere) und sammelt Ergebnisse; Exceptions lassen den Test scheitern."""
    barrier = threading.Barrier(n)
    results, errors = [None] * n, []

    def worker(i):
        try:
            barrier.wait(timeout=10)
            results[i] = fn(i)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert errors == [], errors
    assert all(not t.is_alive() for t in threads), "Deadlock?"
    return results


def widen_race_window(env, delay=0.003):
    """Verlangsamt die Risikopruefung kuenstlich. Ohne korrekte Sperre wuerden sich Threads
    dadurch zuverlaessig ueberholen (Pruefung und Eintragung der Position waeren nicht unteilbar)."""
    original = env.risk.evaluate

    def slow_evaluate(*args, **kwargs):
        decision = original(*args, **kwargs)
        time.sleep(delay)
        return decision

    env.risk.evaluate = slow_evaluate  # nur diese Test-Instanz


# ------------------------------------------------------------------ Executor direkt


@pytest.mark.parametrize("run", range(N_PARALLEL_REPEATS))
def test_parallel_orders_never_exceed_the_two_percent_cap(run):
    env = Env()
    widen_race_window(env)

    def place(i):
        # Kleine Positionen mit weitem Stop (9 Stueck, Stop 95): Hoechstverlust 46,143 = 0,46 %.
        # Vier passen unter 2 %; beim fuenften griffe NUR das Gesamtrisiko (Exposure 45 %, Stress 4,5 %).
        sym = "S%d-USD" % i
        return env.order("o%d" % i, qty=D(9), lev=D(2), symbol=sym, market_type=MarketType.FUTURES,
                         stop=D(95), tp=D(110), snap_kw=dict(FUT, symbol=sym))

    results = run_parallel(24, place)
    filled = [r for r in results if r.status is OrderStatus.FILLED]
    rejected = [r for r in results if r.status is OrderStatus.REJECTED]
    assert len(filled) == 4 and len(rejected) == 20  # 4 x 0,46 % passen, der 5. nicht
    assert all(r.reasons == (R.PORTFOLIO_RISK_TOO_HIGH,) for r in rejected)
    state = env.executor.portfolio_state()
    assert state.open_risk / state.equity <= D("0.02")
    assert state.gross_exposure / state.equity <= D(1) and state.stress_loss / state.equity <= D("0.05")
    assert state.open_count == 4
    assert len(env.log.of_type(EventType.ORDER_FILLED)) == 4
    assert len(env.log.of_type(EventType.ORDER_REJECTED)) == 20
    assert env.log.verify().ok


@pytest.mark.parametrize("run", range(N_PARALLEL_REPEATS))
def test_same_order_id_sent_in_parallel_is_executed_exactly_once(run):
    env = Env()
    widen_race_window(env)
    results = run_parallel(16, lambda i: env.order("same-id"))
    first = [r for r in results if not r.replayed]
    assert len(first) == 1 and first[0].status is OrderStatus.FILLED and first[0].position is not None
    # Die 15 Wiederholungen bekommen das urspruengliche Ergebnis - dieselbe Position, kein zweiter Handel.
    assert sum(r.replayed and r.position == first[0].position for r in results) == 15
    assert len(env.executor.open_positions) == 1
    assert len(env.log.of_type(EventType.ORDER_FILLED)) == 1 and len(env.log.of_type(EventType.ORDER_REPLAYED)) == 15


@pytest.mark.parametrize("run", range(N_PARALLEL_REPEATS))
def test_parallel_orders_for_the_same_market_open_only_one_position(run):
    env = Env()
    widen_race_window(env)
    results = run_parallel(16, lambda i: env.order("id-%d" % i))
    assert sum(r.status is OrderStatus.FILLED for r in results) == 1
    assert sum(R.DUPLICATE_POSITION in r.reasons for r in results) == 15


def test_queries_during_trading_do_not_break_or_deadlock():
    env = Env()

    def mixed(i):
        if i % 3 == 0:
            return env.order("o%d" % i, qty=D(9), lev=D(2), symbol="S%d-USD" % i, stop=D(95), tp=D(110),
                             market_type=MarketType.FUTURES, snap_kw=dict(FUT, symbol="S%d-USD" % i))
        if i % 3 == 1:
            return env.executor.portfolio_state()
        return (env.executor.equity(), env.executor.open_positions, env.executor.closed_trades)

    run_parallel(30, mixed)
    assert env.log.verify().ok


# ------------------------------------------------------------------ komplette Pipeline


class OneSetupPerMarketMomo(MomoAgent):
    """Test-MOMO: schlaegt fuer jeden uebergebenen Markt genau einen Long-Trade vor."""

    def __init__(self):
        super().__init__()
        self._counter = itertools.count()
        self._lock = threading.Lock()

    def scan(self, contexts: Sequence[MarketContext]) -> List[TradeSetup]:
        out = []
        for c in contexts:
            with self._lock:
                n = next(self._counter)
            s = c.snapshot
            # Weiter Stop (5 %): so begrenzt das Risiko-Budget, nicht der Stress-Test.
            out.append(make_setup("par-%d" % n, symbol=s.symbol, market_type=s.market_type,
                                  entry=s.mid, stop=s.mid * D("0.95"), tp=s.mid * D("1.15")))
        return out


def build_pipeline(env, symbols):
    universe = TradingUniverse.of(futures=symbols)
    rin = RuleBasedRin(universe, env.clock, RinConfig(), env.risk.costs)
    sora = RuleBasedSora(SizingPolicy.from_limits(DEFAULT_LIMITS), env.risk.costs)
    aki = AkiAgent(env.log)
    return TradingPipeline(OneSetupPerMarketMomo(), rin, StubKibo(), StubYuzu(), sora, aki, env.executor)


@pytest.mark.parametrize("run", range(N_PARALLEL_REPEATS))
def test_parallel_pipeline_cycles_share_one_risk_budget(run):
    env = Env()
    widen_race_window(env)
    symbols = ["AA%d-USD" % i for i in range(12)]
    pipe = build_pipeline(env, symbols)

    def cycle(i):
        ctx = MarketContext(env.snap(symbol=symbols[i], mid=D(100), **FUT))
        return pipe.run_cycle([ctx])[0]

    outcomes = run_parallel(12, cycle)
    executed = [o for o in outcomes if o.executed]
    state = env.executor.portfolio_state()

    assert len(executed) == 5  # Positionslimit; vorher waere das 2-%-Budget nie ueberschritten
    assert state.open_count == 5
    assert state.open_risk / state.equity <= D("0.02")
    for o in outcomes:
        if not o.executed:
            assert o.reasons, "jede Blockade braucht einen Grund"
    # Nachvollziehbarkeit: 12 Vorschlaege, 5 ausgefuehrt, 7 blockiert - alles im Protokoll
    assert len(env.log.of_type(EventType.SETUP_PROPOSED)) == 12
    assert len(env.log.of_type(EventType.ORDER_FILLED)) == 5
    blocked = len(env.log.of_type(EventType.SETUP_BLOCKED)) + len(env.log.of_type(EventType.ORDER_REJECTED))
    assert blocked == 7
    assert env.log.verify().ok


def test_parallel_and_sequential_runs_end_in_the_same_risk_state():
    def run(parallel):
        env = Env()
        symbols = ["AA%d-USD" % i for i in range(8)]
        pipe = build_pipeline(env, symbols)
        fn = lambda i: pipe.run_cycle([MarketContext(env.snap(symbol=symbols[i], mid=D(100), **FUT))])
        if parallel:
            run_parallel(8, fn)
        else:
            [fn(i) for i in range(8)]
        s = env.executor.portfolio_state()
        return s.open_count, s.open_risk / s.equity <= D("0.02")

    assert run(True) == run(False) == (5, True)


class SlowSora(RuleBasedSora):
    """Rechnet absichtlich langsam. Rechnete SORA ausserhalb der Sperre des Executors, saehen so alle Pipelines
    denselben (leeren) Zustand und schluegen dieselbe Groesse vor - die spaeten wuerden dann abgelehnt (Audit M8)."""

    def size(self, setup, context, portfolio):
        time.sleep(0.01)
        return super().size(setup, context, portfolio)


def separate_pipelines(env, symbols, risk_per_trade):
    """Je Markt eine EIGENE Pipeline (eigene Sperre) - wie mehrere Prozesse, die sich nur den Executor teilen."""
    universe = TradingUniverse.of(futures=symbols)
    policy = SizingPolicy.from_limits(DEFAULT_LIMITS)
    momo = OneSetupPerMarketMomo()  # gemeinsam, damit jede Setup-ID nur einmal vorkommt
    return [
        TradingPipeline(momo, RuleBasedRin(universe, env.clock, RinConfig(), env.risk.costs),
                        StubKibo(), StubYuzu(),
                        SlowSora(policy, env.risk.costs, SoraConfig(risk_per_trade=risk_per_trade)),
                        AkiAgent(env.log), env.executor)
        for _ in symbols
    ]


@pytest.mark.parametrize("run", range(N_PARALLEL_REPEATS))
def test_competing_pipelines_shrink_into_the_remaining_budget_instead_of_being_rejected(run):
    """Audit M8: Ziel 0,45 % je Trade -> vier passen, das Restbudget (ca. 0,2 %) bekommt der fuenfte in KLEINERER
    Groesse. Dass SORA unter der Sperre rechnet, macht das Ergebnis exakt - egal, wer zuerst kommt."""
    env = Env()
    symbols = ["RS%d-USD" % i for i in range(8)]
    pipes = separate_pipelines(env, symbols, D("0.0045"))

    def cycle(i):
        return pipes[i].run_cycle([MarketContext(env.snap(symbol=symbols[i], mid=D(100), **FUT))])[0]

    outcomes = run_parallel(8, cycle)
    executed = [o for o in outcomes if o.executed]
    state = env.executor.portfolio_state()
    assert len(executed) == 5 and state.open_count == 5  # Positionslimit, nicht ein veralteter SORA-Vorschlag
    risks = sorted(o.result.risk.risk_pct for o in executed)
    assert all(r > D("0.0044") for r in risks[1:])  # vier in voller Groesse
    assert D("0.001") < risks[0] < D("0.0025")  # der fuenfte verkleinert ins Restbudget
    assert state.open_risk / state.equity <= D("0.02")
    blocked = [o for o in outcomes if not o.executed]
    assert all(o.stage == "SORA" and o.reasons == (R.TOO_MANY_POSITIONS,) for o in blocked), blocked
    assert env.log.of_type(EventType.ORDER_REJECTED) == ()  # der Risikomanager musste nichts mehr ablehnen
    assert env.log.verify().ok


def test_competing_pipelines_end_exactly_like_a_sequential_run():
    def run(parallel):
        env = Env()
        symbols = ["RQ%d-USD" % i for i in range(8)]
        pipes = separate_pipelines(env, symbols, D("0.0045"))
        fn = lambda i: pipes[i].run_cycle([MarketContext(env.snap(symbol=symbols[i], mid=D(100), **FUT))])
        if parallel:
            run_parallel(8, fn)
        else:
            [fn(i) for i in range(8)]
        s = env.executor.portfolio_state()
        return s.open_count, sorted(p.quantity for p in env.executor.open_positions), s.open_risk

    parallel, sequential = run(True), run(False)
    # Gleiche Mengen (die Reihenfolge der Maerkte darf abweichen) und gleiches Gesamtrisiko.
    assert parallel == sequential


# ------------------------------------------------------------------ Stufe 1: neue Operationen unter Parallelitaet


def open_futures(env, qty=D(9)):
    r = env.order("base", qty=qty, lev=D(2), market_type=MarketType.FUTURES, stop=D(95), tp=D(110),
                  snap_kw=dict(FUT))
    assert r.status is OrderStatus.FILLED
    return inst("BTC-USD", MarketType.FUTURES)


@pytest.mark.parametrize("run", range(N_PARALLEL_REPEATS))
def test_parallel_close_requests_close_the_position_exactly_once(run):
    env = Env()
    widen_race_window(env)
    instrument = open_futures(env)
    snap = env.snap(**FUT)
    results = run_parallel(16, lambda i: env.executor.close_position(instrument, snap, ExitReason.SIGNAL, "c%d" % i))
    assert sum(r.closed for r in results) == 1
    assert sum(r.reasons == (R.NO_POSITION,) for r in results) == 15
    assert len(env.executor.closed_trades) == 1 and env.executor.open_positions == ()
    assert env.executor.balance == env.equity0 + env.executor.closed_trades[0].net_pnl
    assert len(env.log.of_type(EventType.POSITION_CLOSED)) == 1 and env.log.verify().ok


@pytest.mark.parametrize("run", range(N_PARALLEL_REPEATS))
def test_parallel_delivery_of_the_same_funding_event_charges_once(run):
    env = Env()
    instrument = open_futures(env)
    env.clock.advance(4 * 3600)
    event = FundingEvent(instrument, env.clock.now(), D("0.0001"), D(100))
    costs = run_parallel(16, lambda i: env.executor.apply_funding(event))
    assert sorted(costs)[-1] == D("0.09") and sum(costs) == D("0.09")  # 9 x 100 x 0,01 %, genau einmal
    assert env.executor.open_positions[0].funding_paid == D("0.09")
    assert len(env.log.of_type(EventType.FUNDING_APPLIED)) == 1


@pytest.mark.parametrize("run", range(N_PARALLEL_REPEATS))
def test_parallel_delivery_of_the_same_candle_is_processed_once(run):
    env = Env()
    open_futures(env)
    stop_candle = make_candle(T0, D(100), D(100), D(94), D(95), market_type=MarketType.FUTURES)
    env.clock.set(stop_candle.close_time)
    results = run_parallel(16, lambda i: env.executor.on_candle(stop_candle))
    assert sorted(len(r) for r in results) == [0] * 15 + [1]  # genau EIN Stop-Out
    assert len(env.executor.closed_trades) == 1
    rejected = [tuple(e.payload["reasons"]) for e in env.log.of_type(EventType.DATA_REJECTED)]
    assert rejected == [("DUPLICATE_CANDLE",)] * 15


def test_orders_closes_candles_and_funding_in_parallel_keep_the_books_consistent():
    env = Env(equity=D(100000))
    symbols = ["M%d-USD" % i for i in range(6)]

    def work(i):
        sym = symbols[i % 6]
        instrument = inst(sym, MarketType.FUTURES)
        kind = i % 4
        if kind == 0:
            return env.order("o%d" % i, qty=D(9), lev=D(2), symbol=sym, market_type=MarketType.FUTURES,
                             stop=D(95), tp=D(110), snap_kw=dict(FUT, symbol=sym))
        if kind == 1:
            return env.executor.close_position(instrument, env.snap(symbol=sym, **FUT), ExitReason.MANUAL, "c%d" % i)
        if kind == 2:
            return env.executor.on_candle(make_candle(T0 - minutes(1), D(100), D(100.5), D(99.5), D(100),
                                                      symbol=sym, market_type=MarketType.FUTURES))
        return env.executor.apply_funding(FundingEvent(instrument, T0, D("0.0001"), D(100)))

    run_parallel(48, work)
    ex = env.executor
    expected = env.equity0 + sum(t.net_pnl for t in ex.closed_trades) \
        - sum(p.entry_fee + p.funding_paid for p in ex.open_positions)
    assert ex.balance == expected
    s = ex.portfolio_state()
    assert s.open_count == len(ex.open_positions) == len({p.instrument for p in ex.open_positions})
    assert s.open_risk / s.equity <= D("0.02") and env.log.verify().ok
