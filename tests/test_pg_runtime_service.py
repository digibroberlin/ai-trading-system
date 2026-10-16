"""Paper-Dienst mit echter Datenbank (AP-27): Verdrahtung der PostgreSQL-Bausteine, gemeinsamer Not-Aus von
Marktueberwachung und Executor, Gesundheit. Marktdaten aus dem simulierten Server - kein Netz."""

from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import requires_db
from stream_helpers import MIN, T0, SimServer, at, push

psycopg = pytest.importorskip("psycopg", reason="psycopg nicht installiert - Datenbank-Tests uebersprungen")

from trading.clock import FixedClock  # noqa: E402
from trading.domain.instrument import Exchange, MarketType  # noqa: E402
from trading.marketdata.public_http import FetchError  # noqa: E402
from trading.ops.health import Level  # noqa: E402
from trading.runtime.engine import APPROVAL_ENV, APPROVAL_TEXT, EngineConfig  # noqa: E402
from trading.runtime.service import PaperService, ServiceConfig, production_parts  # noqa: E402

CONFIG = ServiceConfig(Exchange.BITGET, MarketType.SPOT, ("BTC-USDT",), interval_s=60, warm_up_bars=10)


def no_rest(url, params):
    raise FetchError("kein Netz im Test", None, False)


@requires_db
def test_production_parts_run_against_postgres_and_share_the_kill_switch(pg):
    pg.create_account("main", Decimal(10000))
    clock = FixedClock(T0)
    script = [at(T0 + MIN * i + timedelta(seconds=5), T0 + MIN * i) for i in range(4)]
    script.append((T0 + 4 * MIN + timedelta(seconds=5), push(T0 + 30 * MIN, T0 + 4 * MIN)))  # Kerze aus der Zukunft
    server = SimServer(clock, script)
    env = {"DATABASE_URL": pg.app_url, APPROVAL_ENV: APPROVAL_TEXT}
    parts = production_parts(CONFIG, env, clock, transport=server, http=no_rest)
    parts.monitor._sleep = clock.advance
    try:
        service = PaperService(CONFIG, parts, EngineConfig(enabled=True))
        stop = threading.Event()
        original = parts.monitor.step

        def step():
            original()
            if clock.now() >= T0 + 4 * MIN + timedelta(seconds=10):
                stop.set()

        parts.monitor.step = step
        stats = service.run(stop, env)
        assert stats.candles == 4 and stats.errors == 0
        assert parts.executor.halt_status().halted  # Not-Aus des Monitors = Not-Aus des Executors (eine Tabelle)
        report = service.probe.report()
        assert report.level is Level.CRITICAL
        assert {c.name: c.level for c in report.checks}["database"] is Level.OK
    finally:
        for close in parts.closers:
            close()


@requires_db
def test_production_parts_need_a_database_url():
    from trading.config import ConfigError

    with pytest.raises(ConfigError, match="DATABASE_URL"):
        production_parts(CONFIG, {}, FixedClock(T0), transport=SimServer(FixedClock(T0), []), http=no_rest)
