"""Laufzeit-Engine (AP-21): standardmaessig aus; ereignisgesteuert mit simulierter Uhr und erfundenen Daten; Tausende
Ereignisse, Neustart ohne Doppel-Order, Fehler -> Not-Aus, KI-Kosten je Ereignis, Marktphasen-Wechsel."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from bt_helpers import BTC, ETHF, bars, synthetic
from helpers import T0

from trading.agents.aki import AkiAgent
from trading.agents.ai_review import AIReview
from trading.agents.kibo_rules import RuleBasedKibo
from trading.audit.event_log import InMemoryEventLog
from trading.audit.events import EventType
from trading.backtest.strategy import StrategyDecision
from trading.clock import FixedClock
from trading.config import ConfigError
from trading.domain.models import Candle
from trading.domain.universe import TradingUniverse
from trading.execution.paper_executor import PaperExecutor
from trading.runtime.engine import (
    APPROVAL_ENV,
    APPROVAL_TEXT,
    EngineConfig,
    EngineDisabledError,
    RuntimeEngine,
    check_start_allowed,
    simulate,
)
from trading.safety.data_validator import SnapshotValidator
from trading.safety.limits import DEFAULT_LIMITS
from trading.safety.risk_manager import RiskManager
from trading.strategies import BreakoutStrategy, build_strategy

D = Decimal
HOUR = timedelta(hours=1)
APPROVED = {APPROVAL_ENV: APPROVAL_TEXT}


class Env:
    def __init__(self, strategy=None, kibo=None, regime_review=None, funding=True, executor=None, aki=None, log=None,
                 clock=None, config=None):
        self.clock = clock or FixedClock(T0)
        self.log = log or InMemoryEventLog(self.clock)
        self.executor = executor or PaperExecutor(D(10000), RiskManager(DEFAULT_LIMITS),
                                                  SnapshotValidator(DEFAULT_LIMITS, self.clock), self.log, self.clock)
        self.aki = aki or AkiAgent(self.log)
        self.engine = RuntimeEngine(strategy or BreakoutStrategy(), self.executor, self.aki, self.clock,
                                    TradingUniverse(frozenset({BTC, ETHF})), kibo=kibo, regime_review=regime_review,
                                    funding_rates={ETHF: D("0.0001")} if funding else None, config=config)


def events(days=60, seed=5):
    ds = synthetic(days=days, seed=seed)
    return [c for s in ds.series for c in s.candles] + [f for s in ds.series for f in s.funding]


# ------------------------------------------------------------------ Freigabe


def test_engine_is_off_unless_enabled_paper_and_approved_by_the_owner():
    env = Env()
    with pytest.raises(EngineDisabledError, match="deaktiviert"):
        env.engine.run([], APPROVED)
    with pytest.raises(EngineDisabledError, match="Freigabe"):
        check_start_allowed(EngineConfig(enabled=True), {})
    with pytest.raises(EngineDisabledError, match="Freigabe"):
        check_start_allowed(EngineConfig(enabled=True), {APPROVAL_ENV: "ja"})
    with pytest.raises(ConfigError):
        check_start_allowed(EngineConfig(enabled=True), dict(APPROVED, TRADING_MODE="live"))
    with pytest.raises(ConfigError):
        check_start_allowed(EngineConfig(enabled=True), dict(APPROVED, BITGET_API_KEY="x" * 20))
    check_start_allowed(EngineConfig(enabled=True), APPROVED)  # nur so darf es laufen
    assert Env(config=EngineConfig(enabled=True)).engine.run([], APPROVED).events == 0


@pytest.mark.parametrize("bad", [dict(history_bars=0), dict(max_consecutive_errors=0), dict(spread_bps=D(-1))])
def test_invalid_engine_settings_are_rejected(bad):
    with pytest.raises(ValueError):
        EngineConfig(**bad)


# ------------------------------------------------------------------ viele Ereignisse


def test_thousands_of_events_run_through_all_agents_with_consistent_accounting():
    env = Env()
    evts = events(days=60)
    stats = simulate(env.engine, evts, env.clock)
    candles = sum(1 for e in evts if isinstance(e, Candle))
    assert stats.events == len(evts) > 3000 and stats.candles == candles == stats.decisions
    assert stats.errors == 0 and stats.rejected_candles == 0 and stats.fundings > 100
    assert stats.setups > 0 and stats.executed > 0 and stats.closed_trades > 0
    assert stats.setups == stats.executed + stats.blocked + stats.replayed
    report = env.aki.report(D(10000), env.executor.open_positions)  # Endkapital = Start + realisiert + unrealisiert
    assert report.trades.trade_count == len(env.executor.closed_trades) == stats.closed_trades
    agents = {e.payload["agent"] for e in env.log.of_type(EventType.AGENT_OPINION)}
    assert {"RIN", "KIBO", "YUZU"} <= agents and env.log.verify().ok
    assert stats.ai_paid_calls == 0 and stats.ai_cost == 0  # ohne KI-Pruefung: schneller Regelpfad, kostenlos


def test_runs_are_reproducible():
    a, b = Env(), Env()
    evts = events(days=20)
    simulate(a.engine, evts, a.clock)
    simulate(b.engine, list(reversed(evts)), b.clock)  # Eingangsreihenfolge egal: zeitlich sortiert abgespielt
    assert a.engine.stats == b.engine.stats and a.log.events()[-1].hash == b.log.events()[-1].hash


# ------------------------------------------------------------------ Neustart


def test_restart_with_warm_up_continues_exactly_like_an_uninterrupted_run():
    evts = events(days=30)
    cut = T0 + timedelta(days=15)
    early = [e for e in evts if (e.close_time if isinstance(e, Candle) else e.time) <= cut]
    late = [e for e in evts if e not in early]
    whole = Env()
    simulate(whole.engine, evts, whole.clock)

    first = Env()
    simulate(first.engine, early, first.clock)
    # Neustart: Executor/Protokoll bleiben (im Betrieb in PostgreSQL), die Engine beginnt leer und fuellt nur Historie auf
    second = Env(executor=first.executor, aki=first.aki, log=first.log, clock=first.clock)
    logged = len(first.log.events())
    taken = second.engine.warm_up([e for e in early if isinstance(e, Candle)])
    assert len(first.log.events()) == logged  # Aufwaermen entscheidet nichts und protokolliert nichts
    assert taken == sum(1 for e in early if isinstance(e, Candle))
    assert len(first.executor.closed_trades) == len(second.executor.closed_trades)  # Aufwaermen handelt nie
    simulate(second.engine, late, first.clock)
    assert [t.setup_id for t in second.executor.closed_trades] == [t.setup_id for t in whole.executor.closed_trades]


def test_the_same_candle_after_a_restart_never_opens_a_second_position():
    env = Env(strategy=BreakoutStrategy(entry=20, exit=10))
    rows = [[100, 101, 99, 100]] * 30 + [[100, 104, 100, 103]]
    candles = bars(BTC, rows)
    for c in candles:
        env.clock.set(c.close_time)
        out = env.engine.process(c)
    assert out.executed == 1 and len(env.executor.open_positions) == 1
    again = Env(strategy=BreakoutStrategy(entry=20, exit=10), executor=env.executor, aki=env.aki, log=env.log,
                clock=env.clock)
    fills = len(env.log.of_type(EventType.ORDER_FILLED))
    again.engine.warm_up(candles[:-1])
    out = again.engine.process(candles[-1])  # dieselbe Kerze noch einmal (z. B. nach Absturz vor der Bestaetigung)
    assert out.executed == 0 and len(env.executor.open_positions) == 1
    assert len(env.log.of_type(EventType.ORDER_FILLED)) == fills  # nichts neu ausgefuehrt


def test_setup_ids_come_from_the_decision_time_so_a_repeated_order_is_replayed_not_doubled():
    """Kennungen sind reproduzierbar (Strategie|Instrument|Richtung|Zeit). Erreicht dieselbe Entscheidung den Executor
    ein zweites Mal, liefert er das urspruengliche Ergebnis (AP-08) - auch wenn alle Vorstufen sie durchliessen."""
    from trading.domain.models import OrderIntent

    env = Env(strategy=BreakoutStrategy(entry=20, exit=10))
    rows = [[100, 101, 99, 100]] * 30 + [[100, 104, 100, 103]]
    candles = bars(BTC, rows)
    for c in candles:
        env.clock.set(c.close_time)
        env.engine.process(c)
    position = env.executor.open_positions[0]
    assert position.setup_id == "breakout-20-10|%s|long|%s" % (BTC.key, candles[-1].close_time.strftime("%Y%m%dT%H%M%SZ"))
    fill = env.log.of_type(EventType.ORDER_FILLED)[-1].payload
    assert fill["client_order_id"] == position.setup_id


# ------------------------------------------------------------------ Fehlerfaelle


class Broken(BreakoutStrategy):
    def on_bar(self, view):
        raise RuntimeError("Strategie kaputt")


def test_repeated_errors_trigger_the_kill_switch():
    env = Env(strategy=Broken(), config=EngineConfig(max_consecutive_errors=3))
    for c in bars(BTC, [[100, 101, 99, 100]] * 3):
        env.clock.set(c.close_time)
        out = env.engine.process(c)
    assert out.kind == "error" and "Not-Aus" in out.note and env.executor.halt_status().halted
    assert len(env.log.of_type(EventType.SYSTEM_ERROR)) == 3 and env.engine.stats.errors == 3


def test_bad_and_duplicate_candles_are_rejected_without_trading():
    env = Env()
    good = bars(BTC, [[100, 101, 99, 100]] * 2)
    env.clock.set(good[1].close_time)
    env.engine.process(good[0])
    broken = Candle(BTC, good[1].open_time, 3600, D(100), D(99), D(101), D(100), D(1))  # Hoch unter Tief
    assert env.engine.process(broken).kind == "rejected"
    assert env.engine.process(good[0]).kind == "rejected"  # doppelt
    future = bars(BTC, [[100, 101, 99, 100]], start=T0 + 10 * HOUR)[0]
    assert env.engine.process(future).kind == "rejected"  # noch nicht abgeschlossen
    assert env.engine.stats.rejected_candles == 3 and env.engine.stats.decisions == 1
    assert len(env.log.of_type(EventType.DATA_REJECTED)) >= 2


def test_futures_without_a_known_funding_rate_are_blocked():
    env = Env(strategy=build_strategy("breakout"), funding=False)
    rows = [[100, 101, 99, 100]] * 30 + [[100, 100, 96, 97]]  # Ausbruch nach unten -> Short-Vorschlag (Futures)
    for c in bars(ETHF, rows):
        env.clock.set(c.close_time)
        env.engine.process(c)
    assert env.engine.stats.setups == 1 and env.engine.stats.executed == 0 and env.engine.stats.blocked == 1
    reasons = [r for e in env.log.of_type(EventType.SETUP_BLOCKED) for r in e.payload["reasons"]]
    assert "FUNDING_MISSING" in reasons


# ------------------------------------------------------------------ KI nur ereignisbasiert, Kosten je Ereignis


def review_env(budget=None, text='{"approve": true, "confidence": 0.9}'):
    from test_ai_orchestration import BUDGET, FakeProvider
    from test_ai_orchestration import Env as AIEnv

    return AIEnv(FakeProvider(text=text), budget=budget or BUDGET)


def test_ai_costs_are_measured_per_event_and_only_where_a_setup_needs_it():
    ai = review_env()
    env = Env(kibo=RuleBasedKibo(review=AIReview(ai.orch, D(50))))
    ai.orch._recorder = env.aki.record_ai_call  # Messpunkte an AKI (wie im Betrieb)
    outcomes = []
    for e in sorted(events(days=20), key=lambda e: (e.close_time if isinstance(e, Candle) else e.time)):
        env.clock.set(e.close_time if isinstance(e, Candle) else e.time)
        ai.clock.set(env.clock.now())
        outcomes.append(env.engine.process(e))
    paid = [o for o in outcomes if o.ai_paid_calls]
    assert paid and all(o.kind == "candle" and o.setups > 0 for o in paid)  # nie ohne Anlass
    assert env.engine.stats.ai_cost == sum(o.ai_cost for o in outcomes) == env.aki.ai_cost_summary().total_cost > 0
    assert env.engine.stats.ai_paid_calls == len(ai.provider.calls)


def test_without_ai_budget_nothing_is_paid_even_with_ai_review_configured():
    from trading.ai.budget import NO_BUDGET

    ai = review_env(budget=NO_BUDGET)
    env = Env(kibo=RuleBasedKibo(review=AIReview(ai.orch, D(50))))
    simulate(env.engine, events(days=10), env.clock)
    assert ai.provider.calls == [] and env.engine.stats.ai_cost == 0 and env.engine.stats.executed > 0


def test_regime_changes_are_events_and_the_only_trigger_for_a_regime_review():
    ai = review_env(text='{"confidence": 0.9, "note": "neue Phase"}')
    env = Env(strategy=Quiet(), regime_review=AIReview(ai.orch, D(50)))
    simulate(env.engine, events(days=40), env.clock)
    changes = env.log.of_type(EventType.REGIME_CHANGE)
    assert changes and env.engine.stats.regime_changes == len(changes)
    kinds = {r.trigger.value for r in ai.records}
    assert kinds == {"REGIME_CHANGE"} and 0 < len(ai.provider.calls) <= len(changes)  # Abkuehlzeit begrenzt weiter


class Quiet(BreakoutStrategy):
    def on_bar(self, view):
        return StrategyDecision()


# ------------------------------------------------------------------ Dashboard (DEMO) aus der laufenden Engine


def test_dashboard_shows_the_running_paper_engine():
    pytest.importorskip("fastapi", reason="FastAPI nicht installiert")
    from trading.dashboard.state import PaperSource

    env = Env()
    simulate(env.engine, events(days=20), env.clock)
    state = PaperSource(env.executor, env.aki, D(10000), T0, env.clock.now, log=env.log,
                        halt=env.executor.halt_status, strategy="breakout").state(env.clock.now())
    assert state["mode"] == "DEMO" and state["status"]["accounting_problem"] == ""
    assert state["metrics"]["trades"] == len(env.executor.closed_trades) and state["agents"]["KIBO"]["opinions"] > 0
