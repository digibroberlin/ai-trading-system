"""Paper-Dienst (AP-27, D-029): standardmaessig aus, Freigabe vor jedem Zugriff, Aufwaermen ohne Handel, Kerzen aus der
Marktueberwachung durch alle sechs Agenten, Gesundheit, Waechter - mit simuliertem Server und Paper-Executor im
Speicher. Es wird nichts an eine Boerse gesendet."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from stream_helpers import BG, MIN, T0, Backfill, SimServer, at, push

from trading.agents.aki import AkiAgent
from trading.audit.event_log import InMemoryEventLog
from trading.clock import FixedClock
from trading.config import ConfigError
from trading.domain.instrument import Exchange, MarketType
from trading.execution.paper_executor import PaperExecutor
from trading.marketdata.bitget_ws import BitgetStream
from trading.marketdata.stream import MarketStreamMonitor
from trading.ops.health import Level
from trading.runtime.engine import APPROVAL_ENV, APPROVAL_TEXT, EngineConfig, EngineDisabledError
from trading.runtime.service import Parts, PaperService, ServiceConfig, Watchdog, main
from trading.safety.data_validator import SnapshotValidator
from trading.safety.kill_switch import InMemoryKillSwitch
from trading.safety.limits import DEFAULT_LIMITS
from trading.safety.risk_manager import RiskManager

D = Decimal
APPROVED = {APPROVAL_ENV: APPROVAL_TEXT}
CONFIG = ServiceConfig(Exchange.BITGET, MarketType.SPOT, ("BTC-USDT",), interval_s=60, warm_up_bars=30)


def parts(script, clock=None, backfill=None):
    clock = clock or FixedClock(T0 + 30 * MIN)
    log = InMemoryEventLog(clock)
    switch = InMemoryKillSwitch(clock)
    server = SimServer(clock, script)
    history = backfill or Backfill()
    monitor = MarketStreamMonitor(BitgetStream(), server, [BG], 60, clock, switch, history, sleep=clock.advance)
    executor = PaperExecutor(D(10000), RiskManager(DEFAULT_LIMITS), SnapshotValidator(DEFAULT_LIMITS, clock), log, clock)
    return Parts(executor, AkiAgent(log), log, switch, monitor, history, clock, database=lambda: None), server


# ------------------------------------------------------------------ Einstellungen


def test_configuration_from_the_environment_with_safe_defaults():
    c = ServiceConfig.from_env({})
    assert (c.exchange, c.market, c.symbols, c.interval_s, c.strategy, c.dashboard_host) == (
        Exchange.BITGET, MarketType.SPOT, ("BTC-USDT",), 3600, "breakout", "127.0.0.1")
    c = ServiceConfig.from_env({"TRADING_EXCHANGE": "coinbase", "TRADING_SYMBOLS": "BTC-USD, ETH-USD",
                                "TRADING_INTERVAL_S": "900", "TRADING_STRATEGY": "momentum"})
    assert [i.key for i in c.instruments] == ["coinbase:spot:BTC-USD", "coinbase:spot:ETH-USD"]
    for bad in ({"TRADING_SYMBOLS": "btcusdt"}, {"TRADING_SYMBOLS": ""}, {"TRADING_STRATEGY": "zauberei"},
                {"TRADING_INTERVAL_S": "86400"}, {"TRADING_EXCHANGE": "coinbase", "TRADING_MARKET": "futures",
                                                  "TRADING_SYMBOLS": "BTC-USD"},
                {"TRADING_INTERVAL_S": "eine Stunde"}, {"TRADING_EXCHANGE": "binance"}, {"WATCHDOG_S": "5"}):
        with pytest.raises(ConfigError):
            ServiceConfig.from_env(bad)


# ------------------------------------------------------------------ Freigabe


def test_without_approval_nothing_runs_and_nothing_is_contacted(capsys):
    calls = []

    def factory(*args):
        calls.append(args)
        raise AssertionError("darf nicht gebaut werden")

    assert main([], {"TRADING_ENGINE_ENABLED": "1"}, parts_factory=factory) == 0  # Freigabetext fehlt
    assert main([], dict(APPROVED), parts_factory=factory) == 0  # nicht eingeschaltet
    assert calls == [] and "startet nicht" in capsys.readouterr().out
    with pytest.raises(ConfigError):
        main([], {"TRADING_MODE": "live"}, parts_factory=factory)  # nie etwas anderes als PAPER
    p, server = parts([])
    service = PaperService(CONFIG, p, EngineConfig(enabled=True))
    with pytest.raises(EngineDisabledError):
        service.run(threading.Event(), {})
    assert server.connects == [] and p.history.calls == []  # weder Boerse noch REST angefragt


# ------------------------------------------------------------------ Betrieb (simuliert)


def test_service_warms_up_without_trading_then_processes_live_candles():
    start = T0 + 30 * MIN
    script = [at(start + MIN * i + timedelta(seconds=5), start + MIN * i) for i in range(6)]
    p, server = parts(script)
    service = PaperService(CONFIG, p, EngineConfig(enabled=True))
    stop = threading.Event()
    clock = p.clock

    def stopper():
        return clock.now() >= start + 5 * MIN + timedelta(seconds=6)  # direkt nach der letzten Nachricht

    original = p.monitor.step

    def step():
        original()
        if stopper():
            stop.set()

    p.monitor.step = step
    stats = service.run(stop, APPROVED)
    assert p.history.calls[0] == (T0, start)  # 30 Kerzen Historie bis zum Start
    assert stats.candles == 5 and stats.errors == 0 and stats.rejected_candles == 0
    assert len(service.engine._history[BG]) == 35  # 30 aufgewaermt + 5 live
    report = service.probe.report()
    assert report.level is Level.OK and {c.name for c in report.checks} == {
        "kill_switch", "database", "market_data", "engine"}


def test_failed_warm_up_only_delays_the_strategy():
    p, server = parts([], backfill=Backfill(fail=1))
    service = PaperService(CONFIG, p, EngineConfig(enabled=True))
    assert service.warm_up() == 0  # Fehler wird gemeldet, nicht verschluckt oder erfunden


def test_dashboard_source_shows_the_paper_account():
    p, server = parts([])
    service = PaperService(CONFIG, p, EngineConfig(enabled=True))
    pytest.importorskip("fastapi", reason="FastAPI nicht installiert")
    state = service.dashboard_source(T0).state(T0 + 31 * MIN)
    assert state["mode"] == "DEMO" and D(state["portfolio"]["equity"]) == D(10000)


# ------------------------------------------------------------------ Waechter


def test_watchdog_restarts_only_a_hanging_main_loop():
    now = [0.0]
    progress = [0]
    stalls = []
    dog = Watchdog(lambda: progress[0], 60, lambda: stalls.append(now[0]), monotonic=lambda: now[0])
    for _ in range(10):
        now[0] += 30
        progress[0] += 1  # Schleife laeuft
        dog.check()
    assert stalls == []
    now[0] += 59
    assert not dog.check()
    now[0] += 1
    assert dog.check() and stalls == [360.0]
    now[0] += 600
    dog.check()
    assert stalls == [360.0]  # nur einmal


def test_futures_service_receives_exchange_funding_before_candles():
    from test_marketdata_adapters import FakeBitget

    from trading.domain.instrument import InstrumentId
    from trading.marketdata.bitget import BitgetHistorySource
    from trading.marketdata.ratelimit import RateLimiter

    perp = InstrumentId(Exchange.BITGET, MarketType.FUTURES, "BTC-USDT")
    start = T0 + 30 * MIN
    clock = FixedClock(start)
    script = [(start + MIN * i + timedelta(seconds=5), push(start + MIN * i, start + MIN * i + timedelta(seconds=5),
                                                              inst_type="USDT-FUTURES")) for i in range(4)]
    api = FakeBitget(interval=60)
    history = BitgetHistorySource(api, clock, None, RateLimiter(50, monotonic=lambda: 0.0, sleep=lambda s: None),
                                  sleep=lambda s: None)
    log = InMemoryEventLog(clock)
    switch = InMemoryKillSwitch(clock)
    server = SimServer(clock, script)
    monitor = MarketStreamMonitor(BitgetStream(), server, [perp], 60, clock, switch, history, sleep=clock.advance)
    executor = PaperExecutor(D(10000), RiskManager(DEFAULT_LIMITS), SnapshotValidator(DEFAULT_LIMITS, clock), log, clock)
    p = Parts(executor, AkiAgent(log), log, switch, monitor, history, clock)
    config = ServiceConfig(Exchange.BITGET, MarketType.FUTURES, ("BTC-USDT",), interval_s=60, warm_up_bars=10)
    service = PaperService(config, p, EngineConfig(enabled=True))
    stop = threading.Event()
    original = monitor.step

    def step():
        original()
        if clock.now() >= start + 3 * MIN + timedelta(seconds=6):
            stop.set()

    monitor.step = step
    stats = service.run(stop, APPROVED)
    assert stats.candles == 3 and stats.fundings >= 3 and stats.errors == 0  # Abrechnungen des letzten Tages
    eight = int((T0.replace(hour=8) - datetime(1970, 1, 1, tzinfo=T0.tzinfo)).total_seconds())  # letzte vor dem Start
    assert service.engine._funding[perp] == api.rate(eight)
    assert not switch.halted  # Funding bekannt: kein Not-Aus
