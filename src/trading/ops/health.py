"""Gesundheit und Kennzahlen fuer den Betrieb (AP-25, D-027) - nur lesend, aendert nie etwas am System.

`HealthProbe.report()` fasst die Lage in OK / WARN / CRITICAL zusammen (das Schlechteste zaehlt):
  * Not-Aus aktiv oder nicht lesbar          -> CRITICAL (es werden keine Positionen eroeffnet)
  * Datenbank nicht erreichbar                -> CRITICAL
  * Marktdaten aelter als die Not-Aus-Frist   -> CRITICAL; nicht verbunden / laenger still / offene Luecken -> WARN
  * Fehler der Laufzeit-Engine                -> WARN
  * KI-Budget gesperrt                        -> WARN (die Regeln entscheiden weiter; ein Mensch muss pruefen)
`HealthProbe.metrics()` liefert dieselben Angaben als Zahlen; `render_prometheus` schreibt sie im Textformat von
Prometheus (fuer Ueberwachung und Alarme, AP-28). Fehlt ein Baustein, wird er weggelassen - nichts wird geraten.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from trading.ai.budget import BudgetLedger
from trading.clock import Clock
from trading.domain.models import PortfolioState
from trading.marketdata.stream import MarketStreamMonitor
from trading.runtime.engine import RuntimeEngine
from trading.safety.kill_switch import KillSwitch


class Level(str, Enum):
    OK = "OK"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


SEVERITY = {Level.OK: 0, Level.WARN: 1, Level.CRITICAL: 2}


@dataclass(frozen=True)
class Check:
    name: str
    level: Level
    detail: str


@dataclass(frozen=True)
class HealthReport:
    time: datetime
    level: Level
    checks: Tuple[Check, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.level is not Level.CRITICAL, "status": self.level.value, "time": self.time.isoformat(),
                "live_trading": "deaktiviert", "read_only": True,
                "checks": [{"name": c.name, "status": c.level.value, "detail": c.detail} for c in self.checks]}


Number = Union[int, float, Decimal]


@dataclass(frozen=True)
class Metric:
    name: str
    kind: str  # "gauge" oder "counter"
    help: str
    value: Number
    labels: Mapping[str, str] = field(default_factory=dict)


class HealthProbe:
    def __init__(
        self,
        clock: Clock,
        *,
        kill_switch: Optional[KillSwitch] = None,
        database: Optional[Callable[[], None]] = None,  # wirft, wenn die Datenbank nicht erreichbar ist
        monitor: Optional[MarketStreamMonitor] = None,
        engine: Optional[RuntimeEngine] = None,
        budget: Optional[BudgetLedger] = None,
        portfolio: Optional[Callable[[], PortfolioState]] = None,
    ) -> None:
        self._clock = clock
        self._kill_switch, self._database, self._monitor = kill_switch, database, monitor
        self._engine, self._budget, self._portfolio = engine, budget, portfolio

    # ------------------------------------------------------------------ Gesundheit

    def report(self) -> HealthReport:
        now = self._clock.now()
        checks: List[Check] = []
        if self._kill_switch is not None:
            checks.append(self._check_kill_switch())
        if self._database is not None:
            checks.append(self._check_database())
        if self._monitor is not None:
            checks.append(self._check_market(now))
        if self._engine is not None:
            errors = self._engine.stats.errors
            checks.append(Check("engine", Level.WARN if errors else Level.OK,
                                "%d Fehler bisher" % errors if errors else "%d Ereignisse ohne Fehler"
                                % self._engine.stats.events))
        if self._budget is not None:
            checks.append(self._check_budget())
        level = max((c.level for c in checks), key=SEVERITY.__getitem__, default=Level.OK)
        return HealthReport(now, level, tuple(checks))

    def _check_kill_switch(self) -> Check:
        assert self._kill_switch is not None
        try:
            status = self._kill_switch.status()
        except Exception as exc:  # noqa: BLE001 - nicht lesbar ist so schlimm wie ausgeloest
            return Check("kill_switch", Level.CRITICAL, "nicht lesbar (%s)" % type(exc).__name__)
        if status.halted:
            return Check("kill_switch", Level.CRITICAL, "Not-Aus aktiv seit %s: %s %s" % (
                status.since.isoformat() if status.since else "?", status.reason.value if status.reason else "",
                status.note))
        return Check("kill_switch", Level.OK, "nicht ausgeloest")

    def _check_database(self) -> Check:
        assert self._database is not None
        try:
            self._database()
        except Exception as exc:  # noqa: BLE001 - jeder Fehler heisst: nicht erreichbar
            return Check("database", Level.CRITICAL, "nicht erreichbar (%s)" % type(exc).__name__)
        return Check("database", Level.OK, "erreichbar")

    def _check_market(self, now: datetime) -> Check:
        assert self._monitor is not None
        status, policy = self._monitor.status, self._monitor.policy
        age = self._data_age(now)
        if age is None or age >= policy.halt_after_s:
            return Check("market_data", Level.CRITICAL, "keine Marktdaten seit %s" % (
                "Start" if age is None else "%.0f s" % age))
        problems = []
        if not status.connected:
            problems.append("nicht verbunden")
        if age >= policy.reconnect_after_s:
            problems.append("seit %.0f s still" % age)
        if status.gaps_given_up:
            problems.append("%d Luecken offen" % status.gaps_given_up)
        if problems:
            return Check("market_data", Level.WARN, ", ".join(problems))
        return Check("market_data", Level.OK, "letzte Daten vor %.0f s" % age)

    def _check_budget(self) -> Check:
        assert self._budget is not None
        try:
            status = self._budget.status()
        except Exception as exc:  # noqa: BLE001
            return Check("ai_budget", Level.WARN, "nicht lesbar (%s)" % type(exc).__name__)
        if status.blocked:
            return Check("ai_budget", Level.WARN, "KI-Budget gesperrt: %s" % (status.note or "Ursache pruefen"))
        return Check("ai_budget", Level.OK, "heute %s USD von %s USD" % (status.spent_today, status.budget.daily_usd))

    def _data_age(self, now: datetime) -> Optional[float]:
        assert self._monitor is not None
        last = self._monitor.status.last_data_at
        return None if last is None else max(0.0, (now - last).total_seconds())

    # ------------------------------------------------------------------ Kennzahlen

    def metrics(self) -> List[Metric]:
        now = self._clock.now()
        report = self.report()
        out = [Metric("trading_up", "gauge", "Dienst laeuft (immer 1, solange er antwortet)", 1),
               Metric("trading_health_status", "gauge", "0 OK, 1 WARN, 2 CRITICAL", SEVERITY[report.level])]
        if self._kill_switch is not None:
            halted = any(c.name == "kill_switch" and c.level is Level.CRITICAL for c in report.checks)
            out.append(Metric("trading_kill_switch_halted", "gauge", "Not-Aus aktiv (1) oder nicht (0)", int(halted)))
        if self._monitor is not None:
            s = self._monitor.status
            age = self._data_age(now)
            out += [Metric("trading_market_connected", "gauge", "WebSocket verbunden", int(s.connected)),
                    Metric("trading_market_connects_total", "counter", "Verbindungsaufbauten", s.connects),
                    Metric("trading_market_disconnects_total", "counter", "Verbindungsabbrueche", s.disconnects),
                    Metric("trading_market_candles_total", "counter", "ausgegebene Kerzen", s.candles_out),
                    Metric("trading_market_gaps_filled_total", "counter", "nachgeladene Luecken", s.gaps_filled),
                    Metric("trading_market_gaps_open_total", "counter", "nicht nachladbare Luecken", s.gaps_given_up),
                    Metric("trading_market_rejected_total", "counter", "verworfene Nachrichten", s.rejected)]
            if age is not None:
                out.append(Metric("trading_market_data_age_seconds", "gauge", "Alter der letzten Marktdaten", age))
        if self._engine is not None:
            e = self._engine.stats
            for name, value, text in (("events", e.events, "verarbeitete Ereignisse"),
                                      ("candles", e.candles, "angenommene Kerzen"),
                                      ("rejected_candles", e.rejected_candles, "verworfene Kerzen"),
                                      ("setups", e.setups, "Handelsvorschlaege"),
                                      ("executed", e.executed, "ausgefuehrte Eroeffnungen (Paper)"),
                                      ("blocked", e.blocked, "abgelehnte Vorschlaege"),
                                      ("closed_trades", e.closed_trades, "geschlossene Trades"),
                                      ("errors", e.errors, "Fehler"),
                                      ("ai_paid_calls", e.ai_paid_calls, "bezahlte KI-Aufrufe")):
                out.append(Metric("trading_engine_%s_total" % name, "counter", text, value))
            out.append(Metric("trading_engine_ai_cost_usd_total", "counter", "KI-Kosten in USD", e.ai_cost))
        if self._budget is not None:
            try:
                b = self._budget.status()
                out += [Metric("trading_ai_budget_blocked", "gauge", "KI-Budget gesperrt", int(b.blocked)),
                        Metric("trading_ai_spent_today_usd", "gauge", "KI-Kosten heute (USD)", b.spent_today),
                        Metric("trading_ai_spent_month_usd", "gauge", "KI-Kosten im Monat (USD)", b.spent_month)]
            except Exception:  # noqa: BLE001 - steht schon in der Gesundheit
                pass
        if self._portfolio is not None:
            p = self._portfolio()
            drawdown = (p.peak_equity - p.equity) / p.peak_equity if p.peak_equity > 0 else Decimal(0)
            out += [Metric("trading_equity_usd", "gauge", "Netto-Equity des Paper-Kontos", p.equity),
                    Metric("trading_open_positions", "gauge", "offene Positionen", p.open_count),
                    Metric("trading_drawdown_ratio", "gauge", "Rueckgang vom Hoechststand (0..1)", drawdown)]
        return out


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def render_prometheus(metrics: Sequence[Metric]) -> str:
    """Textformat 0.0.4 von Prometheus. Nicht endliche Werte werden weggelassen (statt NaN zu melden)."""
    lines: List[str] = []
    seen = set()
    for m in metrics:
        value = float(m.value)
        if not math.isfinite(value):
            continue
        if m.name not in seen:
            lines += ["# HELP %s %s" % (m.name, _escape(m.help)), "# TYPE %s %s" % (m.name, m.kind)]
            seen.add(m.name)
        labels = ",".join('%s="%s"' % (k, _escape(v)) for k, v in sorted(m.labels.items()))
        lines.append("%s%s %s" % (m.name, "{%s}" % labels if labels else "", repr(value) if value % 1 else int(value)))
    return "\n".join(lines) + "\n"


__all__ = ["Check", "HealthProbe", "HealthReport", "Level", "Metric", "render_prometheus"]
