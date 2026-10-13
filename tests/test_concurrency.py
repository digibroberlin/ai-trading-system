"""Gleichzeitige Trade-Anfragen: Limits und Doppel-Order-Schutz muessen auch unter Parallelitaet halten."""

import itertools
import threading
import time
from typing import List, Sequence

import pytest
from helpers import D, Env, make_setup, snapshot
from stubs import StubKibo, StubYuzu

from trading.agents import (
    AkiAgent,
    MarketContext,
    MomoAgent,
    RinConfig,
    RuleBasedRin,
    RuleBasedSora,
    SizingPolicy,
)
from trading.audit.events import EventType
from trading.domain.models import MarketType, OrderStatus, TradeSetup
from trading.domain.reasons import RejectReason as R
from trading.domain.universe import TradingUniverse
from trading.pipeline import TradingPipeline
from trading.safety.limits import DEFAULT_LIMITS

FUT = {"market_type": MarketType.FUTURES, "funding": D("0.0001")}
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
        sym = "S%d" % i
        return env.order("o%d" % i, qty=D(40), lev=D(2), symbol=sym, market_type=MarketType.FUTURES,
                         snap_kw=dict(FUT, symbol=sym))

    results = run_parallel(24, place)
    filled = [r for r in results if r.status is OrderStatus.FILLED]
    rejected = [r for r in results if r.status is OrderStatus.REJECTED]
    assert len(filled) == 4 and len(rejected) == 20  # 4 x 0,45 % passen, der 5. nicht
    state = env.executor.portfolio_state()
    assert state.open_risk / state.equity <= D("0.02")
    assert state.open_count == 4
    assert len(env.log.of_type(EventType.ORDER_FILLED)) == 4
    assert len(env.log.of_type(EventType.ORDER_REJECTED)) == 20
    assert env.log.verify().ok


@pytest.mark.parametrize("run", range(N_PARALLEL_REPEATS))
def test_same_order_id_sent_in_parallel_is_executed_exactly_once(run):
    env = Env()
    widen_race_window(env)
    results = run_parallel(16, lambda i: env.order("same-id"))
    assert sum(r.status is OrderStatus.FILLED for r in results) == 1
    assert sum(R.DUPLICATE_ORDER_ID in r.reasons for r in results) == 15
    assert len(env.executor.open_positions) == 1


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
            return env.order("o%d" % i, qty=D(40), lev=D(2), symbol="S%d" % i,
                             market_type=MarketType.FUTURES, snap_kw=dict(FUT, symbol="S%d" % i))
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
            out.append(make_setup("par-%d" % n, symbol=s.symbol, market_type=s.market_type,
                                  entry=s.mid, stop=s.mid * D("0.99"), tp=s.mid * D("1.03")))
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
