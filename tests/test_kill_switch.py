"""Globaler Not-Aus ohne Datenbank (AP-07): sperrt neue Positionen, laesst Schliessen zu, loest bei Daten- und
Protokollfehlern sowie Notbremsen selbst aus und laesst sich nur bewusst aufheben."""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest
from helpers import D, FUT, T0, Env, make_candle, minutes

from trading.audit.event_log import InMemoryEventLog
from trading.audit.events import EventType
from trading.domain.models import ExitReason, MarketType, OrderStatus
from trading.domain.reasons import RejectReason as R
from trading.execution.paper_executor import PaperExecutor
from trading.safety.kill_switch import (
    RELEASE_CONFIRMATION,
    HaltReason,
    InMemoryKillSwitch,
    KillSwitch,
    KillSwitchError,
)

FUTM = {"market_type": MarketType.FUTURES}


def halted_env(note="Test"):
    env = Env()
    assert env.executor.halt(note) is True
    return env


def reasons_logged(env, event_type):
    return [e.payload.get("reason") for e in env.log.of_type(event_type)]


# ------------------------------------------------------------------ Wirkung


def test_halt_blocks_new_positions_and_changes_nothing():
    env = halted_env()
    before = env.executor.export_state()
    r = env.order("x")
    assert r.status is OrderStatus.REJECTED and r.reasons == (R.TRADING_HALTED,)
    assert env.executor.export_state() == before
    rejected = env.log.of_type(EventType.ORDER_REJECTED)[-1].payload
    assert rejected["reasons"] == ["TRADING_HALTED"]


def test_halt_still_allows_every_kind_of_exit():
    env = Env()
    assert env.order("a", D(20)).status is OrderStatus.FILLED
    assert env.order("f", D(10), D(2), symbol="ETH-USD", market_type=MarketType.FUTURES,
                     snap_kw={"symbol": "ETH-USD", **FUT}).status is OrderStatus.FILLED
    spot = env.executor.open_positions[0].instrument
    env.executor.halt("Test")
    # Funding wird weiter gebucht, ein Ausstiegssignal schliesst, ein Stop-Loss schliesst.
    env.clock.advance(3600)
    assert env.fund("ETH-USD") != 0
    closed = env.executor.close_position(spot, env.snap(), ExitReason.SIGNAL, "exit-1")
    assert closed.closed
    stop = make_candle(env.clock.now(), D(100), D(100), D(98), D(99), symbol="ETH-USD", **FUTM)
    assert [t.exit_reason for t in env.feed(stop)] == [ExitReason.STOP_LOSS]
    assert env.executor.open_positions == ()


def test_manual_halt_is_logged_once_and_keeps_the_first_reason():
    env = Env()
    assert env.executor.halt("Wartung") is True
    assert env.executor.halt("noch einmal") is False
    status = env.executor.halt_status()
    assert status.halted and status.reason is HaltReason.MANUAL and status.note == "Wartung" and status.since == T0
    assert reasons_logged(env, EventType.HALT_ACTIVATED) == ["MANUAL"]


def test_one_shared_switch_stops_every_executor_using_it():
    a, b = Env(), Env()
    shared = InMemoryKillSwitch(a.clock)
    for env in (a, b):
        env.executor = PaperExecutor(D(10000), env.risk, env.validator, env.log, env.clock, kill_switch=shared)
    a.executor.halt("global")
    assert b.order("x").reasons == (R.TRADING_HALTED,)


class BrokenSwitch(KillSwitch):
    def status(self):
        raise OSError("nicht erreichbar")

    def trip(self, reason, note=""):
        raise OSError("nicht erreichbar")

    def release(self, operator, confirmation):
        raise OSError("nicht erreichbar")


def test_unreadable_switch_counts_as_halted():
    env = Env()
    env.executor = PaperExecutor(D(10000), env.risk, env.validator, env.log, env.clock, kill_switch=BrokenSwitch())
    assert env.executor.halt_status().halted
    assert env.order("x").reasons == (R.TRADING_HALTED,)


# ------------------------------------------------------------------ Freigabe nur bewusst


@pytest.mark.parametrize("operator, confirmation", [
    ("Eigner", ""), ("Eigner", "ja"), ("Eigner", RELEASE_CONFIRMATION.lower()), ("Eigner", RELEASE_CONFIRMATION + " "),
    ("", RELEASE_CONFIRMATION), ("   ", RELEASE_CONFIRMATION), (None, RELEASE_CONFIRMATION),
])
def test_release_needs_a_name_and_the_exact_confirmation(operator, confirmation):
    env = halted_env()
    with pytest.raises(KillSwitchError):
        env.executor.release_halt(operator, confirmation)
    assert env.executor.halt_status().halted
    assert env.order("x").reasons == (R.TRADING_HALTED,)
    assert env.log.of_type(EventType.HALT_RELEASED) == ()


def test_deliberate_release_resumes_trading_and_is_logged():
    env = halted_env()
    env.executor.release_halt("  Eigner  ", RELEASE_CONFIRMATION)
    assert not env.executor.halt_status().halted
    assert env.order("x").status is OrderStatus.FILLED
    released = env.log.of_type(EventType.HALT_RELEASED)[0].payload
    assert released["operator"] == "Eigner" and released["previous_reason"] == "MANUAL"


# ------------------------------------------------------------------ automatische Ausloeser: Datenfehler


def _bad_candle(**changes):
    return dataclasses.replace(make_candle(T0, D(100), D(101), D(99), D(100)), **changes)


@pytest.mark.parametrize("changes, reason", [
    ({"high": D("NaN")}, "NON_FINITE_VALUE"),
    ({"low": D(-1)}, "NON_POSITIVE_PRICE"),
    ({"high": D(98)}, "BAD_CANDLE"),
    ({"volume": None}, "MISSING_DATA"),
])
def test_formally_broken_candle_trips_the_halt(changes, reason):
    env = Env()
    env.clock.advance(120)
    assert env.executor.on_candle(_bad_candle(**changes)) == []
    status = env.executor.halt_status()
    assert status.reason is HaltReason.DATA_ERROR and reason in status.note
    assert env.order("x").reasons == (R.TRADING_HALTED,)


def test_candle_from_the_future_trips_the_halt():
    env = Env()
    env.executor.on_candle(make_candle(T0 + timedelta(hours=1), D(100), D(101), D(99), D(100)))
    assert env.executor.halt_status().reason is HaltReason.DATA_ERROR


def test_repeated_or_older_candles_do_not_trip_the_halt():
    """Wiederholungen sind nach Neustarts normal; sie werden verworfen, stoppen aber nicht den Handel."""
    env = Env()
    c1 = make_candle(T0, D(100), D(101), D(99), D(100))
    c2 = make_candle(T0 + minutes(1), D(100), D(101), D(99), D(100))
    env.feed(c1)
    env.feed(c2)
    assert env.executor.on_candle(c2) == [] and env.executor.on_candle(c1) == []
    assert not env.executor.halt_status().halted
    assert len(env.log.of_type(EventType.DATA_REJECTED)) == 2


def test_broken_funding_event_trips_the_halt():
    env = Env()
    env.fund(rate=D("NaN"))
    assert env.executor.halt_status().reason is HaltReason.DATA_ERROR


def test_corrupt_snapshot_trips_the_halt_but_market_conditions_do_not():
    env = Env()
    assert env.order("wide", snap_kw={"half_spread": D(5)}).reasons == (R.SPREAD_TOO_WIDE,)
    assert env.order("stale", snap_kw={"ts": T0 - timedelta(hours=1)}).reasons == (R.STALE_DATA,)
    assert not env.executor.halt_status().halted
    assert R.NAIVE_TIMESTAMP in env.order("naive", snap_kw={"ts": T0.replace(tzinfo=None)}).reasons
    assert env.executor.halt_status().reason is HaltReason.DATA_ERROR
    assert env.order("next").reasons == (R.TRADING_HALTED,)


# ------------------------------------------------------------------ automatische Ausloeser: Protokoll, Notbremsen


class FailingLog(InMemoryEventLog):
    fail = False

    def _persist(self, event):
        if self.fail:
            raise OSError("Platte voll")
        super()._persist(event)


def test_protocol_failure_on_any_operation_trips_the_halt():
    env = Env()
    log = FailingLog(env.clock)
    env.executor._log = log
    assert env.order("a").status is OrderStatus.FILLED
    log.fail = True
    with pytest.raises(OSError):  # Stop wird ausgeloest -> Protokolleintrag scheitert
        env.feed(make_candle(T0, D(100), D(100), D(98), D(99)))
    assert len(env.executor.open_positions) == 1  # alles zurueckgesetzt
    assert env.executor.halt_status().reason is HaltReason.PROTOCOL_ERROR
    log.fail = False
    assert env.order("x").reasons == (R.TRADING_HALTED,)


def test_drawdown_brake_trips_even_when_only_the_portfolio_is_queried():
    env = Env()
    env.executor.restore_state(dataclasses.replace(env.executor.export_state(), peak_equity=D(11200)))  # 10,7 % unter Hoch
    assert env.log.of_type(EventType.HALT_ACTIVATED) == ()
    env.executor.portfolio_state()  # reine Abfrage, keine Order
    assert env.executor.halt_status().reason is HaltReason.DRAWDOWN
    assert reasons_logged(env, EventType.HALT_ACTIVATED) == ["DRAWDOWN"]


def test_unknown_halt_reason_still_stops():
    switch = InMemoryKillSwitch(Env().clock)
    assert switch.trip("Unsinn", "x") is True  # type: ignore[arg-type]
    assert switch.status().halted and switch.status().reason is HaltReason.MANUAL
