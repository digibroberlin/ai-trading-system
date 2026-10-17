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

Der Entscheidungsweg ist derselbe wie im spaeteren Betrieb: Strategie (als MOMO) -> RIN -> SORA-Vorpruefung -> KIBO
-> YUZU -> SORA -> Executor mit unabhaengigem Risikomanager; AKI protokolliert jeden Schritt.
KIBO und YUZU arbeiten standardmaessig regelbasiert (AP-17); neutrale Platzhalter nur, wenn ausdruecklich uebergeben
(im Ergebnis ausgewiesen).

Reproduzierbarkeit: keine Systemuhr, kein ungesetzter Zufall, feste Reihenfolge der Instrumente.
Gleicher Datensatz + gleiche Einstellungen + gleiche Strategie ergeben bitgleiche Ergebnisse
(`BacktestResult.result_hash`).

Vorlauf (AP-20, `BacktestConfig.trade_from`): Kerzen VOR `trade_from` fuellen nur die Historie (Indikatoren, Marktphase,
zuletzt bekannter Funding-Satz) - keine Entscheidung, kein Punkt der Kapitalkurve, keine Position. Das sind Daten aus der
Vergangenheit des Zeitraums, also kein Blick in die Zukunft; ohne Vorlauf waeren die ersten 50-200 Kerzen jedes Fensters
"blind". Kennzahlen und Kurve beginnen bei `trade_from`.
Ausgewertet wird zusaetzlich je Setup, an welcher Stufe es endete (`setup_log`, `blocked_by_stage`), wann der Not-Aus
griff (`halted_at`) und - mit `measure=True` - die Rechenzeit je Agent (`timings`, nicht deterministisch, nie im Hash).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from trading.agents.aki import AkiAgent
from trading.agents.contracts import MarketContext
from trading.agents.kibo import KiboAgent
from trading.agents.kibo_rules import RuleBasedKibo
from trading.agents.momo import MomoAgent
from trading.agents.rin import RinAgent
from trading.agents.neutral import NeutralKibo, NeutralYuzu
from trading.agents.rin_rules import RinConfig, RuleBasedRin
from trading.agents.sora import SoraAgent
from trading.agents.sora_rules import RuleBasedSora, SizingPolicy, SoraConfig
from trading.agents.yuzu import YuzuAgent
from trading.agents.yuzu_rules import RuleBasedYuzu
from trading.audit.event_log import EventLog, InMemoryEventLog
from trading.audit.events import EventType, to_jsonable
from trading.audit.performance import EquityPoint, PerformanceReport
from trading.backtest.data import InstrumentSeries, MarketDataset
from trading.backtest.instrumentation import Stopwatch, TimedKibo, TimedMomo, TimedRin, TimedSora, TimedYuzu
from trading.backtest.strategy import MarketView, Strategy, StrategyMomo
from trading.clock import Clock, FixedClock
from trading.domain.exchange_rules import ExchangeRules
from trading.domain.instrument import InstrumentId
from trading.domain.models import Candle, ClosedTrade, ExitReason, FundingEvent, Position
from trading.domain.numeric import is_positive_decimal
from trading.domain.universe import TradingUniverse
from trading.execution.costs import CostModel
from trading.execution.paper_executor import PaperExecutor
from trading.execution.protocol import Executor
from trading.marketdata.candles import is_aware
from trading.marketdata.synthetic import snapshot_from_price
from trading.pipeline import TradingPipeline
from trading.safety.data_validator import SnapshotValidator
from trading.safety.kill_switch import RELEASE_CONFIRMATION
from trading.safety.limits import DEFAULT_LIMITS, SafetyLimits
from trading.safety.risk_manager import RiskManager

LogFactory = Callable[[Clock], EventLog]
MONTHLY_REVIEW = "monatlich"
SILENT_STOP = "BREMSE_OHNE_NOT_AUS"
BRAKES = frozenset({"DRAWDOWN_LIMIT", "DAILY_LOSS_LIMIT"})
REVIEW_OPERATOR = "Backtest-Simulation: monatliche Pruefung des Eigners"
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
    rules: Optional[ExchangeRules] = None  # Boersenregeln je Instrument (AP-13); None = nur eigene Limits
    label: str = ""
    trade_from: Optional[datetime] = None  # Vorlauf: davor nur Historie, keine Entscheidung (AP-20); None = ab Beginn
    # Am Ende offene Positionen zum letzten Schlusskurs MIT allen Ausstiegskosten schliessen (Grund MANUAL) - sonst werden
    # sie nur bewertet (ohne Gebuehr, Spread, Slippage), was das Ergebnis schoent (AP-20).
    close_at_end: bool = False
    # Simulierte Pruefung durch den Eigner (AP-20): Nach einem Not-Aus wird er am Ersten des Folgemonats mit dem
    # vorgesehenen Ablauf freigegeben (Name + Bestaetigung, gemessen wird ab dann neu - mit DENSELBEN Grenzen).
    # Ebenso ein "stiller Stillstand": Orders werden wegen Tagesverlust-/Drawdown-Bremse abgelehnt, ohne dass der
    # Not-Aus ausloest (Konto knapp unter der Grenze, jede neue Order wuerde sie reissen) - im Betrieb ohne Alarm.
    # "" = streng: keine Freigabe bis zum Ende. Jedes Ereignis steht in `halts` (Grund BREMSE_OHNE_NOT_AUS bzw. Not-Aus).
    halt_review: str = ""

    def __post_init__(self) -> None:
        if not is_positive_decimal(self.initial_equity):
            raise ValueError("initial_equity muss eine positive Decimal-Zahl sein.")
        if not isinstance(self.history_bars, int) or self.history_bars < 1:
            raise ValueError("history_bars muss eine ganze Zahl >= 1 sein.")
        if self.trade_from is not None and not is_aware(self.trade_from):
            raise ValueError("trade_from braucht eine Zeitzone.")
        if self.halt_review not in ("", MONTHLY_REVIEW):
            raise ValueError("halt_review: '' (streng) oder %r." % MONTHLY_REVIEW)


def _digest(obj: Any) -> str:
    text = json.dumps(to_jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SetupRecord:
    """Ein Vorschlag von MOMO und wo er endete: "FILLED" oder die blockierende Stufe (RIN, SORA, KIBO, YUZU, EXECUTOR,
    PIPELINE) mit Gruenden. Zeit = Entscheidungszeitpunkt (Kerzenschluss)."""

    setup_id: str
    time: datetime
    instrument: str
    side: str
    stage: str
    reasons: Tuple[str, ...]


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
    blocked_by_stage: Dict[str, int] = field(default_factory=dict)  # je SETUP die Stufe, an der es endete
    setup_log: Tuple[SetupRecord, ...] = ()
    halted_at: Optional[datetime] = None  # erster Not-Aus im Lauf
    halt_reason: str = ""
    halts: Tuple[Tuple[datetime, str, Optional[datetime]], ...] = ()  # (Zeitpunkt, Grund, simulierte Freigabe)
    timings: Dict[str, Dict[str, float]] = field(default_factory=dict, compare=False)  # nur mit measure=True

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
        """Kanonische Textform des gesamten Ergebnisses (zum Ablegen und spaeteren Vergleichen) - ohne die nicht
        deterministischen Rechenzeiten."""
        body = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "timings"}
        return json.dumps(to_jsonable(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def halted(executor: Executor) -> bool:
    status = getattr(executor, "halt_status", None)
    return bool(status is not None and status().halted)


def next_month(moment: datetime) -> datetime:
    """Erster Tag des Folgemonats, 00:00 UTC - Zeitpunkt der simulierten Pruefung durch den Eigner."""
    utc = moment.astimezone(timezone.utc)
    year, month = (utc.year + 1, 1) if utc.month == 12 else (utc.year, utc.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


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
        measure: bool = False,
    ) -> None:
        self._dataset = dataset
        self._strategy = strategy
        self._config = config or BacktestConfig()
        self._kibo, self._yuzu = kibo, yuzu
        self._log_factory = log_factory
        self._executor_factory = executor_factory
        self._measure = measure

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
        trade_from = cfg.trade_from or data.start
        if not data.start <= trade_from < data.end:
            raise BacktestError("trade_from liegt nicht im Datensatz (%s bis %s)." % (data.start, data.end))
        clock = FixedClock(data.start)
        log = self._log_factory(clock) if self._log_factory else InMemoryEventLog(clock)
        risk = RiskManager(cfg.limits, cfg.costs, cfg.rules)
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

        kibo: KiboAgent = self._kibo or RuleBasedKibo()
        yuzu: YuzuAgent = self._yuzu or RuleBasedYuzu()
        placeholders = tuple(name for name, neutral in (("KIBO", isinstance(kibo, NeutralKibo)),
                                                       ("YUZU", isinstance(yuzu, NeutralYuzu))) if neutral)
        momo: MomoAgent = StrategyMomo(self._strategy, clock, view, cfg.rules)
        rin: RinAgent = RuleBasedRin(TradingUniverse(frozenset(data.instruments)), clock, cfg.rin, cfg.costs)
        sora: SoraAgent = RuleBasedSora(SizingPolicy.from_limits(cfg.limits), cfg.costs, cfg.sora, rules=cfg.rules)
        watch = Stopwatch()
        if self._measure:  # nur Zeitmessung um dieselben Agenten - Entscheidungen bleiben identisch
            momo, rin, sora = TimedMomo(momo, watch), TimedRin(rin, watch), TimedSora(sora, watch)
            kibo, yuzu = TimedKibo(kibo, watch), TimedYuzu(yuzu, watch)
        pipeline = TradingPipeline(momo, rin, kibo, yuzu, sora, aki, executor)
        config_fp = _digest({k: v for k, v in to_jsonable(cfg).items() if k != "label"})
        assumptions_fp = _digest((cfg.initial_equity, cfg.costs, cfg.limits) + ((cfg.rules,) if cfg.rules else ()))
        log.append(EventType.BACKTEST_STARTED, {
            "strategy": self._strategy.name, "label": cfg.label, "dataset": data.fingerprint,
            "config": config_fp, "instruments": [i.key for i in data.instruments],
            "start": data.start, "end": data.end, "placeholders": placeholders, "mode": "paper",
            "trade_from": trade_from,
        })

        proposed = executed = exits_requested = exits_executed = 0
        blocked: Dict[str, int] = {}
        by_stage: Dict[str, int] = {}
        setup_log: List[SetupRecord] = []
        release_at: Optional[datetime] = None
        releases: List[datetime] = []
        silent: List[datetime] = []  # Beginn eines stillen Stillstands
        silent_open = False

        def release(target: Executor, moment: datetime) -> None:
            if not hasattr(target, "release_halt"):
                raise BacktestError("halt_review braucht einen Executor mit Freigabe (release_halt).")
            target.release_halt(REVIEW_OPERATOR, RELEASE_CONFIRMATION)
            releases.append(moment)
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
            if not tick.closes or now < trade_from:  # Vorlauf: nur Historie
                continue
            if cfg.halt_review and release_at is not None and now >= release_at:
                release(executor, now)
                release_at, silent_open = None, False
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
                started = time.perf_counter() if self._measure else 0.0
                result = pipeline.process_setup(setup, fill(tick, setup.instrument))
                if self._measure:
                    watch.add("ENTSCHEIDUNG_GESAMT", time.perf_counter() - started)
                if result.executed:
                    executed += 1
                for reason in result.reasons:
                    blocked[reason.value] = blocked.get(reason.value, 0) + 1
                stage = "FILLED" if result.executed else result.stage
                by_stage[stage] = by_stage.get(stage, 0) + 1
                setup_log.append(SetupRecord(setup.setup_id, now, setup.instrument.key, setup.side.value, stage,
                                             tuple(r.value for r in result.reasons)))
            stopped = halted(executor)
            if not stopped and not silent_open and any(BRAKES & set(s.reasons) for s in setup_log if s.time == now):
                silent.append(now)  # stiller Stillstand: Bremse blockiert, der Not-Aus loest aber nicht aus
                silent_open = stopped = True
            if cfg.halt_review and release_at is None and stopped:
                release_at = next_month(now)

        if cfg.close_at_end:  # zum letzten Schlusskurs schliessen - mit Gebuehr, Spread und Slippage wie jeder Ausstieg
            for position in executor.open_positions:
                bars = history[position.instrument]
                if not bars:
                    continue
                closing = executor.close_position(position.instrument, context(position.instrument, bars[-1].close).snapshot,
                                                  ExitReason.MANUAL, "END-%s" % position.instrument.key)
                if closing.closed and closing.trade is not None:
                    aki.register_closed_trades([closing.trade])
            if executor.open_positions:
                raise BacktestError("Positionen liessen sich am Ende nicht schliessen.")
            aki.update_equity(clock.now(), executor.portfolio_state())  # Punkt des letzten Zeitpunkts nach dem Schliessen

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
        halts = log.of_type(EventType.HALT_ACTIVATED)
        halted_at = datetime.fromisoformat(halts[0].ts) if halts else None
        halt_reason = str(halts[0].payload.get("reason", "")) if halts else ""
        events_list: List[Tuple[datetime, str]] = [(datetime.fromisoformat(h.ts), str(h.payload.get("reason", "")))
                                                    for h in halts]
        events_list += [(moment, SILENT_STOP) for moment in silent]
        events_list.sort()
        halt_list = tuple((moment, reason, next((r for r in releases if r >= moment), None))
                          for moment, reason in events_list)
        return BacktestResult(
            strategy=self._strategy.name, label=cfg.label, start=trade_from, end=data.end,
            dataset_fingerprint=data.fingerprint, assumptions_fingerprint=assumptions_fp,
            config_fingerprint=config_fp, report=report, trades=trades, equity_curve=curve,
            open_positions=open_positions, setups_proposed=proposed, setups_executed=executed,
            blocked_by_reason=dict(sorted(blocked.items())), exits_requested=exits_requested,
            exits_executed=exits_executed, placeholders=placeholders, data_warnings=data.warnings,
            events=len(events), log_head=events[-1].hash, result_hash=result_hash,
            blocked_by_stage=dict(sorted(by_stage.items())), setup_log=tuple(setup_log), halted_at=halted_at,
            halt_reason=halt_reason, halts=halt_list, timings=watch.summary(),
        )
