"""Deterministische Backtesting-Engine.

Ablauf je Zeitpunkt T (immer in dieser Reihenfolge):

  1. Kerzen, die bei T SCHLIESSEN, werden verarbeitet  -> Stop-Loss, Take-Profit, Liquidation, Bewertung
  2. Funding-Abrechnungen mit Zeitpunkt T              -> Haltekosten der Futures-Positionen
  3. Punkt der Kapitalkurve                            -> alle offenen Positionen zum Schlusskurs bewertet
  4. Die Strategie entscheidet                         -> sie sieht NUR abgeschlossene Kerzen bis T
  5. Ausfuehrung zum EROEFFNUNGSKURS der Kerzen, die bei T beginnen (plus Spread, Slippage, Gebuehren)
     - erst Ausstiegssignale, dann neue Positionen. Gibt es fuer ein Instrument keine solche Kerze
       (Datenende), wird nicht gehandelt.

Warum Schritt 5 so: Die Entscheidung faellt aus dem Schlusskurs; gehandelt wird zum naechsten Kurs,
den die Strategie nicht kennen konnte. So kann kein Wissen aus der Zukunft in eine Entscheidung
fliessen, und Kursluecken zwischen Entscheidung und Ausfuehrung gehen zu Lasten des Ergebnisses.

Der Entscheidungsweg ist derselbe wie im spaeteren Betrieb: Strategie (als MOMO) -> RIN -> KIBO -> YUZU
-> SORA -> Executor mit unabhaengigem Risikomanager; AKI protokolliert jeden Schritt.
Solange KIBO und YUZU keine eigene Logik haben, laufen neutrale Platzhalter (im Ergebnis ausgewiesen).

Reproduzierbarkeit: keine Systemuhr, kein ungesetzter Zufall, feste Reihenfolge der Instrumente.
Gleicher Datensatz + gleiche Einstellungen + gleiche Strategie ergeben bitgleiche Ergebnisse
(`BacktestResult.result_hash`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from trading.agents.aki import AkiAgent
from trading.agents.contracts import MarketContext
from trading.agents.kibo import KiboAgent
from trading.agents.neutral import NeutralKibo, NeutralYuzu
from trading.agents.rin_rules import RinConfig, RuleBasedRin
from trading.agents.sora_rules import RuleBasedSora, SizingPolicy, SoraConfig
from trading.agents.yuzu import YuzuAgent
from trading.audit.event_log import EventLog, InMemoryEventLog
from trading.audit.events import EventType, to_jsonable
from trading.audit.performance import EquityPoint, PerformanceReport
from trading.backtest.data import InstrumentSeries, MarketDataset
from trading.backtest.strategy import MarketView, Strategy, StrategyMomo
from trading.clock import Clock, FixedClock
from trading.domain.instrument import InstrumentId
from trading.domain.models import Candle, ClosedTrade, FundingEvent, Position
from trading.domain.numeric import is_positive_decimal
from trading.domain.universe import TradingUniverse
from trading.execution.costs import CostModel
from trading.execution.paper_executor import PaperExecutor
from trading.execution.protocol import Executor
from trading.marketdata.synthetic import snapshot_from_price
from trading.pipeline import TradingPipeline
from trading.safety.data_validator import SnapshotValidator
from trading.safety.limits import DEFAULT_LIMITS, SafetyLimits
from trading.safety.risk_manager import RiskManager

LogFactory = Callable[[Clock], EventLog]
ExecutorFactory = Callable[[Clock, RiskManager, EventLog], Executor]


class BacktestError(RuntimeError):
    """Der Lauf ist nicht vertrauenswuerdig (z. B. abgestuerzte Strategie, verworfene Daten, defektes Protokoll)."""


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: Decimal = Decimal("10000")
    costs: CostModel = field(default_factory=CostModel)  # Gebuehren und Slippage
    limits: SafetyLimits = DEFAULT_LIMITS  # nur strenger als die Sicherheitslimits moeglich
    rin: RinConfig = field(default_factory=RinConfig)
    sora: SoraConfig = field(default_factory=SoraConfig)
    history_bars: int = 200  # so viele abgeschlossene Kerzen sieht die Strategie je Instrument
    label: str = ""

    def __post_init__(self) -> None:
        if not is_positive_decimal(self.initial_equity):
            raise ValueError("initial_equity muss eine positive Decimal-Zahl sein.")
        if not isinstance(self.history_bars, int) or self.history_bars < 1:
            raise ValueError("history_bars muss eine ganze Zahl >= 1 sein.")


def _digest(obj: Any) -> str:
    text = json.dumps(to_jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BacktestResult:
    strategy: str
    label: str
    start: datetime
    end: datetime
    dataset_fingerprint: str
    assumptions_fingerprint: str  # Startkapital, Kosten, Limits - Voraussetzung fuer Vergleichbarkeit
    config_fingerprint: str  # alle Einstellungen des Laufs (ohne die Bezeichnung)
    report: PerformanceReport
    trades: Tuple[ClosedTrade, ...]
    equity_curve: Tuple[EquityPoint, ...]
    open_positions: Tuple[Position, ...]
    setups_proposed: int
    setups_executed: int
    blocked_by_reason: Dict[str, int]
    exits_requested: int
    exits_executed: int
    placeholders: Tuple[str, ...]  # Agenten, die nur als neutraler Platzhalter liefen
    data_warnings: Tuple[str, ...]
    events: int
    log_head: str  # Hash des letzten Protokolleintrags
    result_hash: str  # Fingerabdruck aller Ergebnisse - gleich bei jedem identischen Lauf

    def summary(self) -> Dict[str, Any]:
        r, t, e = self.report, self.report.trades, self.report.equity
        return {
            "strategy": self.strategy, "label": self.label,
            "start": self.start, "end": self.end,
            "final_equity": r.final_equity, "net_result": r.net_result,
            "return_pct": e.total_return_pct, "realized_pnl": r.realized_pnl,
            "unrealized_pnl": r.unrealized_pnl, "open_positions": r.open_positions,
            "trades": t.trade_count, "win_rate": t.win_rate, "profit_factor": t.profit_factor,
            "avg_win": t.avg_win, "avg_loss": t.avg_loss, "expectancy": t.expectancy,
            "max_drawdown_pct": e.max_drawdown_pct, "sharpe": e.sharpe, "sortino": e.sortino,
            "total_fees": r.total_fees, "total_funding": r.total_funding,
            "setups_proposed": self.setups_proposed, "setups_executed": self.setups_executed,
            "blocked_by_reason": self.blocked_by_reason, "placeholders": self.placeholders,
            "dataset_fingerprint": self.dataset_fingerprint, "result_hash": self.result_hash,
        }

    def to_json(self) -> str:
        """Kanonische Textform des gesamten Ergebnisses (zum Ablegen und spaeteren Vergleichen)."""
        return json.dumps(to_jsonable(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass
class _Tick:
    closes: List[Tuple[InstrumentSeries, Candle]] = field(default_factory=list)
    fundings: List[FundingEvent] = field(default_factory=list)
    opens: Dict[InstrumentId, Tuple[InstrumentSeries, Candle]] = field(default_factory=dict)


class BacktestEngine:
    def __init__(
        self,
        dataset: MarketDataset,
        strategy: Strategy,
        config: Optional[BacktestConfig] = None,
        *,
        kibo: Optional[KiboAgent] = None,
        yuzu: Optional[YuzuAgent] = None,
        log_factory: Optional[LogFactory] = None,
        executor_factory: Optional[ExecutorFactory] = None,
    ) -> None:
        self._dataset = dataset
        self._strategy = strategy
        self._config = config or BacktestConfig()
        self._kibo, self._yuzu = kibo, yuzu
        self._log_factory = log_factory
        self._executor_factory = executor_factory

    def _timeline(self) -> Dict[datetime, _Tick]:
        ticks: Dict[datetime, _Tick] = {}
        for series in self._dataset.series:  # bereits nach Instrument sortiert -> feste Reihenfolge
            for candle in series.candles:
                ticks.setdefault(candle.open_time, _Tick()).opens[series.instrument] = (series, candle)
                ticks.setdefault(candle.close_time, _Tick()).closes.append((series, candle))
            for event in series.funding:
                ticks.setdefault(event.time, _Tick()).fundings.append(event)
        return ticks

    def run(self) -> BacktestResult:
        cfg, data = self._config, self._dataset
        clock = FixedClock(data.start)
        log = self._log_factory(clock) if self._log_factory else InMemoryEventLog(clock)
        risk = RiskManager(cfg.limits, cfg.costs)
        executor: Executor = (
            self._executor_factory(clock, risk, log)
            if self._executor_factory
            else PaperExecutor(cfg.initial_equity, risk, SnapshotValidator(cfg.limits, clock), log, clock)
        )
        aki = AkiAgent(log)
        history: Dict[InstrumentId, List[Candle]] = {i: [] for i in data.instruments}
        spread = {s.instrument: s.spread_bps for s in data.series}
        funding_rate: Dict[InstrumentId, Optional[Decimal]] = {
            s.instrument: s.initial_funding_rate for s in data.series
        }

        def view() -> MarketView:
            return MarketView(
                now=clock.now(),
                bars={i: tuple(bars[-cfg.history_bars:]) for i, bars in history.items() if bars},
                positions=executor.open_positions,
            )

        def context(instrument: InstrumentId, price: Decimal) -> MarketContext:
            snapshot = snapshot_from_price(
                instrument, clock.now(), price, spread[instrument], funding_rate[instrument]
            )
            return MarketContext(snapshot, tuple(history[instrument][-cfg.history_bars:]))

        def fill(tick: _Tick, instrument: InstrumentId) -> Optional[MarketContext]:
            """Ausfuehrungskurs = Eroeffnung der Kerze, die JETZT beginnt. Gibt es keine, wird nicht gehandelt."""
            opening = tick.opens.get(instrument)
            return context(instrument, opening[1].open) if opening is not None else None

        pipeline = TradingPipeline(
            StrategyMomo(self._strategy, clock, view),
            RuleBasedRin(TradingUniverse(frozenset(data.instruments)), clock, cfg.rin, cfg.costs),
            self._kibo or NeutralKibo(),
            self._yuzu or NeutralYuzu(),
            RuleBasedSora(SizingPolicy.from_limits(cfg.limits), cfg.costs, cfg.sora),
            aki,
            executor,
        )
        placeholders = tuple(name for name, agent in (("KIBO", self._kibo), ("YUZU", self._yuzu)) if agent is None)
        config_fp = _digest({k: v for k, v in to_jsonable(cfg).items() if k != "label"})
        assumptions_fp = _digest((cfg.initial_equity, cfg.costs, cfg.limits))
        log.append(EventType.BACKTEST_STARTED, {
            "strategy": self._strategy.name, "label": cfg.label, "dataset": data.fingerprint,
            "config": config_fp, "instruments": [i.key for i in data.instruments],
            "start": data.start, "end": data.end, "placeholders": placeholders, "mode": "paper",
        })

        proposed = executed = exits_requested = exits_executed = 0
        blocked: Dict[str, int] = {}
        ticks = self._timeline()
        for now in sorted(ticks):
            tick = ticks[now]
            clock.set(now)
            for _, candle in tick.closes:  # 1. Kerzenschluss
                aki.register_closed_trades(executor.on_candle(candle))
                history[candle.instrument].append(candle)
            for event in tick.fundings:  # 2. Funding
                executor.apply_funding(event)
                funding_rate[event.instrument] = event.rate
            if not tick.closes:
                continue
            aki.record_equity(now, executor.portfolio_state())  # 3. Kapitalkurve

            decision_data = [context(series.instrument, candle.close) for series, candle in tick.closes]
            setups = pipeline.propose(decision_data)  # 4. Entscheidung
            signals = pipeline.propose_exits(decision_data, executor.open_positions)
            proposed += len(setups)
            exits_requested += len(signals)

            for signal in signals:  # 5. Ausfuehrung: erst Ausstiege, dann Einstiege
                outcome = pipeline.process_exit(signal, fill(tick, signal.instrument))
                if outcome.closed and outcome.result is not None and outcome.result.trade is not None:
                    exits_executed += 1
                    aki.register_closed_trades([outcome.result.trade])
            for setup in setups:
                result = pipeline.process_setup(setup, fill(tick, setup.instrument))
                if result.executed:
                    executed += 1
                for reason in result.reasons:
                    blocked[reason.value] = blocked.get(reason.value, 0) + 1

        # --- Vertrauenswuerdigkeit des Laufs ---
        if log.of_type(EventType.SYSTEM_ERROR):
            raise BacktestError("Eine Strategie oder ein Agent ist waehrend des Laufs abgestuerzt (siehe Protokoll).")
        if log.of_type(EventType.DATA_REJECTED):
            raise BacktestError("Der Executor hat Marktdaten verworfen - der Datensatz ist nicht sauber.")
        open_positions = executor.open_positions
        report = aki.report(cfg.initial_equity, open_positions)  # wirft AccountingError, wenn es nicht aufgeht
        trades, curve = aki.trades, aki.equity_curve
        core = {
            "strategy": self._strategy.name, "dataset": data.fingerprint, "config": config_fp,
            "trades": trades, "curve": curve, "open": open_positions, "blocked": dict(sorted(blocked.items())),
            "counts": (proposed, executed, exits_requested, exits_executed),
        }
        result_hash = _digest(core)
        log.append(EventType.BACKTEST_FINISHED, {
            "strategy": self._strategy.name, "result_hash": result_hash, "final_equity": report.final_equity,
            "trades": len(trades), "open_positions": len(open_positions),
        })
        check = log.verify()
        if not check.ok:
            raise BacktestError("Das Ereignisprotokoll des Laufs ist beschaedigt: %s" % check.message)
        events = log.events()
        return BacktestResult(
            strategy=self._strategy.name, label=cfg.label, start=data.start, end=data.end,
            dataset_fingerprint=data.fingerprint, assumptions_fingerprint=assumptions_fp,
            config_fingerprint=config_fp, report=report, trades=trades, equity_curve=curve,
            open_positions=open_positions, setups_proposed=proposed, setups_executed=executed,
            blocked_by_reason=dict(sorted(blocked.items())), exits_requested=exits_requested,
            exits_executed=exits_executed, placeholders=placeholders, data_warnings=data.warnings,
            events=len(events), log_head=events[-1].hash, result_hash=result_hash,
        )
