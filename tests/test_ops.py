"""Betrieb (AP-25, D-027): strukturiertes Logging ohne Geheimnisse, Gesundheit (OK/WARN/CRITICAL) und Kennzahlen."""

from __future__ import annotations

import io
import json
import logging
from datetime import timedelta
from decimal import Decimal

import pytest
from helpers import T0

from trading.ai.budget import AIBudget, InMemoryBudgetLedger
from trading.clock import FixedClock
from trading.ops.health import HealthProbe, Level, Metric, render_prometheus
from trading.ops.logs import JsonFormatter, configure_logging, redact
from trading.safety.kill_switch import HaltReason, InMemoryKillSwitch

D = Decimal


# ------------------------------------------------------------------ Logging


@pytest.mark.parametrize("text, hidden", [
    ("postgresql://trading_app:fakepass123@db:5432/trading", "fakepass123"),
    ("api_key=fakekey123456", "fakekey123456"),
    ('{"secret": "xyz987"}', "xyz987"),
    ("BITGET_API_PASSPHRASE: hunter2", "hunter2"),
    ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
    ("Schluessel " + "sk-" + "ant-api03-AbCdEfGhIjKlMn im Text", "AbCdEfGhIjKlMn"),  # zusammengesetzt: kein Fund
    ("password = 'p@ss'", "p@ss"),
])
def test_secrets_never_reach_the_log(text, hidden):
    assert hidden not in redact(text) and "***" in redact(text)


def test_harmless_text_is_left_alone():
    for text in ("input_tokens=120 output_tokens=30", "Kerze 2026-09-01 12:00 geschlossen", "max_tokens=400"):
        assert redact(text) == text


@pytest.fixture
def restore_logging():
    """configure_logging veraendert den Logger "trading" global - danach den alten Zustand wiederherstellen."""
    logger = logging.getLogger("trading")
    saved = (list(logger.handlers), logger.level, logger.propagate)
    yield
    logger.handlers[:] = saved[0]
    logger.setLevel(saved[1])
    logger.propagate = saved[2]


def test_json_lines_with_extras_and_redacted_errors(restore_logging):
    stream = io.StringIO()
    configure_logging("INFO", stream)
    configure_logging("INFO", stream)  # zweimal: trotzdem nur eine Zeile je Meldung
    log = logging.getLogger("trading.test")
    log.info("Verbindung zu %s", "postgresql://u:pw123@h/db", extra={"component": "test", "amount": D("1.5")})
    try:
        raise RuntimeError("token=abc123 ging verloren")
    except RuntimeError:
        log.exception("Fehler")
    log.debug("nicht sichtbar")
    lines = [json.loads(x) for x in stream.getvalue().splitlines()]
    assert len(lines) == 2
    assert lines[0]["message"] == "Verbindung zu postgresql://u:***@h/db" and lines[0]["level"] == "INFO"
    assert lines[0]["component"] == "test" and lines[0]["amount"] == "1.5" and lines[0]["time"].endswith("+00:00")
    assert "abc123" not in lines[1]["error"] and "RuntimeError" in lines[1]["error"]


def test_formatter_handles_odd_extra_values():
    record = logging.LogRecord("trading.x", logging.WARNING, "f", 1, "m", None, None)
    record.values = [float("nan"), 1, {"k": object()}]
    data = json.loads(JsonFormatter().format(record))
    assert data["values"][0] == "nan" and data["values"][1] == 1 and isinstance(data["values"][2]["k"], str)


# ------------------------------------------------------------------ Gesundheit


class FakeMonitorStatus:
    def __init__(self, connected=True, last=None, gaps=0):
        self.connected, self.last_data_at, self.gaps_given_up = connected, last, gaps
        self.connects, self.disconnects, self.candles_out, self.gaps_filled, self.rejected = 1, 0, 10, 0, 0


class FakeMonitor:
    def __init__(self, **kw):
        from trading.marketdata.stream import MonitorPolicy

        self.status = FakeMonitorStatus(**kw)
        self.policy = MonitorPolicy()


def probe(**kw):
    clock = kw.pop("clock", FixedClock(T0))
    return HealthProbe(clock, **kw), clock


def test_everything_fine_is_ok():
    switch = InMemoryKillSwitch(FixedClock(T0))
    p, clock = probe(kill_switch=switch, database=lambda: None, monitor=FakeMonitor(last=T0 - timedelta(seconds=3)),
                     budget=InMemoryBudgetLedger(AIBudget(D("0.05"), D(1), D(10)), FixedClock(T0)))
    report = p.report()
    assert report.level is Level.OK and [c.name for c in report.checks] == [
        "kill_switch", "database", "market_data", "ai_budget"]
    assert report.as_dict()["ok"] is True and report.as_dict()["live_trading"] == "deaktiviert"


def test_kill_switch_database_or_stale_data_are_critical():
    switch = InMemoryKillSwitch(FixedClock(T0))
    switch.trip(HaltReason.DATA_ERROR, "Marktdaten veraltet")
    assert probe(kill_switch=switch)[0].report().level is Level.CRITICAL

    class Broken:
        def status(self):
            raise OSError("weg")

    assert probe(kill_switch=Broken())[0].report().level is Level.CRITICAL

    def no_db():
        raise ConnectionError("db")

    report = probe(database=no_db)[0].report()
    assert report.level is Level.CRITICAL and "nicht erreichbar" in report.checks[0].detail
    assert probe(monitor=FakeMonitor(last=T0 - timedelta(seconds=121)))[0].report().level is Level.CRITICAL
    assert probe(monitor=FakeMonitor(last=None))[0].report().level is Level.CRITICAL  # nie Daten gesehen


def test_warnings_for_silence_disconnects_gaps_engine_errors_and_a_blocked_ai_budget():
    for monitor in (FakeMonitor(last=T0 - timedelta(seconds=50)), FakeMonitor(connected=False, last=T0),
                    FakeMonitor(last=T0, gaps=2)):
        assert probe(monitor=monitor)[0].report().level is Level.WARN
    ledger = InMemoryBudgetLedger(AIBudget(D("0.05"), D(1), D(10)), FixedClock(T0))
    ledger.block("Anbieter meldet keinen Verbrauch")
    report = probe(budget=ledger)[0].report()
    assert report.level is Level.WARN and "keinen Verbrauch" in report.checks[0].detail

    class Engine:
        class stats:  # noqa: N801 - Nachbildung von EngineStats
            errors, events = 2, 10

    assert probe(engine=Engine())[0].report().level is Level.WARN
    switch = InMemoryKillSwitch(FixedClock(T0))
    switch.trip(HaltReason.MANUAL, "Test")
    assert probe(kill_switch=switch, budget=ledger)[0].report().level is Level.CRITICAL  # das Schlechteste zaehlt


# ------------------------------------------------------------------ Kennzahlen


def test_prometheus_text_format():
    text = render_prometheus([Metric("a_total", "counter", "Zaehler", 3), Metric("b", "gauge", 'Wert "x"\nneu', D("1.5"),
                                                                                 {"exchange": 'bit"get'}),
                              Metric("c", "gauge", "kaputt", float("nan"))])
    assert text == ('# HELP a_total Zaehler\n# TYPE a_total counter\na_total 3\n'
                    '# HELP b Wert \\"x\\"\\nneu\n# TYPE b gauge\nb{exchange="bit\\"get"} 1.5\n')


def test_metrics_cover_market_engine_budget_and_portfolio():
    from trading.domain.models import PortfolioState

    switch = InMemoryKillSwitch(FixedClock(T0))
    state = PortfolioState(D(9000), D(0), D(9000), D(9000), D(10000), D(10000), D(0), 1, frozenset(), D(0), D(0), True)
    p, clock = probe(kill_switch=switch, monitor=FakeMonitor(last=T0 - timedelta(seconds=4)),
                     budget=InMemoryBudgetLedger(AIBudget(D("0.05"), D(1), D(10)), FixedClock(T0)),
                     portfolio=lambda: state)
    values = {m.name: m.value for m in p.metrics()}
    assert values["trading_up"] == 1 and values["trading_health_status"] == 0
    assert values["trading_kill_switch_halted"] == 0 and values["trading_market_data_age_seconds"] == 4
    assert values["trading_drawdown_ratio"] == D("0.1") and values["trading_open_positions"] == 1
    assert values["trading_ai_budget_blocked"] == 0 and values["trading_market_candles_total"] == 10


# ------------------------------------------------------------------ Meldungen der Bausteine


def test_monitor_issues_and_budget_blocks_are_logged(caplog):
    from trading.ai.activation import DEFAULT_ACTIVATION, ActivationPolicy
    from trading.ai.orchestrator import AIOrchestrator
    from trading.ai.pricing import DEFAULT_PRICES
    from trading.ai.provider import ProviderRegistry

    clock = FixedClock(T0)
    ledger = InMemoryBudgetLedger(AIBudget(D("0.05"), D(1), D(10)), clock)
    orch = AIOrchestrator(ProviderRegistry(), DEFAULT_PRICES, ledger, ActivationPolicy(DEFAULT_ACTIVATION, clock), clock)
    with caplog.at_level(logging.ERROR, logger="trading"):
        orch._block("Test-Sperre")
    assert any(r.name == "trading.ai.orchestrator" and "Test-Sperre" in r.getMessage() and r.alert
               for r in caplog.records)

    from trading.domain.instrument import Exchange, InstrumentId, MarketType
    from trading.marketdata.bitget_ws import BitgetStream
    from trading.marketdata.stream import ConnectionLost, MarketStreamMonitor

    class Unreachable:
        def connect(self, url):
            raise ConnectionLost("weg")

    caplog.clear()
    mon = MarketStreamMonitor(BitgetStream(), Unreachable(), [InstrumentId(Exchange.BITGET, MarketType.SPOT, "BTC-USDT")],
                              60, clock, InMemoryKillSwitch(clock), sleep=clock.advance)
    with caplog.at_level(logging.WARNING, logger="trading"):
        for _ in range(200):
            mon.step()
    texts = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "trading.marketdata.stream"]
    assert (logging.WARNING, "Verbindung nicht moeglich (weg) - neuer Versuch in 1 s") in texts
    assert any(level == logging.CRITICAL and text.startswith("Not-Aus") for level, text in texts)
