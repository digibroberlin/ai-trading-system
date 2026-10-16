"""Paper-Dienst fuer den Betrieb (AP-27, D-029): Marktueberwachung -> Laufzeit-Engine (alle sechs Agenten) ->
Paper-Executor in PostgreSQL, dazu das nur lesende Dashboard mit /api/health und /metrics.

STANDARDMAESSIG AUS. `python -m trading.runtime` startet den Handel nur, wenn ALLES zutrifft:
  * Modus PAPER, keine Boersen-Schluessel (`load_settings`),
  * `TRADING_ENGINE_ENABLED=1` UND die Freigabe des Eigners `TRADING_ENGINE_APPROVAL="PAPER-HANDEL FREIGEGEBEN"`,
  * `DATABASE_URL` (eingeschraenkte Rolle) und ein angelegtes Konto.
Fehlt die Freigabe, beendet sich der Dienst sofort mit Code 0 und einer Meldung - Docker startet ihn dann NICHT neu
(`restart: on-failure`). Es gibt keinen Weg zu echtem Geld: Der Executor ist der Paper-Executor.

Betriebssicherheit:
  * SIGTERM/SIGINT: sauber beenden (laufende Kerze zu Ende, Verbindungen schliessen).
  * Waechter: Macht die Hauptschleife `watchdog_s` lang keinen Schritt mehr (haengt), beendet sich der Prozess mit
    Code 3 - Docker startet ihn neu; die Engine waermt sich danach aus der Historie auf, ohne zu handeln.
  * Not-Aus, Datenbank, Marktdaten, Engine und KI-Budget erscheinen unter /api/health (bei CRITICAL HTTP 503).
KI-Zweitmeinungen sind hier nicht angeschlossen (Budget-Freigabe B-004 offen): KIBO und YUZU entscheiden regelbasiert.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple

from trading.agents.aki import AkiAgent
from trading.ai.budget import BudgetLedger
from trading.audit.costs import CostLedger, operating_costs_from_env
from trading.audit.event_log import EventLog
from trading.backtest.data import HistoricalDataSource
from trading.clock import Clock, SystemClock
from trading.config import ConfigError, load_settings
from trading.domain.instrument import Exchange, InstrumentId, MarketType, is_instrument
from trading.domain.universe import TradingUniverse
from trading.execution.protocol import Executor
from trading.marketdata.stream import MarketStreamMonitor, bucket
from trading.ops.health import HealthProbe
from trading.runtime.engine import EngineConfig, EngineDisabledError, EngineStats, RuntimeEngine, check_start_allowed
from trading.safety.kill_switch import KillSwitch
from trading.strategies import STRATEGY_TYPES, build_strategy

LOG = logging.getLogger("trading.runtime.service")
STALL_EXIT_CODE = 3
STREAM_INTERVALS = {Exchange.BITGET: frozenset({60, 300, 900, 1800, 3600, 14400}),
                    Exchange.COINBASE: frozenset({60, 300, 900, 3600, 21600, 86400})}


@dataclass(frozen=True)
class ServiceConfig:
    exchange: Exchange
    market: MarketType
    symbols: Tuple[str, ...]
    interval_s: int = 3600
    strategy: str = "breakout"
    account: str = "main"
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000
    watchdog_s: float = 900.0
    warm_up_bars: int = 200

    def __post_init__(self) -> None:
        if not self.symbols or any(not is_instrument(i) for i in self.instruments):
            raise ConfigError("TRADING_SYMBOLS: mindestens ein Symbol in der Form BASIS-QUOTE (z. B. BTC-USDT).")
        if self.exchange is Exchange.COINBASE and self.market is not MarketType.SPOT:
            raise ConfigError("Coinbase: nur Spot.")
        if self.interval_s not in STREAM_INTERVALS.get(self.exchange, frozenset()):
            raise ConfigError("TRADING_INTERVAL_S %r gibt es bei %s nicht." % (self.interval_s, self.exchange.value))
        if self.strategy not in STRATEGY_TYPES:
            raise ConfigError("TRADING_STRATEGY %r unbekannt (%s)." % (self.strategy, ", ".join(sorted(STRATEGY_TYPES))))
        if not 60 <= self.watchdog_s <= 3600 or not 0 <= self.warm_up_bars <= 1000:
            raise ConfigError("Waechter 60..3600 s, Aufwaermen 0..1000 Kerzen.")

    @property
    def instruments(self) -> Tuple[InstrumentId, ...]:
        return tuple(InstrumentId(self.exchange, self.market, s) for s in self.symbols)

    @staticmethod
    def from_env(env: Mapping[str, str]) -> "ServiceConfig":
        try:
            return ServiceConfig(
                exchange=Exchange(env.get("TRADING_EXCHANGE", "bitget")),
                market=MarketType(env.get("TRADING_MARKET", "spot")),
                symbols=tuple(s.strip() for s in env.get("TRADING_SYMBOLS", "BTC-USDT").split(",") if s.strip()),
                interval_s=int(env.get("TRADING_INTERVAL_S", "3600")),
                strategy=env.get("TRADING_STRATEGY", "breakout"),
                account=env.get("TRADING_ACCOUNT", "main"),
                dashboard_host=env.get("DASHBOARD_HOST", "127.0.0.1"),
                dashboard_port=int(env.get("DASHBOARD_PORT", "8000")),
                watchdog_s=float(env.get("WATCHDOG_S", "900")),
            )
        except ValueError as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError("Ungueltige Einstellung: %s" % exc) from None


@dataclass
class Parts:
    """Alles, was der Dienst braucht - im Betrieb aus PostgreSQL und dem Netz, in Tests aus Attrappen."""

    executor: Executor
    aki: AkiAgent
    log: EventLog
    kill_switch: KillSwitch
    monitor: MarketStreamMonitor
    history: HistoricalDataSource
    clock: Clock
    budget: Optional[BudgetLedger] = None
    database: Optional[Callable[[], None]] = None
    closers: Sequence[Callable[[], None]] = ()


class Watchdog:
    """Erkennt eine haengende Hauptschleife: aendert sich `progress()` `timeout_s` lang nicht, wird `on_stall` gerufen."""

    def __init__(self, progress: Callable[[], int], timeout_s: float, on_stall: Callable[[], None],
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._progress, self._timeout, self._on_stall, self._monotonic = progress, timeout_s, on_stall, monotonic
        self._last_value = progress()
        self._last_change = monotonic()
        self.stalled = False

    def check(self) -> bool:
        value, now = self._progress(), self._monotonic()
        if value != self._last_value:
            self._last_value, self._last_change = value, now
        elif not self.stalled and now - self._last_change >= self._timeout:
            self.stalled = True
            LOG.critical("Hauptschleife haengt seit %.0f s - Neustart", now - self._last_change,
                         extra={"component": "watchdog", "alert": True})
            self._on_stall()
        return self.stalled

    def run(self, stop: threading.Event, every_s: float = 5.0) -> None:
        while not stop.wait(every_s):
            self.check()


class PaperService:
    def __init__(self, config: ServiceConfig, parts: Parts, engine_config: EngineConfig) -> None:
        self.config, self.parts, self.engine_config = config, parts, engine_config
        self.engine = RuntimeEngine(build_strategy(config.strategy), parts.executor, parts.aki, parts.clock,
                                    TradingUniverse(frozenset(config.instruments)), config=engine_config)
        self.probe = HealthProbe(parts.clock, kill_switch=parts.kill_switch, database=parts.database,
                                 monitor=parts.monitor, engine=self.engine, budget=parts.budget,
                                 portfolio=parts.executor.portfolio_state)

    def warm_up(self) -> int:
        """Historie fuer Strategie und Marktphase - ohne zu handeln. Fehler hier verhindern nur das Aufwaermen."""
        if self.config.warm_up_bars == 0:
            return 0
        end = bucket(self.parts.clock.now(), self.config.interval_s)
        start = end - timedelta(seconds=self.config.interval_s * self.config.warm_up_bars)
        taken = 0
        for instrument in self.config.instruments:
            try:
                series = self.parts.history.load(instrument, self.config.interval_s, start, end)
            except Exception as exc:  # noqa: BLE001 - ohne Historie wartet die Strategie einfach laenger
                LOG.warning("Aufwaermen fuer %s nicht moeglich: %s", instrument.key, exc,
                            extra={"component": "service"})
                continue
            taken += self.engine.warm_up(series.candles)
        return taken

    def run(self, stop: threading.Event, environ: Optional[Mapping[str, str]] = None) -> EngineStats:
        check_start_allowed(self.engine_config, environ)  # VOR jedem Netz- oder Datenbankzugriff des Laufs
        LOG.info("Paper-Dienst startet: %s, %d s, Strategie %s", ",".join(i.key for i in self.config.instruments),
                 self.config.interval_s, self.config.strategy, extra={"component": "service"})
        LOG.info("Aufgewaermt mit %d Kerzen", self.warm_up(), extra={"component": "service"})
        stats = self.engine.run(self.parts.monitor.candles(stop=stop.is_set), environ)
        LOG.info("Paper-Dienst beendet: %d Ereignisse, %d Fehler", stats.events, stats.errors,
                 extra={"component": "service"})
        return stats

    def dashboard_source(self, started_at: datetime, costs: Optional[CostLedger] = None) -> Any:
        """Paper-Konto fuer das Dashboard - Netto nach allen Kosten inkl. Server/Marktdaten (`costs`, AP-28)."""
        from trading.dashboard.state import PaperSource

        portfolio = self.parts.executor.portfolio_state()
        return PaperSource(self.parts.executor, self.parts.aki, portfolio.equity, started_at, self.parts.clock.now,
                           log=self.parts.log, costs=costs, budget=self.parts.budget,
                           halt=self.parts.kill_switch.status, strategy=self.config.strategy)


def production_parts(config: ServiceConfig, env: Mapping[str, str], clock: Clock,
                     transport: Any = None, http: Any = None) -> Parts:
    """Bausteine fuer den Betrieb: PostgreSQL (eingeschraenkte Rolle) und oeffentliche Marktdaten."""
    from trading.agents.aki import AkiAgent
    from trading.ai.budget import budget_from_env
    from trading.marketdata.bitget import BitgetHistorySource
    from trading.marketdata.bitget_ws import BitgetStream
    from trading.marketdata.coinbase import CoinbaseHistorySource
    from trading.marketdata.coinbase_ws import CoinbaseStream
    from trading.marketdata.public_http import PublicHttpGet
    from trading.persistence.ai_budget import PostgresBudgetLedger
    from trading.persistence.kill_switch import PostgresKillSwitch
    from trading.persistence.pg_event_log import PostgresEventLog
    from trading.persistence.pg_executor import PostgresPaperExecutor
    from trading.safety.limits import DEFAULT_LIMITS
    from trading.safety.risk_manager import RiskManager

    url = env.get("DATABASE_URL", "")
    if not url:
        raise ConfigError("DATABASE_URL fehlt (eingeschraenkte Rolle trading_app).")
    if transport is None:
        from trading.marketdata.public_ws import PublicWebSocket  # braucht wsproto (Python >= 3.10)

        transport = PublicWebSocket()
    getter = http or PublicHttpGet()
    if config.exchange is Exchange.BITGET:
        history: HistoricalDataSource = BitgetHistorySource(getter, clock)
        protocol: Any = BitgetStream()
    else:
        history, protocol = CoinbaseHistorySource(getter, clock), CoinbaseStream()
    log = PostgresEventLog(url, clock)
    executor = PostgresPaperExecutor(url, config.account, RiskManager(DEFAULT_LIMITS), clock)
    kill_switch = PostgresKillSwitch(url, clock)
    budget = PostgresBudgetLedger(url, budget_from_env(env), clock)
    monitor = MarketStreamMonitor(protocol, transport, config.instruments, config.interval_s, clock, kill_switch,
                                  history)
    closers: List[Callable[[], None]] = [transport.close, executor.close, log.close]
    for extra in (kill_switch, budget):
        close = getattr(extra, "close", None)
        if callable(close):
            closers.append(close)

    def database() -> None:
        kill_switch.status()  # liest aus der Datenbank - wirft, wenn sie nicht erreichbar ist

    return Parts(executor, AkiAgent(log), log, kill_switch, monitor, history, clock, budget, database, closers)


def main(argv: Optional[Sequence[str]] = None, environ: Optional[Mapping[str, str]] = None,
         parts_factory: Callable[..., Parts] = production_parts, exit_process: Callable[[int], None] = os._exit) -> int:
    env = os.environ if environ is None else environ
    load_settings(env)  # nur PAPER, keine Boersen-Schluessel
    engine_config = EngineConfig(enabled=env.get("TRADING_ENGINE_ENABLED") == "1")
    try:
        check_start_allowed(engine_config, env)
    except EngineDisabledError as exc:
        LOG.warning("Paper-Dienst startet nicht: %s", exc, extra={"component": "service"})
        print("Paper-Dienst startet nicht: %s" % exc)
        return 0  # kein Fehler: Docker startet ihn nicht neu
    config = ServiceConfig.from_env(env)
    operating_costs_from_env(env, SystemClock().now())  # ungueltige Kostenangaben: lieber kein Start
    clock = SystemClock()
    parts = parts_factory(config, env, clock)
    service = PaperService(config, parts, engine_config)
    stop = threading.Event()

    def request_stop(*_: Any) -> None:
        LOG.info("Beenden angefordert", extra={"component": "service"})
        stop.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
    watchdog = Watchdog(lambda: parts.monitor.steps, config.watchdog_s, lambda: exit_process(STALL_EXIT_CODE))
    threading.Thread(target=watchdog.run, args=(stop,), name="watchdog", daemon=True).start()
    server = None
    try:
        from trading.dashboard.server import DashboardServer

        started = clock.now()
        source = service.dashboard_source(started, operating_costs_from_env(env, started))
        server = DashboardServer(source, service.probe, config.dashboard_host, config.dashboard_port)
        server.start()
        service.run(stop, env)
    finally:
        stop.set()
        if server is not None:
            server.stop()
        for close in parts.closers:
            try:
                close()
            except Exception:  # noqa: BLE001 - Aufraeumen darf das Beenden nicht verhindern
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
