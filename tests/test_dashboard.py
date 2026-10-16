"""Dashboard (AP-23/24): Momentaufnahme aus Backtest und Paper-Konto, nur lesende API, WebSocket, Sicherheits-Header."""

from __future__ import annotations

import ast
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

# Ohne FastAPI (z. B. Python 3.9, Kern-Pruefung) sichtbar ueberspringen.
pytest.importorskip("fastapi", reason="FastAPI nicht installiert - Dashboard-Tests uebersprungen")
from bt_helpers import synthetic  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from helpers import T0, inst  # noqa: E402

from trading.agents.aki import AkiAgent  # noqa: E402
from trading.ai.budget import AIBudget, InMemoryBudgetLedger  # noqa: E402
from trading.audit.event_log import InMemoryEventLog  # noqa: E402
from trading.audit.performance import EquityPoint  # noqa: E402
from trading.clock import FixedClock  # noqa: E402
from trading.dashboard.__main__ import backtest_source  # noqa: E402
from trading.dashboard.api import SECURITY_HEADERS, create_app  # noqa: E402
from trading.dashboard.state import (  # noqa: E402
    SECTIONS,
    BacktestSource,
    DashboardSource,
    Mode,
    PaperSource,
    curve_rows,
    thin_curve,
)
from trading.domain.models import MarketType, OrderIntent, Side, TradeSetup  # noqa: E402
from trading.execution.paper_executor import PaperExecutor  # noqa: E402
from trading.marketdata.synthetic import snapshot_from_price  # noqa: E402
from trading.safety.data_validator import SnapshotValidator  # noqa: E402
from trading.safety.limits import DEFAULT_LIMITS  # noqa: E402
from trading.safety.risk_manager import RiskManager  # noqa: E402

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
HOUR = timedelta(hours=1)


@pytest.fixture(scope="module")
def source():
    return backtest_source(synthetic(days=12, seed=2))


def point(i, equity):
    return EquityPoint(T0 + i * HOUR, D(equity), D(0), D(equity), D(0), D(0), 0)


# ------------------------------------------------------------------ Kapitalkurve fuer die Anzeige


def test_thinned_curve_keeps_start_end_and_the_lowest_point():
    pts = [point(i, 10000 + (i % 7) * 10) for i in range(2000)]
    pts[1234] = point(1234, 5000)  # tiefster Punkt darf nie verschwinden
    thin = thin_curve(pts, 100)
    assert len(thin) <= 101 and thin[0] == pts[0] and thin[-1] == pts[-1] and pts[1234] in thin
    assert thin_curve(pts[:50], 100) == pts[:50]
    with pytest.raises(ValueError):
        thin_curve(pts, 2)


def test_drawdown_is_measured_on_all_points_not_only_the_displayed_ones():
    rows = curve_rows([point(0, 10000), point(1, 12000), point(2, 9000)], D(10000))
    assert [D(r["drawdown_pct"]) for r in rows] == [0, 0, 25]  # gegen den Hoechststand 12 000


# ------------------------------------------------------------------ Momentaufnahme aus Backtests


def test_backtest_snapshot_has_every_section_and_is_pure_json(source):
    state = source.state(T0)
    assert set(SECTIONS) <= set(state) and state["mode"] == "BACKTEST"
    json.dumps(state)  # reines JSON (Decimal als Text)
    assert state["status"]["live_trading"] == "deaktiviert" and not state["status"]["trading_halted"]
    assert state["status"]["placeholders"] == []  # KIBO/YUZU regelbasiert
    assert {"RIN", "KIBO", "YUZU", "MOMO"} <= set(state["agents"])
    assert state["agents"]["MOMO"]["approved"] is None  # MOMO schlaegt nur vor
    sora = state["agents"]["SORA"]  # Vorpruefung + endgueltige Groesse je ausgefuehrtem Trade
    assert sora["approved"] >= 2 * state["metrics"]["trades"] and sora["opinions"] == sora["approved"] + sora["rejected"]
    assert len(state["strategies"]) == 8 and state["costs"]["net"]["trades"] == state["metrics"]["trades"]
    assert state["equity"] and D(state["portfolio"]["equity"]) == D(state["metrics"]["final_equity"])


def test_version_changes_only_with_the_content(source):
    a, b = source.state(T0), source.state(T0 + HOUR)
    assert a["version"] == b["version"] and a["generated_at"] != b["generated_at"]
    other = BacktestSource(source._results, source._logs, selected="breakout-20-10").state(T0)
    assert other["version"] != a["version"] and other["status"]["strategy"] == "breakout-20-10"


def test_invalid_backtest_sources_are_rejected(source):
    with pytest.raises(ValueError):
        BacktestSource([])
    with pytest.raises(ValueError):
        BacktestSource(source._results, selected="gibt-es-nicht")


# ------------------------------------------------------------------ Momentaufnahme aus dem Paper-Konto (DEMO)


def paper():
    clock = FixedClock(T0)
    log = InMemoryEventLog(clock)
    executor = PaperExecutor(D(10000), RiskManager(DEFAULT_LIMITS), SnapshotValidator(DEFAULT_LIMITS, clock), log, clock)
    aki = AkiAgent(log)
    budget = InMemoryBudgetLedger(AIBudget(D("0.05"), D("1"), D("10")), clock)
    return clock, log, executor, aki, budget


def test_paper_snapshot_shows_positions_halt_and_ai_budget():
    clock, log, executor, aki, budget = paper()
    btc = inst("BTC-USD", MarketType.SPOT)
    snap = snapshot_from_price(btc, T0, D(100), D(2))
    setup = TradeSetup("s1", btc, Side.LONG, D(100), D(95), D(115), "intraday", "test", T0)
    result = executor.submit(OrderIntent("s1", setup, D(1), D(1)), snap)
    assert result.status.value == "filled"
    src = PaperSource(executor, aki, D(10000), T0, clock.now, log=log, budget=budget,
                      halt=executor.halt_status, strategy="test")
    state = src.state(T0)
    assert state["mode"] == "DEMO" and state["portfolio"]["open_positions"] == 1
    assert state["positions"][0]["instrument"] == btc.key and state["costs"]["budget"]["blocked"] is False
    executor.halt("Test")
    halted = src.state(T0)
    assert halted["status"]["trading_halted"] and halted["status"]["halt_reason"] == "MANUAL"
    assert halted["version"] != state["version"]
    assert any(e["type"] == "HALT_ACTIVATED" for e in halted["errors"])


# ------------------------------------------------------------------ API


@pytest.fixture(scope="module")
def client(source):
    return TestClient(create_app(source, FixedClock(T0), push_interval_s=0.05))


def test_api_serves_state_sections_and_health(client):
    assert client.get("/api/health").json() == {"ok": True, "mode": "BACKTEST", "live_trading": "deaktiviert",
                                                "read_only": True}
    full = client.get("/api/state").json()
    assert set(SECTIONS) <= set(full)
    part = client.get("/api/state/portfolio").json()
    assert part["portfolio"] == full["portfolio"] and part["version"] == full["version"]
    assert client.get("/api/state/geheim").status_code == 404


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_nothing_can_be_changed_through_the_api(client, method):
    for path in ("/api/state", "/api/state/portfolio", "/api/health", "/"):
        assert getattr(client, method)(path).status_code == 405


def test_web_page_and_files_with_security_headers(client):
    page = client.get("/")
    assert page.status_code == 200 and "KI-Trading-System" in page.text and "/static/app.js" in page.text
    for key, value in SECURITY_HEADERS.items():
        assert page.headers[key] == value
    assert "default-src 'self'" in page.headers["Content-Security-Policy"]
    assert client.get("/static/app.js").headers["content-type"].startswith("text/javascript")
    for bad in ("/static/index.html", "/static/..%2Fapi.py", "/static/%2e%2e%2fstate.py", "/docs", "/openapi.json"):
        assert client.get(bad).status_code == 404
    for name in ("index.html", "app.js", "style.css"):  # keine fremden Server (CDN, Schriften, Analyse)
        text = (ROOT / "src/trading/dashboard/static" / name).read_text(encoding="utf-8")
        assert "http://" not in text.replace("http://www.w3.org/2000/svg", "") and "https://" not in text


def test_websocket_sends_the_state_and_again_only_after_a_change():
    clock, log, executor, aki, budget = paper()
    src = PaperSource(executor, aki, D(10000), T0, clock.now, log=log, halt=executor.halt_status)
    with TestClient(create_app(src, clock, push_interval_s=0.05)) as c, c.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["mode"] == "DEMO" and not first["status"]["trading_halted"]
        import time

        time.sleep(0.3)  # mehrere Sendetakte ohne Aenderung: es darf nichts nachkommen
        executor.halt("Test")
        second = ws.receive_json()  # naechste Nachricht kommt erst mit geaendertem Inhalt
        assert second["status"]["trading_halted"] and second["version"] != first["version"]


def test_live_mode_cannot_be_served_at_all(source):
    class Live(DashboardSource):
        mode = Mode.LIVE

        def build(self):
            return {}

    with pytest.raises(ValueError, match="LIVE"):
        create_app(Live())
    with pytest.raises(ValueError):
        create_app(source, push_interval_s=0)


# ------------------------------------------------------------------ Aufbau


def test_dashboard_code_never_calls_anything_that_changes_the_system():
    forbidden = {"submit", "submit_sized", "close_position", "on_candle", "apply_funding", "halt", "release",
                 "release_halt", "trip", "reserve", "settle", "block", "record_equity", "register_closed_trades",
                 "add", "record_ai_call", "record_operating_cost"}
    for name in ("state.py", "api.py"):
        tree = ast.parse((ROOT / "src/trading/dashboard" / name).read_text(encoding="utf-8"))
        calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert not calls & forbidden, (name, calls & forbidden)


def test_main_program_refuses_any_mode_other_than_paper(monkeypatch):
    from trading.config import ConfigError
    from trading.dashboard.__main__ import main

    monkeypatch.setenv("TRADING_MODE", "live")
    with pytest.raises(ConfigError):
        main(["--days", "1"])
