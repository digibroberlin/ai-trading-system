"""Zustand des Dashboards (AP-23/24): EINE JSON-sichere Momentaufnahme aus Backtest- oder Paper-Daten. Nur lesend.

Quellen:
  * `BacktestSource` - Ergebnisse eines oder mehrerer Backtests (mit ihrem Ereignisprotokoll).
  * `PaperSource` - ein laufender Paper-Executor mit AKI (fuer die Laufzeit-Engine, AP-21).
Modus LIVE gibt es im Dashboard nur als Anzeige "deaktiviert" - eine Quelle fuer echtes Geld existiert nicht.

Alle Zahlen bleiben Decimal-genau und werden als Text ausgegeben (keine Rundungsfehler durch float im Browser).
Die Kapitalkurve wird fuer die Anzeige auf hoechstens `MAX_CURVE_POINTS` Punkte ausgeduennt (erster, letzter und der
tiefste Punkt bleiben immer erhalten).
"""

from __future__ import annotations

import hashlib
import json
import threading
from abc import ABC, abstractmethod
from collections import Counter
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from trading.agents.aki import AkiAgent
from trading.ai.budget import BudgetLedger
from trading.ai.orchestrator import AICallRecord
from trading.audit.costs import CostLedger, build_net_profit_report, summarize_ai_costs
from trading.audit.event_log import EventLog
from trading.audit.events import EventType, to_jsonable
from trading.audit.performance import AccountingError, EquityPoint, PerformanceReport, build_report
from trading.backtest.compare import comparison_table
from trading.backtest.engine import BacktestResult
from trading.domain.models import ClosedTrade, Position
from trading.execution.protocol import Executor
from trading.safety.kill_switch import HaltStatus

ZERO = Decimal(0)
MAX_CURVE_POINTS = 500
MAX_TRADES = 200
MAX_ERRORS = 50
ERROR_EVENTS = (EventType.SYSTEM_ERROR, EventType.DATA_REJECTED, EventType.DATA_GAP, EventType.DATA_ANOMALY,
                EventType.CLOSE_REJECTED, EventType.HALT_ACTIVATED, EventType.HALT_RELEASED)


class Mode(str, Enum):
    BACKTEST = "BACKTEST"
    DEMO = "DEMO"  # Paper-Handel mit simuliertem Geld (Laufzeit-Engine, AP-21)
    LIVE = "LIVE"  # echtes Geld: im Dashboard nur als "deaktiviert" sichtbar


def _obj(value: Any) -> Dict[str, Any]:
    out = to_jsonable(value)
    if not isinstance(out, dict):
        raise TypeError("Abschnitt muss ein Objekt sein.")
    return out


def thin_curve(points: Sequence[EquityPoint], limit: int = MAX_CURVE_POINTS) -> List[EquityPoint]:
    """Gleichmaessig ausduennen; erster, letzter und tiefster Punkt bleiben (der Drawdown wird nie geschoent)."""
    if limit < 3:
        raise ValueError("limit muss mindestens 3 sein.")
    if len(points) <= limit:
        return list(points)
    lowest = min(range(len(points)), key=lambda i: (points[i].equity, i))
    step = (len(points) - 1) / (limit - 2)
    keep = {round(i * step) for i in range(limit - 1)} | {0, len(points) - 1, lowest}
    return [points[i] for i in sorted(keep)]


def curve_rows(points: Sequence[EquityPoint], initial_equity: Decimal) -> List[Dict[str, str]]:
    """Kapitalkurve mit Drawdown (gegen den Hoechststand inkl. Startkapital), gemessen an ALLEN Punkten."""
    peak = initial_equity
    drawdown: Dict[datetime, Decimal] = {}
    for p in points:
        peak = max(peak, p.equity)
        drawdown[p.time] = (peak - p.equity) / peak * 100 if peak > 0 else ZERO
    return [{"time": p.time.isoformat(), "equity": str(p.equity), "drawdown_pct": str(drawdown[p.time])}
            for p in thin_curve(points)]


def _metrics(report: PerformanceReport) -> Dict[str, Any]:
    t, e = report.trades, report.equity
    return _obj({
        "initial_equity": report.initial_equity, "final_equity": report.final_equity,
        "net_result": report.net_result, "realized_pnl": report.realized_pnl, "unrealized_pnl": report.unrealized_pnl,
        "return_pct": e.total_return_pct, "max_drawdown_pct": e.max_drawdown_pct, "sharpe": e.sharpe,
        "sortino": e.sortino, "time_in_market_pct": e.time_in_market_pct, "trades": t.trade_count,
        "win_rate_pct": t.win_rate * 100, "profit_factor": t.profit_factor, "expectancy": t.expectancy,
        "avg_win": t.avg_win, "avg_loss": t.avg_loss, "largest_loss": t.largest_loss,
        "max_consecutive_losses": t.max_consecutive_losses, "total_fees": report.total_fees,
        "total_funding": report.total_funding, "exits_by_reason": t.exits_by_reason or {},
    })


def _agents(log: Optional[EventLog]) -> Dict[str, Dict[str, Optional[int]]]:
    """Je Agent: Einschaetzungen, Zustimmungen, Ablehnungen (aus dem Protokoll). MOMO schlaegt nur vor (keine
    Zustimmung/Ablehnung: None); SORA zaehlt Vorpruefungen und endgueltige Groessen."""
    out: Dict[str, Dict[str, Optional[int]]] = {}
    if log is None:
        return out

    def count(agent: str, approve: Optional[bool]) -> None:
        row = out.setdefault(agent, {"opinions": 0, "approved": 0, "rejected": 0})
        row["opinions"] = (row["opinions"] or 0) + 1
        key = "approved" if approve else "rejected"
        row[key] = (row[key] or 0) + 1

    for e in log.of_type(EventType.AGENT_OPINION):
        count(str(e.payload.get("agent")), bool(e.payload.get("approve")))
    for kind in (EventType.SIZING_PRECHECK, EventType.SIZING_PROPOSED):  # beide: {"agent": "SORA", "sizing": {...}}
        for e in log.of_type(kind):
            count("SORA", bool((e.payload.get("sizing") or {}).get("approve")))
    proposals = len(log.of_type(EventType.SETUP_PROPOSED))
    if proposals:
        out["MOMO"] = {"opinions": proposals, "approved": None, "rejected": None}
    return out


def _blocked(log: Optional[EventLog]) -> Dict[str, Any]:
    by_reason: Counter[str] = Counter()
    by_stage: Counter[str] = Counter()
    if log is not None:
        for e in log.of_type(EventType.SETUP_BLOCKED):
            by_stage[str(e.payload.get("stage", "?"))] += 1
            for reason in e.payload.get("reasons") or ():
                by_reason[str(reason)] += 1
    return {"by_reason": dict(by_reason.most_common()), "by_stage": dict(by_stage.most_common())}


def _errors(log: Optional[EventLog]) -> List[Dict[str, Any]]:
    if log is None:
        return []
    events = sorted((e for kind in ERROR_EVENTS for e in log.of_type(kind)), key=lambda e: e.seq)
    return [{"time": e.ts, "type": e.event_type, "detail": e.payload} for e in events[-MAX_ERRORS:]]


def _positions(positions: Sequence[Position]) -> List[Any]:
    return [to_jsonable({"instrument": p.instrument.key, "side": p.side, "quantity": p.quantity,
                         "entry_price": p.entry_price, "stop_loss": p.stop_loss, "take_profit": p.take_profit,
                         "leverage": p.leverage, "opened_at": p.opened_at, "risk_at_open": p.risk_at_open,
                         "funding_paid": p.funding_paid, "strategy": p.strategy}) for p in positions]


def _trades(trades: Sequence[ClosedTrade]) -> List[Any]:
    return [to_jsonable({"instrument": t.instrument.key, "side": t.side, "strategy": t.strategy,
                         "opened_at": t.opened_at, "closed_at": t.closed_at, "entry_price": t.entry_price,
                         "exit_price": t.exit_price, "quantity": t.quantity, "exit_reason": t.exit_reason,
                         "net_pnl": t.net_pnl, "fees": t.fees, "funding": t.funding, "r_multiple": t.r_multiple})
            for t in list(trades)[-MAX_TRADES:]]


def _status(halt: HaltStatus, **extra: Any) -> Dict[str, Any]:
    return _obj(dict({"trading_halted": halt.halted, "halt_reason": halt.reason, "halt_since": halt.since,
                             "halt_note": halt.note, "live_trading": "deaktiviert"}, **extra))


def _costs(trades: Sequence[ClosedTrade], ai_records: Sequence[AICallRecord], ledger: CostLedger, start: datetime,
           end: datetime, budget: Optional[BudgetLedger]) -> Dict[str, Any]:
    summary = summarize_ai_costs(ai_records)
    out: Dict[str, Any] = {"ai": to_jsonable(summary), "budget": None, "net": None}
    if end > start:
        report = build_net_profit_report(trades, ai_records, ledger, start, end)
        out["net"] = to_jsonable({k: getattr(report, k) for k in (
            "trades", "gross_pnl", "trading_fees", "funding", "trading_net_pnl", "ai_cost", "server_cost",
            "market_data_cost", "other_cost", "operating_cost", "net_after_all_costs")})
        out["net"]["attribution"] = to_jsonable(report.attribution)
    if budget is not None:
        status = budget.status()
        out["budget"] = to_jsonable({"day": status.day.isoformat(), "spent_today": status.spent_today,
                                     "spent_month": status.spent_month, "limits": status.budget,
                                     "remaining_today": status.remaining_today, "blocked": status.blocked,
                                     "note": status.note})
    return out


def fingerprint(state: Mapping[str, Any]) -> str:
    body = {k: v for k, v in state.items() if k not in ("version", "generated_at")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


class DashboardSource(ABC):
    mode: Mode

    @abstractmethod
    def build(self) -> Dict[str, Any]:
        """Alle Abschnitte ausser `mode`, `version`, `generated_at` (JSON-sicher)."""

    def state(self, now: datetime) -> Dict[str, Any]:
        body = dict(self.build())
        body["mode"] = self.mode.value
        body["version"] = fingerprint(body)
        body["generated_at"] = now.isoformat()
        return body


class BacktestSource(DashboardSource):
    """Ein oder mehrere Backtests auf DEMSELBEN Datensatz; angezeigt wird `selected` (Standard: der erste)."""

    mode = Mode.BACKTEST

    def __init__(self, results: Sequence[BacktestResult], logs: Optional[Mapping[str, EventLog]] = None,
                 costs: Optional[CostLedger] = None, selected: Optional[str] = None,
                 ai_records: Sequence[AICallRecord] = ()) -> None:
        if not results:
            raise ValueError("Mindestens ein Backtest-Ergebnis noetig.")
        self._results = list(results)
        self._logs = dict(logs or {})
        self._costs = costs or CostLedger()
        self._ai_records = list(ai_records)
        names = [r.strategy for r in self._results]
        if selected is not None and selected not in names:
            raise ValueError("Unbekannte Strategie %r." % selected)
        self._selected = selected or names[0]

    def build(self) -> Dict[str, Any]:
        r = next(x for x in self._results if x.strategy == self._selected)
        log = self._logs.get(r.strategy)
        rep = r.report
        last = r.equity_curve[-1] if r.equity_curve else None
        return {
            "status": _status(HaltStatus(False), strategy=r.strategy, label=r.label, start=r.start, end=r.end,
                              dataset=r.dataset_fingerprint[:12], result_hash=r.result_hash[:12],
                              placeholders=list(r.placeholders), data_warnings=list(r.data_warnings),
                              events=r.events),
            "portfolio": to_jsonable({"initial_equity": rep.initial_equity, "equity": rep.final_equity,
                                      "balance": last.balance if last else rep.final_equity,
                                      "unrealized_pnl": rep.unrealized_pnl, "net_result": rep.net_result,
                                      "return_pct": rep.equity.total_return_pct, "open_positions": rep.open_positions,
                                      "gross_exposure": last.gross_exposure if last else ZERO,
                                      "open_risk": last.open_risk if last else ZERO}),
            "positions": _positions(r.open_positions),
            "trades": _trades(r.trades),
            "equity": curve_rows(r.equity_curve, rep.initial_equity),
            "metrics": _metrics(rep),
            "agents": _agents(log),
            "blocked": _blocked(log) if log is not None else {"by_reason": dict(r.blocked_by_reason), "by_stage": {}},
            "strategies": to_jsonable(comparison_table(self._results)) if len(self._results) > 1 else [],
            "costs": _costs(r.trades, self._ai_records, self._costs, r.start, r.end, None),
            "errors": _errors(log),
        }


class PaperSource(DashboardSource):
    """Laufender Paper-Executor (DEMO) mit AKI. Liest nur - aendert nie etwas am Executor."""

    mode = Mode.DEMO

    def __init__(self, executor: Executor, aki: AkiAgent, initial_equity: Decimal, started_at: datetime,
                 clock: Callable[[], datetime], log: Optional[EventLog] = None, costs: Optional[CostLedger] = None,
                 budget: Optional[BudgetLedger] = None, halt: Optional[Callable[[], HaltStatus]] = None,
                 strategy: str = "") -> None:
        self._executor, self._aki = executor, aki
        self._initial, self._started, self._clock = initial_equity, started_at, clock
        self._log, self._costs, self._budget, self._halt = log, costs or CostLedger(), budget, halt
        self._strategy = strategy
        self._lock = threading.Lock()

    def build(self) -> Dict[str, Any]:
        with self._lock:
            now = self._clock()
            portfolio = self._executor.portfolio_state()
            positions = self._executor.open_positions
            trades = self._executor.closed_trades
            # Aktueller Kontostand als letzter Punkt (nur fuer die Anzeige; im Protokoll/AKI wird nichts geschrieben)
            curve = [p for p in self._aki.equity_curve if p.time < now] + [EquityPoint.from_state(now, portfolio)]
            try:
                report: Optional[PerformanceReport] = build_report(self._initial, trades, curve, positions)
                problem = ""
            except AccountingError as exc:  # sichtbar machen statt falsche Zahlen zu zeigen
                report, problem = None, str(exc)
            halt = self._halt() if self._halt is not None else HaltStatus(False)
            return {
                "status": _status(halt, strategy=self._strategy, start=self._started, end=now,
                                  valuation_complete=portfolio.valuation_complete, accounting_problem=problem),
                "portfolio": to_jsonable({"initial_equity": self._initial, "equity": portfolio.equity,
                                          "balance": portfolio.balance, "unrealized_pnl": portfolio.unrealized_pnl,
                                          "net_result": portfolio.equity - self._initial,
                                          "return_pct": (portfolio.equity / self._initial - 1) * 100,
                                          "open_positions": portfolio.open_count,
                                          "gross_exposure": portfolio.gross_exposure, "open_risk": portfolio.open_risk}),
                "positions": _positions(positions),
                "trades": _trades(trades),
                "equity": curve_rows(curve, self._initial),
                "metrics": _metrics(report) if report is not None else {},
                "agents": _agents(self._log),
                "blocked": _blocked(self._log),
                "strategies": [],
                "costs": _costs(trades, self._aki.ai_records, self._costs, self._started, now, self._budget),
                "errors": _errors(self._log),
            }


SECTIONS: Tuple[str, ...] = ("status", "portfolio", "positions", "trades", "equity", "metrics", "agents", "blocked",
                             "strategies", "costs", "errors")

__all__ = ["BacktestSource", "DashboardSource", "MAX_CURVE_POINTS", "Mode", "PaperSource", "SECTIONS", "curve_rows",
           "fingerprint", "thin_curve"]
