"""Ereignisgesteuerte Laufzeit-Engine (AP-21) - BLEIBT DEAKTIVIERT, bis der Eigner den Paper-Betrieb freigibt.

Verarbeitet Marktereignisse einzeln (keine Dauerschleife ueber alle Agenten):
  Kerze geschlossen -> Executor (Stop/Ziel/Liquidation, Datenschutz) -> Kapitalkurve -> Marktphase (bei Wechsel:
  Ereignis REGIME_CHANGE, KI hoechstens ueber den Kosten-Orchestrator) -> Strategie (MOMO) fuer GENAU dieses Instrument
  -> Ausstiege, dann Einstiege durch die volle Pipeline (RIN -> SORA-Vorpruefung -> KIBO -> YUZU -> SORA unter der
  Sperre -> Executor mit Risikomanager) -> AKI protokolliert alles.
  Funding -> Executor.
Schneller Regelpfad: Ohne konfigurierte KI-Pruefung laeuft alles regelbasiert und kostenlos. KI nur ereignisbasiert ueber
die Aktivierungsregeln des Orchestrators (D-016), die Kosten je Ereignis werden gemessen.

Kennungen der Setups entstehen aus Strategie, Instrument, Richtung und Entscheidungszeit: Wird nach einem Neustart
dieselbe Kerze noch einmal verarbeitet, liefert der Executor das urspruengliche Ergebnis (keine Doppel-Order, AP-08).

Start im Betrieb (`run`) nur, wenn ALLES zutrifft: `EngineConfig.enabled`, Modus PAPER (keine Boersen-Schluessel) und
die ausdrueckliche Freigabe des Eigners in `TRADING_ENGINE_APPROVAL`. In Tests wird nur `process`/`simulate` mit
simulierter Uhr und erfundenen Daten benutzt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from trading.agents.aki import AkiAgent
from trading.agents.ai_review import AIReview
from trading.agents.contracts import ExitSignal, MarketContext
from trading.agents.kibo import KiboAgent
from trading.agents.kibo_rules import RuleBasedKibo
from trading.agents.momo import MomoAgent
from trading.agents.rin_rules import RinConfig, RuleBasedRin
from trading.agents.sora_rules import RuleBasedSora, SizingPolicy, SoraConfig
from trading.agents.yuzu import YuzuAgent
from trading.agents.yuzu_rules import RuleBasedYuzu
from trading.ai.activation import TriggerEvent
from trading.ai.orchestrator import AnalysisRequest
from trading.analysis.regime import DEFAULT_REGIME, MarketRegime, RegimeConfig, detect_regime
from trading.backtest.strategy import MarketView, Strategy, StrategyDecision, on_ticks
from trading.clock import Clock, FixedClock
from trading.config import load_settings
from trading.domain.exchange_rules import ExchangeRules
from trading.domain.instrument import InstrumentId
from trading.domain.models import Candle, FundingEvent, MarketSnapshot, Position, TradeSetup
from trading.domain.universe import TradingUniverse
from trading.execution.costs import CostModel
from trading.execution.protocol import Executor
from trading.marketdata.candles import candle_problems
from trading.marketdata.synthetic import snapshot_from_price
from trading.pipeline import TradingPipeline
from trading.safety.limits import DEFAULT_LIMITS, SafetyLimits

D = Decimal
ZERO = D(0)
APPROVAL_ENV = "TRADING_ENGINE_APPROVAL"
APPROVAL_TEXT = "PAPER-HANDEL FREIGEGEBEN"
Event = Union[Candle, FundingEvent]


class EngineDisabledError(RuntimeError):
    """Die Engine darf (noch) nicht laufen."""


@dataclass(frozen=True)
class EngineConfig:
    enabled: bool = False  # Standard: AUS. Einschalten nur mit Freigabe des Eigners.
    history_bars: int = 200
    spread_bps: Decimal = D("2")  # angenommener Spread, solange es keine echten Kurse gibt
    max_consecutive_errors: int = 3  # danach Not-Aus (fail closed)
    regime: RegimeConfig = field(default_factory=lambda: DEFAULT_REGIME)

    def __post_init__(self) -> None:
        for name in ("history_bars", "max_consecutive_errors"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError("%s muss eine ganze Zahl >= 1 sein." % name)
        if not isinstance(self.spread_bps, Decimal) or not self.spread_bps.is_finite() or self.spread_bps < 0:
            raise ValueError("spread_bps muss eine nicht-negative Decimal-Zahl sein.")


def check_start_allowed(config: EngineConfig, environ: Optional[Mapping[str, str]] = None) -> None:
    """Wirft EngineDisabledError (bzw. ConfigError), solange der Betrieb nicht ausdruecklich freigegeben ist."""
    env = os.environ if environ is None else environ
    if not config.enabled:
        raise EngineDisabledError("Laufzeit-Engine ist deaktiviert (EngineConfig.enabled=False).")
    load_settings(env)  # nur PAPER, keine Boersen-Schluessel
    if env.get(APPROVAL_ENV) != APPROVAL_TEXT:
        raise EngineDisabledError("Freigabe des Eigners fehlt (%s=%r)." % (APPROVAL_ENV, APPROVAL_TEXT))


@dataclass
class EngineStats:
    events: int = 0
    candles: int = 0
    rejected_candles: int = 0
    fundings: int = 0
    decisions: int = 0
    setups: int = 0
    executed: int = 0
    blocked: int = 0
    replayed: int = 0  # schon frueher bearbeitet (z. B. dieselbe Kerze nach einem Neustart): nichts gehandelt
    exits_requested: int = 0
    exits_executed: int = 0
    closed_trades: int = 0
    regime_changes: int = 0
    errors: int = 0
    ai_paid_calls: int = 0
    ai_cost: Decimal = ZERO


@dataclass(frozen=True)
class EventOutcome:
    kind: str  # "candle", "funding", "rejected", "error"
    time: datetime
    instrument: str
    setups: int = 0
    executed: int = 0
    closed_trades: int = 0
    regime_change: Optional[Tuple[str, str]] = None
    ai_cost: Decimal = ZERO
    ai_paid_calls: int = 0
    note: str = ""


class DecisionMomo(MomoAgent):
    """MOMO der Laufzeit: fragt die Strategie fuer EIN Instrument und vergibt Kennungen aus der Entscheidungszeit."""

    def __init__(self, strategy: Strategy, clock: Clock, rules: Optional[ExchangeRules] = None) -> None:
        super().__init__()
        self._strategy, self._clock, self._rules = strategy, clock, rules
        self.decision = StrategyDecision()

    def decide(self, view: MarketView) -> StrategyDecision:
        self.decision = self._strategy.on_bar(view)
        return self.decision

    def scan(self, contexts: Sequence[MarketContext]) -> List[TradeSetup]:
        now = self._clock.now()
        reference = {c.snapshot.instrument: c.snapshot.last for c in contexts}
        out: List[TradeSetup] = []
        for p in self.decision.entries:
            if p.instrument not in reference:
                continue
            setup_id = "%s|%s|%s|%s" % (self._strategy.name, p.instrument.key, p.side.value, now.strftime("%Y%m%dT%H%M%SZ"))
            stop, target = on_ticks(self._rules, p)
            out.append(TradeSetup(setup_id, p.instrument, p.side, reference[p.instrument], stop, target,
                                  p.horizon, self._strategy.name, now, p.rationale))
        return out

    def exit_signals(self, contexts: Sequence[MarketContext], positions: Sequence[Position]) -> List[ExitSignal]:
        now = self._clock.now()
        return [ExitSignal("%s|x|%s|%s" % (self._strategy.name, p.instrument.key, now.strftime("%Y%m%dT%H%M%SZ")),
                           p.instrument, self._strategy.name, now, p.rationale) for p in self.decision.exits]


class RuntimeEngine:
    def __init__(
        self,
        strategy: Strategy,
        executor: Executor,
        aki: AkiAgent,
        clock: Clock,
        universe: TradingUniverse,
        *,
        kibo: Optional[KiboAgent] = None,
        yuzu: Optional[YuzuAgent] = None,
        rin_config: Optional[RinConfig] = None,
        sora_config: Optional[SoraConfig] = None,
        costs: Optional[CostModel] = None,
        limits: SafetyLimits = DEFAULT_LIMITS,
        config: Optional[EngineConfig] = None,
        regime_review: Optional[AIReview] = None,
        funding_rates: Optional[Mapping[InstrumentId, Decimal]] = None,
        rules: Optional[ExchangeRules] = None,
    ) -> None:
        self._executor, self._aki, self._clock = executor, aki, clock
        self._config = config or EngineConfig()
        self._costs = costs or CostModel()
        self._momo = DecisionMomo(strategy, clock, rules)
        self._pipeline = TradingPipeline(
            self._momo, RuleBasedRin(universe, clock, rin_config, self._costs), kibo or RuleBasedKibo(),
            yuzu or RuleBasedYuzu(), RuleBasedSora(SizingPolicy.from_limits(limits), self._costs, sora_config, rules=rules), aki,
            executor)
        self._regime_review = regime_review
        self._funding: Dict[InstrumentId, Decimal] = dict(funding_rates or {})
        self._history: Dict[InstrumentId, List[Candle]] = {}
        self._regimes: Dict[InstrumentId, MarketRegime] = {}
        self._consecutive_errors = 0
        # Trades, die AKI schon kennt (nach einem Neustart: alles, was der Executor bereits geschlossen hat)
        self._registered = len(executor.closed_trades)
        self.stats = EngineStats()

    # ------------------------------------------------------------------ Betrieb

    def run(self, events: Iterable[Event], environ: Optional[Mapping[str, str]] = None) -> EngineStats:
        """Echter (Paper-)Betrieb - nur mit Freigabe (siehe Modulbeschreibung)."""
        check_start_allowed(self._config, environ)
        for event in events:
            self.process(event)
        return self.stats

    def warm_up(self, candles: Sequence[Candle]) -> int:
        """Nach einem Neustart: Historie fuer Strategie und Marktphase auffuellen - OHNE zu handeln."""
        taken = 0
        for c in sorted(candles, key=lambda c: (c.instrument.key, c.open_time)):
            if self._acceptable(c):
                self._remember(c)
                taken += 1
        return taken

    # ------------------------------------------------------------------ Ereignisse

    def process(self, event: Event) -> EventOutcome:
        self.stats.events += 1
        paid_before, cost_before = self._ai_totals()
        try:
            if isinstance(event, FundingEvent):
                outcome = self._on_funding(event)
            elif isinstance(event, Candle):
                outcome = self._on_candle(event)
            else:
                raise TypeError("Unbekanntes Ereignis: %s" % type(event).__name__)
            self._consecutive_errors = 0
        except Exception as exc:  # noqa: BLE001 - jeder Fehler wird protokolliert; wiederholt -> Not-Aus
            outcome = self._failed(event, exc)
        paid_after, cost_after = self._ai_totals()
        self.stats.ai_paid_calls += paid_after - paid_before
        self.stats.ai_cost += cost_after - cost_before
        if paid_after == paid_before and cost_after == cost_before:
            return outcome
        return EventOutcome(outcome.kind, outcome.time, outcome.instrument, outcome.setups, outcome.executed,
                            outcome.closed_trades, outcome.regime_change, cost_after - cost_before,
                            paid_after - paid_before, outcome.note)

    def _on_funding(self, event: FundingEvent) -> EventOutcome:
        self._executor.apply_funding(event)
        self._funding[event.instrument] = event.rate
        self.stats.fundings += 1
        self._record_equity()
        return EventOutcome("funding", event.time, event.instrument.key)

    def _on_candle(self, candle: Candle) -> EventOutcome:
        accepted = self._acceptable(candle)
        self._executor.on_candle(candle)  # Stops/Ziele/Bewertung; der Executor prueft und protokolliert selbst
        if not accepted:
            self.stats.rejected_candles += 1
            return EventOutcome("rejected", candle.close_time, candle.instrument.key, note="Kerze verworfen")
        self.stats.candles += 1
        self._remember(candle)
        now = self._clock.now()
        change = self._regime(candle.instrument)

        history = tuple(self._history[candle.instrument])
        positions = tuple(p for p in self._executor.open_positions if p.instrument == candle.instrument)
        self._momo.decide(MarketView(now, {candle.instrument: history}, positions))
        self.stats.decisions += 1
        ctx = MarketContext(self._snapshot(candle), history)
        for signal in self._pipeline.propose_exits([ctx], positions):
            self.stats.exits_requested += 1
            if self._pipeline.process_exit(signal, ctx).closed:
                self.stats.exits_executed += 1
        executed = 0
        setups = self._pipeline.propose([ctx])
        for setup in setups:
            result = self._pipeline.process_setup(setup, ctx)
            if result.executed:
                executed += 1
            elif result.result is not None and result.result.replayed:
                self.stats.replayed += 1
            else:
                self.stats.blocked += 1
        self.stats.setups += len(setups)
        self.stats.executed += executed
        closed_now = self._record_equity()
        return EventOutcome("candle", candle.close_time, candle.instrument.key, len(setups), executed, closed_now,
                            change)

    # ------------------------------------------------------------------ intern

    def _record_equity(self) -> int:
        """Neu geschlossene Trades (Stop/Ziel UND Ausstiegssignal) an AKI, dann die Kapitalkurve NACH allen Folgen des
        Ereignisses; gleichzeitige Ereignisse ergeben einen Punkt mit dem Stand nach dem letzten. Rueckgabe: neue Trades."""
        trades = self._executor.closed_trades
        new = trades[self._registered:]
        if new:
            self._aki.register_closed_trades(new)
            self._registered = len(trades)
            self.stats.closed_trades += len(new)
        self._aki.update_equity(self._clock.now(), self._executor.portfolio_state())
        return len(new)

    def _acceptable(self, candle: Candle) -> bool:
        if list(candle_problems(candle)) or candle.close_time > self._clock.now():
            return False
        history = self._history.get(candle.instrument)
        return not history or candle.open_time >= history[-1].close_time

    def _remember(self, candle: Candle) -> None:
        history = self._history.setdefault(candle.instrument, [])
        history.append(candle)
        del history[:-self._config.history_bars]

    def _snapshot(self, candle: Candle) -> MarketSnapshot:
        # Futures ohne bekannten Funding-Satz: bewusst KEIN Ersatzwert - Validator und RIN blockieren dann (fail closed)
        rate = self._funding.get(candle.instrument)
        return snapshot_from_price(candle.instrument, candle.close_time, candle.close, self._config.spread_bps, rate,
                                   candle)

    def _regime(self, instrument: InstrumentId) -> Optional[Tuple[str, str]]:
        regime = detect_regime(self._history[instrument], self._config.regime)
        if regime is None:
            return None
        before = self._regimes.get(instrument)
        self._regimes[instrument] = regime
        if before is None or (before.trend, before.volatility) == (regime.trend, regime.volatility):
            return None
        change = ("%s/%s" % (before.trend.value, before.volatility.value),
                  "%s/%s" % (regime.trend.value, regime.volatility.value))
        self.stats.regime_changes += 1
        self._aki.record_regime_change(instrument.key, change[0], change[1])
        if self._regime_review is not None:  # nur ereignisbasiert, ueber Aktivierung/Budget des Orchestrators
            self._regime_review.orchestrator.analyze(AnalysisRequest(
                "YUZU", "yuzu.regime_review", "Neue Marktphase einordnen. Antworte nur als JSON mit confidence.",
                dict(regime.as_payload(), instrument=instrument.key, previous=change[0]),
                self._regime_review.value_usd, TriggerEvent.REGIME_CHANGE, instrument.key, None,
                self._regime_review.max_tier))
        return change

    def _ai_totals(self) -> Tuple[int, Decimal]:
        records = self._aki.ai_records
        return (sum(1 for r in records if r.source.value == "AI"), sum((r.cost for r in records), ZERO))

    def _failed(self, event: Event, exc: Exception) -> EventOutcome:
        self.stats.errors += 1
        self._consecutive_errors += 1
        self._aki.record_system_error("ENGINE", repr(exc))
        note = "Fehler: %s" % type(exc).__name__
        if self._consecutive_errors >= self._config.max_consecutive_errors:
            self._executor.halt("Laufzeit-Engine: %d Fehler in Folge" % self._consecutive_errors)
            note += " - Not-Aus ausgeloest"
        when = event.close_time if isinstance(event, Candle) else getattr(event, "time", self._clock.now())
        return EventOutcome("error", when, getattr(getattr(event, "instrument", None), "key", "?"), note=note)


def simulate(engine: RuntimeEngine, events: Iterable[Event], clock: FixedClock) -> EngineStats:
    """Nur fuer Tests und Vorfuehrungen: Ereignisse in zeitlicher Reihenfolge mit simulierter Uhr abspielen."""
    def when(e: Event) -> datetime:
        return e.close_time if isinstance(e, Candle) else e.time

    for event in sorted(events, key=lambda e: (when(e), 0 if isinstance(e, FundingEvent) else 1, e.instrument.key)):
        clock.set(when(event))
        engine.process(event)
    return engine.stats


__all__ = ["APPROVAL_ENV", "APPROVAL_TEXT", "DecisionMomo", "EngineConfig", "EngineDisabledError", "EngineStats",
           "EventOutcome", "RuntimeEngine", "check_start_allowed", "simulate"]
