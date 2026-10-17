"""Untersuchungsreihe AP-20: Strategien x Agenten-Varianten x Kostenszenarien auf zeitlich getrennten Abschnitten.

Ein Lauf ("Zelle") = ein Abschnitt (Training, Validierung oder Test) x ein Kostenszenario x eine Strategie x eine
Agenten-Variante. Jede Zelle:
  * nutzt den Datensatz mit VORLAUF (Kerzen vor dem Abschnitt nur als Historie, `BacktestConfig.trade_from`),
  * schliesst am Ende alle Positionen mit vollen Kosten (`close_at_end`),
  * liefert eine Kennzahlen-Karte (`Scorecard`) mit Handelsnetto (A) und wirtschaftlichem Netto (B, nach Server- und
    KI-Kosten), Kostenaufteilung, Risikokennzahlen, Zaehlung je Stufe, Long/Short, Marktphase und Jahr,
  * merkt sich je Trade, was KIBO/YUZU im Schatten entschieden haetten (Variante S),
  * zaehlt fuer Variante D die KI-Zweitmeinungen, die die Regeln ausgeloest haetten (unsicheres Urteil ohne harte
    Ablehnung) - als KOSTENSZENARIO; eine echte KI-Entscheidung gibt es nicht (keine Freigabe, B-004).

Not-Aus (Nachtrag 1 zum Protokoll): Standard ist die simulierte monatliche Pruefung des Eigners - nach einem Not-Aus
Freigabe am Ersten des Folgemonats, mit denselben Grenzen; jeder Not-Aus wird gezaehlt. Streng (`halt_review=""`): keine
Freigabe, nach dem ersten Not-Aus keine neue Position bis zum Ende des Abschnitts.
Die Abschnitte, Szenarien und Varianten sind VORAB festgelegt (docs/AP20_PROTOKOLL.md). Testlaeufe sind nur fuer
eingefrorene Kandidaten erlaubt und je Zelle genau einmal (`TestGuard`).
"""

from __future__ import annotations

import hashlib
import json
import os
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from trading.agents.kibo import KiboAgent
from trading.agents.kibo_rules import KiboConfig, RuleBasedKibo
from trading.agents.neutral import NeutralKibo, NeutralYuzu
from trading.agents.rin_rules import RinConfig
from trading.agents.sora_rules import SoraConfig
from trading.agents.yuzu import YuzuAgent
from trading.agents.yuzu_rules import RuleBasedYuzu, YuzuConfig
from trading.ai.pricing import DEFAULT_PRICES, ModelPrice, TokenUsage
from trading.audit.event_log import EventLog, InMemoryEventLog
from trading.audit.events import EventType, to_jsonable
from trading.backtest.data import MarketDataset
from trading.backtest.engine import MONTHLY_REVIEW, BacktestConfig, BacktestEngine, BacktestResult
from trading.backtest.instrumentation import ShadowKibo, ShadowYuzu
from trading.backtest.strategy import Strategy
from trading.clock import Clock
from trading.analysis.regime import DEFAULT_REGIME, detect_regime
from trading.domain.instrument import InstrumentId, MarketType
from trading.domain.models import ClosedTrade, ExitReason, Side
from trading.execution.costs import CostModel
from trading.marketdata.localfile import LocalSeries, futures_proxy
from trading.research.evaluation import UNKNOWN
from trading.research.robust import (
    cagr,
    daily_equity,
    max_drawdown,
    returns_of,
    sharpe,
    sortino,
)
from trading.research.splits import Period
from trading.strategies import STRATEGY_TYPES

D = Decimal
ZERO = D(0)
UTC = timezone.utc
WARMUP_BARS = 300  # >= history_bars (200) und >= Mindest-Historie von Marktphase (114) und Squeeze (80)

# ---------------------------------------------------------------------------------------------- Abschnitte (Protokoll)

TRAIN = Period("training", datetime(2017, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC))
VALIDATION = Period("validierung", datetime(2023, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC))
TEST = Period("test", datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 10, 16, tzinfo=UTC))
PERIODS: Dict[str, Period] = {p.name: p for p in (TRAIN, VALIDATION, TEST)}


# ---------------------------------------------------------------------------------------------- Kostenszenarien

@dataclass(frozen=True)
class CostScenario:
    key: str
    title: str
    market: MarketType
    costs: CostModel
    spread_bps: Decimal
    funding_per_8h: Optional[Decimal]  # nur Futures-Naeherung: SZENARIO, kein echter Satz
    note: str


def stressed(scenario: CostScenario, factor: Decimal = D("1.5")) -> CostScenario:
    """Alle Kosten x `factor`: Gebuehren, Slippage, SPREAD und (Futures) Funding - ungünstig fuer die Long-Seite."""
    c = scenario.costs
    costs = replace(c, taker_fee=c.taker_fee * factor, maker_fee=c.maker_fee * factor,
                    slippage_bps=c.slippage_bps * factor, stop_slippage_bps=c.stop_slippage_bps * factor)
    funding = None if scenario.funding_per_8h is None else scenario.funding_per_8h * factor
    return replace(scenario, key=scenario.key + "x", title=scenario.title + " (Kosten x%s)" % factor, costs=costs,
                   spread_bps=scenario.spread_bps * factor, funding_per_8h=funding)


SCENARIOS: Dict[str, CostScenario] = {
    "F": CostScenario(
        "F", "Futures-Naeherung, Gebuehren wie Bitget USDT-M Standardstufe", MarketType.FUTURES,
        CostModel(taker_fee=D("0.0006"), maker_fee=D("0.0002"), slippage_bps=D("2"), stop_slippage_bps=D("5")),
        D("2"), D("0.0001"),
        "Annahmen: Taker 0,06 %, Maker 0,02 % (letzter bekannter Standardtarif, ungeprueft - Netz gesperrt), Spread 2 bp, "
        "Slippage 2 bp (Stop 5 bp), Funding +0,01 % je 8 h (Long zahlt). Kurse: Bitstamp-Spot als Naeherung fuer den "
        "Perpetual-Kurs."),
    "S": CostScenario(
        "S", "Spot, Gebuehren wie Bitget Spot Standardstufe", MarketType.SPOT,
        CostModel(taker_fee=D("0.001"), maker_fee=D("0.001"), slippage_bps=D("2"), stop_slippage_bps=D("5")),
        D("2"), None,
        "Annahmen: Taker und Maker 0,10 % (letzter bekannter Standardtarif, ungeprueft), Spread 2 bp, Slippage 2 bp "
        "(Stop 5 bp). Nur Long (Spot)."),
    "K": CostScenario(
        "K", "Spot teuer, Gebuehren wie Coinbase Advanced Einstiegsstufe", MarketType.SPOT,
        CostModel(taker_fee=D("0.006"), maker_fee=D("0.004"), slippage_bps=D("2"), stop_slippage_bps=D("5")),
        D("2"), None,
        "Annahmen: Taker 0,60 %, Maker 0,40 % (Groessenordnung der Einstiegsstufe, ungeprueft). Nur Empfindlichkeit."),
}
SCENARIOS.update({s.key: s for s in (stressed(SCENARIOS["F"]), stressed(SCENARIOS["S"]))})  # Fx, Sx: Kosten-Stress


# ---------------------------------------------------------------------------------------------- Agenten-Varianten

NEUTRAL, RULES, SHADOW = "neutral", "regeln", "schatten"


@dataclass(frozen=True)
class Variant:
    key: str
    title: str
    kibo: str
    yuzu: str
    ai_overlay: bool = False  # D: Kosten moeglicher KI-Zweitmeinungen als Szenario (Entscheidungen wie Regeln)
    rin: RinConfig = field(default_factory=RinConfig)
    sora: SoraConfig = field(default_factory=SoraConfig)
    kibo_config: Optional[KiboConfig] = None
    yuzu_config: Optional[YuzuConfig] = None

    def __post_init__(self) -> None:
        if self.kibo not in (NEUTRAL, RULES, SHADOW) or self.yuzu not in (NEUTRAL, RULES, SHADOW):
            raise ValueError("Variante %s: KIBO/YUZU muessen neutral, regeln oder schatten sein." % self.key)

    def agents(self) -> Tuple[KiboAgent, YuzuAgent]:
        kibo_rules, yuzu_rules = RuleBasedKibo(self.kibo_config), RuleBasedYuzu(self.yuzu_config)
        kibo: KiboAgent = {NEUTRAL: NeutralKibo(), RULES: kibo_rules, SHADOW: ShadowKibo(kibo_rules)}[self.kibo]
        yuzu: YuzuAgent = {NEUTRAL: NeutralYuzu(), RULES: yuzu_rules, SHADOW: ShadowYuzu(yuzu_rules)}[self.yuzu]
        return kibo, yuzu


VARIANTS: Dict[str, Variant] = {v.key: v for v in (
    Variant("V0", "MOMO -> RIN -> SORA -> Executor (ohne KIBO/YUZU)", NEUTRAL, NEUTRAL),
    Variant("A", "MOMO -> RIN -> SORA -> KIBO -> Executor", RULES, NEUTRAL),
    Variant("B", "MOMO -> RIN -> SORA -> YUZU -> Executor", NEUTRAL, RULES),
    Variant("C", "MOMO -> RIN -> SORA -> KIBO/YUZU -> Executor (Regel-Architektur)", RULES, RULES),
    Variant("D", "Urspruengliche Architektur: wie C + KI-Zweitmeinung (nur Kostenszenario, keine echte KI)", RULES, RULES,
            ai_overlay=True),
    Variant("S", "Diagnose: Entscheidungen wie V0, KIBO/YUZU urteilen nur im Schatten", SHADOW, SHADOW),
)}


# ---------------------------------------------------------------------------------------------- Strategien

@dataclass(frozen=True)
class StrategySpec:
    key: str  # eindeutig in der Untersuchungsreihe, z. B. "breakout" oder "E1-breakout-4h"
    family: str
    params: Mapping[str, str]  # alle Einstellungen als Text (fuer Register und Bericht)
    factory: Callable[[], Strategy]
    interval_s: int = 3600  # Kerzenlaenge, auf der die Strategie laeuft


STANDARD_KEYS = ("trend", "momentum", "mean_reversion", "breakout", "volatility", "scalping", "sma_cross")


def standard_strategies(keys: Sequence[str] = STANDARD_KEYS) -> List[StrategySpec]:
    """Die vorhandenen Strategien mit ihren STANDARD-Einstellungen (keine Optimierung)."""
    return [StrategySpec(k, k, {"einstellungen": "standard"}, STRATEGY_TYPES[k]) for k in keys]


# ---------------------------------------------------------------------------------------------- Betriebs- und KI-Kosten

@dataclass(frozen=True)
class OperatingAssumptions:
    """Betriebskosten je Monat (USD) - ANNAHMEN, im Bericht so gekennzeichnet."""

    server_usd_month: Decimal = D("6")  # kleiner Cloud-Server (z. B. Hetzner CX22, ca. 4-6 EUR), B-005
    market_data_usd_month: Decimal = ZERO  # oeffentliche Schnittstellen kostenlos
    other_usd_month: Decimal = ZERO
    ai_typical_usage: TokenUsage = TokenUsage(input_tokens=600, output_tokens=60)  # kompakte Anfrage, kurze Antwort
    ai_ceiling_prompt_bytes: int = 2000
    ai_ceiling_output_tokens: int = 400
    ai_models: Tuple[str, ...] = ("claude-haiku-5-5", "claude-sonnet-5-5")

    def monthly(self) -> Decimal:
        return self.server_usd_month + self.market_data_usd_month + self.other_usd_month

    def price(self, model: str) -> ModelPrice:
        return DEFAULT_PRICES.get("anthropic", model)


DEFAULT_OPERATING = OperatingAssumptions()
RULE_CONFIDENCE_LIMIT = D("0.80")  # ab hier entscheiden die Regeln allein (RoutingPolicy.rule_confidence)
SOFT_CODES = frozenset({"TECHNICALS_WEAK", "CONDITIONS_UNFAVORABLE"})


def would_ask_ai(approve: bool, score: Decimal, reasons: Sequence[str], codes: Sequence[str],
                 min_score: Decimal = D("0.35")) -> bool:
    """Haette die Regel-Entscheidung eine KI-Zweitmeinung angefragt? (wie agents.ai_review.ask + Orchestrator):
    nicht bei harter Ablehnung, nicht ohne Urteil (zu wenig Kerzen), nur bei Regel-Sicherheit unter 0,80."""
    if any("zu wenig Kerzen" in r for r in reasons):
        return False
    if not approve and set(codes) - SOFT_CODES:
        return False  # harte Ablehnung - die KI kann sie nie aufheben, wird also nicht gefragt
    confidence = min(D("0.95"), D("0.5") + abs(score - min_score))
    return confidence < RULE_CONFIDENCE_LIMIT


# ---------------------------------------------------------------------------------------------- Ergebnisse

@dataclass(frozen=True)
class TradeRow:
    setup_id: str
    opened_at: datetime
    closed_at: datetime
    side: str
    exit_reason: str
    net: Decimal
    r: Decimal
    regime: str
    kibo_would_approve: Optional[bool]  # nur Variante S
    yuzu_would_approve: Optional[bool]


@dataclass(frozen=True)
class Scorecard:
    initial_equity: Decimal
    final_equity: Decimal
    trading_net: Decimal  # A: nach Gebuehren, Spread, Slippage, Funding (alle Positionen geschlossen)
    operating_cost: Decimal  # Server, Marktdaten, Sonstiges - anteilig fuer den Abschnitt (Annahme)
    ai_cost_typical: Decimal  # nur D: moegliche KI-Zweitmeinungen x typische Kosten (Szenario)
    ai_cost_ceiling: Decimal  # nur D: dieselben Aufrufe zur Kosten-Obergrenze
    economic_net: Decimal  # B: A - Betriebskosten - KI-Kosten (typisch)
    return_pct: float
    cagr_pct: Optional[float]
    max_drawdown_pct: float  # Tagesschluss
    max_drawdown_intraday_pct: float  # Kapitalkurve je Kerze
    sharpe: Optional[float]
    sortino: Optional[float]
    calmar: Optional[float]
    days: int
    trades: int
    wins: int
    losses: int
    win_rate: float
    avg_win: Decimal
    avg_loss: Decimal
    expectancy: Decimal  # Netto je Trade
    profit_factor: Optional[float]
    avg_r: float
    fees: Decimal
    funding: Decimal
    spread_slippage: Decimal  # GESCHAETZT aus dem Kostenmodell (in den Kursen enthalten)
    gross_before_costs: Decimal  # Netto + Gebuehren + Funding + Spread/Slippage
    long_trades: int
    long_net: Decimal
    short_trades: int
    short_net: Decimal
    signals: int  # Vorschlaege von MOMO
    executed: int
    by_stage: Mapping[str, int]  # je Setup: FILLED oder blockierende Stufe
    by_reason: Mapping[str, int]
    ai_reviews: int  # Setups, bei denen KIBO/YUZU eine KI-Zweitmeinung angefragt haetten (D) - Zaehlung, kein Aufruf
    halted_at: Optional[datetime]
    halt_reason: str
    halts: Tuple[Tuple[datetime, str, Optional[datetime]], ...]  # jeder Not-Aus (Zeit, Grund, simulierte Freigabe)
    halt_review: str  # "monatlich" (simulierte Pruefung des Eigners) oder "" (streng)
    time_in_market_pct: float
    avg_exposure_pct: float
    by_regime: Mapping[str, Tuple[int, Decimal]]  # Marktphase beim Einstieg -> (Trades, Netto)
    by_year: Mapping[str, Tuple[int, Decimal]]  # Jahr des Ausstiegs -> (Trades, Netto)
    latency_us: Mapping[str, Mapping[str, float]]


@dataclass(frozen=True)
class Cell:
    key: str
    period: str
    scenario: str
    strategy: str
    variant: str
    params: Mapping[str, str]
    dataset_fingerprint: str
    config_fingerprint: str
    result_hash: str
    card: Scorecard
    daily: Tuple[Tuple[str, float], ...]  # (Tag, Kapital am Tagesschluss)
    trades: Tuple[TradeRow, ...]


def cell_key(period: str, scenario: str, strategy: str, variant: str) -> str:
    return "%s|%s|%s|%s" % (period, scenario, strategy, variant)


def _spread_slippage(trade: ClosedTrade, costs: CostModel, spread_bps: Decimal) -> Decimal:
    """Schaetzung der in den Kursen enthaltenen Spread- und Slippage-Kosten eines Trades (aus dem Kostenmodell)."""
    half = spread_bps / 2 / 10000
    slip = costs.slippage_bps / 10000
    entry = trade.quantity * trade.entry_price * (1 - 1 / ((1 + half) * (1 + slip)))
    reason = trade.exit_reason
    if reason is ExitReason.TAKE_PROFIT:
        exit_cost = ZERO  # Ziel wird genau zum Zielkurs gefuellt (Maker)
    elif reason is ExitReason.STOP_LOSS:
        stop = costs.stop_slippage_bps / 10000
        exit_cost = trade.quantity * trade.exit_price * stop / (1 - stop)
    elif reason is ExitReason.LIQUIDATION:
        exit_cost = ZERO
    else:
        exit_cost = trade.quantity * trade.exit_price * (1 / ((1 - half) * (1 - slip)) - 1)
    return (entry + exit_cost).quantize(D("0.00000001"))


class RegimeLookup:
    """Marktphase beim Einstieg wie `evaluation.regime_at` (nur Kerzen, die bis dahin geschlossen hatten), aber mit
    binaerer Suche statt Durchlauf je Trade."""

    def __init__(self, dataset: MarketDataset, history: int = 200) -> None:
        self._series = {s.instrument: s.candles for s in dataset.series}
        self._closes = {s.instrument: [c.close_time for c in s.candles] for s in dataset.series}
        self._history = history
        self._dataset = dataset

    def __call__(self, instrument: InstrumentId, time: datetime) -> str:
        candles = self._series.get(instrument)
        if candles is None:
            return UNKNOWN
        end = bisect_right(self._closes[instrument], time)
        regime = detect_regime(candles[max(0, end - self._history):end], DEFAULT_REGIME)
        return UNKNOWN if regime is None else "%s/%s" % (regime.trend.value, regime.volatility.value)


def _months(period_days: int) -> Decimal:
    return D(period_days) / D("30.4375")


def scorecard(result: BacktestResult, dataset: MarketDataset, scenario: CostScenario,
              operating: OperatingAssumptions = DEFAULT_OPERATING, ai_reviews: int = 0,
              ai_overlay: bool = False, halt_review: str = "") -> Scorecard:
    rep = result.report
    stats = rep.trades
    initial = float(rep.initial_equity)
    daily = daily_equity(result.equity_curve)
    rets = returns_of(daily, initial)
    days = max(1, (result.end - result.start).days)
    dd = max_drawdown(daily, initial)
    growth = cagr(initial, float(rep.final_equity), days)
    calmar = None if growth is None or dd == 0 else growth / dd
    spread_slip = sum((_spread_slippage(t, scenario.costs, scenario.spread_bps) for t in result.trades), ZERO)
    operating_cost = (operating.monthly() * _months(days)).quantize(D("0.01"))
    typical = ceiling = ZERO
    if ai_overlay and ai_reviews:
        cheapest = operating.price(operating.ai_models[0])
        typical = cheapest.cost(operating.ai_typical_usage) * ai_reviews
        ceiling = cheapest.max_cost(operating.ai_ceiling_prompt_bytes, operating.ai_ceiling_output_tokens) * ai_reviews
    longs = [t for t in result.trades if t.side is Side.LONG]
    shorts = [t for t in result.trades if t.side is Side.SHORT]
    regimes: Dict[str, List[Decimal]] = defaultdict(list)
    years: Dict[str, List[Decimal]] = defaultdict(list)
    lookup = RegimeLookup(dataset)
    for t in result.trades:
        regimes[lookup(t.instrument, t.opened_at)].append(t.net_pnl)
        years[str(t.closed_at.astimezone(UTC).year)].append(t.net_pnl)
    pf = None if stats.profit_factor is None else float(stats.profit_factor)
    return Scorecard(
        initial_equity=rep.initial_equity, final_equity=rep.final_equity, trading_net=rep.net_result,
        operating_cost=operating_cost, ai_cost_typical=typical, ai_cost_ceiling=ceiling,
        economic_net=rep.net_result - operating_cost - typical,
        return_pct=float(rep.equity.total_return_pct), cagr_pct=growth, max_drawdown_pct=dd,
        max_drawdown_intraday_pct=float(rep.equity.max_drawdown_pct), sharpe=sharpe(rets), sortino=sortino(rets),
        calmar=calmar, days=days, trades=stats.trade_count, wins=stats.wins, losses=stats.losses,
        win_rate=float(stats.win_rate), avg_win=stats.avg_win, avg_loss=stats.avg_loss, expectancy=stats.expectancy,
        profit_factor=pf, avg_r=float(stats.avg_r), fees=stats.total_fees, funding=stats.total_funding,
        spread_slippage=spread_slip, gross_before_costs=stats.net_pnl + stats.total_fees + stats.total_funding + spread_slip,
        long_trades=len(longs), long_net=sum((t.net_pnl for t in longs), ZERO),
        short_trades=len(shorts), short_net=sum((t.net_pnl for t in shorts), ZERO),
        signals=result.setups_proposed, executed=result.setups_executed, by_stage=dict(result.blocked_by_stage),
        by_reason=dict(result.blocked_by_reason), ai_reviews=ai_reviews, halted_at=result.halted_at,
        halt_reason=result.halt_reason, halts=result.halts, halt_review=halt_review,
        time_in_market_pct=float(rep.equity.time_in_market_pct),
        avg_exposure_pct=float(rep.equity.avg_gross_exposure_pct),
        by_regime={k: (len(v), sum(v, ZERO)) for k, v in sorted(regimes.items())},
        by_year={k: (len(v), sum(v, ZERO)) for k, v in sorted(years.items())},
        latency_us={k: dict(v) for k, v in result.timings.items()},
    )


# ---------------------------------------------------------------------------------------------- Daten je Szenario

class Bundle:
    """Ein Spot-Datensatz (lokale Datei) und die daraus abgeleiteten Datensaetze je Kostenszenario."""

    def __init__(self, spot: LocalSeries) -> None:
        if spot.series.instrument.market_type is not MarketType.SPOT:
            raise ValueError("Bundle braucht eine Spot-Reihe.")
        self.spot = spot
        self._cache: Dict[Tuple[MarketType, Decimal, Optional[Decimal]], MarketDataset] = {}

    @property
    def interval_s(self) -> int:
        return self.spot.series.interval_s

    def dataset(self, scenario: CostScenario) -> MarketDataset:
        key = (scenario.market, scenario.spread_bps, scenario.funding_per_8h)
        if key not in self._cache:
            local = replace(self.spot, series=replace(self.spot.series, spread_bps=scenario.spread_bps))
            if scenario.market is MarketType.FUTURES:
                if scenario.funding_per_8h is None:
                    raise ValueError("Futures-Szenario ohne Funding-Szenario.")
                local = futures_proxy(local, scenario.funding_per_8h)
            self._cache[key] = MarketDataset.build([local.series])
        return self._cache[key]


def window_for(bundle: Bundle, scenario: CostScenario, period: Period, warmup_bars: int = WARMUP_BARS) -> MarketDataset:
    """Abschnitt mit Vorlauf: [Beginn - Vorlauf, Ende]. Der Vorlauf liegt VOR dem Abschnitt (Vergangenheit)."""
    full = bundle.dataset(scenario)
    start = max(full.start, period.start - timedelta(seconds=bundle.interval_s * warmup_bars))
    return full.window(start, period.end)


# ---------------------------------------------------------------------------------------------- ein Lauf

def run_cell(bundle: Bundle, period: Period, spec: StrategySpec, variant: Variant, scenario: CostScenario,
             initial_equity: Decimal = D("10000"), warmup_bars: int = WARMUP_BARS,
             operating: OperatingAssumptions = DEFAULT_OPERATING, measure: bool = True,
             halt_review: str = MONTHLY_REVIEW) -> Cell:
    if bundle.interval_s != spec.interval_s:
        raise ValueError("Strategie %s braucht %d-s-Kerzen, der Datensatz hat %d s." % (spec.key, spec.interval_s,
                                                                                       bundle.interval_s))
    data = window_for(bundle, scenario, period, warmup_bars)
    if data.start > period.start - timedelta(seconds=bundle.interval_s * min(warmup_bars, 200)):
        raise ValueError("Zu wenig Vorlauf vor %s (Daten ab %s)." % (period.start, data.start))
    config = BacktestConfig(initial_equity=initial_equity, costs=scenario.costs, rin=variant.rin, sora=variant.sora,
                            trade_from=period.start, close_at_end=True, halt_review=halt_review,
                            label=cell_key(period.name, scenario.key, spec.key, variant.key))
    kibo, yuzu = variant.agents()
    logs: List[EventLog] = []

    def keep_log(clock: Clock) -> EventLog:
        log = InMemoryEventLog(clock)
        logs.append(log)
        return log

    result = BacktestEngine(data, spec.factory(), config, kibo=kibo, yuzu=yuzu, log_factory=keep_log,
                            measure=measure).run()
    reviews = count_ai_reviews(logs[0]) if variant.ai_overlay else 0
    card = scorecard(result, data, scenario, operating, reviews, variant.ai_overlay, halt_review)
    lookup = RegimeLookup(data)
    kibo_v = getattr(kibo, "verdicts", None)
    yuzu_v = getattr(yuzu, "verdicts", None)
    rows = tuple(TradeRow(
        t.setup_id, t.opened_at, t.closed_at, t.side.value, t.exit_reason.value, t.net_pnl, t.r_multiple,
        lookup(t.instrument, t.opened_at),
        None if kibo_v is None or t.setup_id not in kibo_v else kibo_v[t.setup_id].approve,
        None if yuzu_v is None or t.setup_id not in yuzu_v else yuzu_v[t.setup_id].approve,
    ) for t in result.trades)
    daily = tuple((d.isoformat(), e) for d, e in daily_equity(result.equity_curve))
    return Cell(config.label, period.name, scenario.key, spec.key, variant.key, dict(spec.params), data.fingerprint,
                result.config_fingerprint, result.result_hash, card, daily, rows)


def count_ai_reviews(log: EventLog) -> int:
    """Zahl der KIBO/YUZU-Urteile, die eine KI-Zweitmeinung angefragt haetten (Szenario fuer Variante D)."""
    count = 0
    for event in log.of_type(EventType.AGENT_OPINION):
        p = event.payload
        if p.get("agent") not in ("KIBO", "YUZU"):
            continue
        if would_ask_ai(bool(p.get("approve")), D(str(p.get("score", "0"))), tuple(p.get("reasons") or ()),
                        tuple(p.get("reject_reasons") or ())):
            count += 1
    return count


# ---------------------------------------------------------------------------------------------- Ablage und Testschutz

def cell_path(root: Path, key: str) -> Path:
    period, scenario, strategy, variant = key.split("|")
    return root / period / scenario / ("%s__%s.json" % (strategy, variant))


def save_cell(root: Path, cell: Cell) -> Path:
    path = cell_path(root, cell.key)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(to_jsonable(cell), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_cell(path: Path) -> Dict[str, Any]:
    data: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


class GuardViolation(RuntimeError):
    """Verstoss gegen die Testregeln (nicht eingefroren, schon getestet)."""


@dataclass(frozen=True)
class Freeze:
    """Vorab festgelegte Testkandidaten - mit Pruefsumme. Danach wird nichts mehr veraendert."""

    created_at: datetime
    cells: Tuple[str, ...]  # Schluessel ohne Abschnitt: "Szenario|Strategie|Variante"
    note: str

    @property
    def digest(self) -> str:
        body = json.dumps({"cells": sorted(self.cells), "note": self.note, "created_at": self.created_at.isoformat()},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def save(self, path: Path) -> None:
        if path.exists():
            raise GuardViolation("Einfrieren nur einmal: %s gibt es schon." % path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"created_at": self.created_at.isoformat(), "cells": sorted(self.cells),
                                    "note": self.note, "sha256": self.digest}, indent=1, ensure_ascii=False),
                        encoding="utf-8")

    @staticmethod
    def load(path: Path) -> "Freeze":
        data = json.loads(path.read_text(encoding="utf-8"))
        freeze = Freeze(datetime.fromisoformat(data["created_at"]), tuple(data["cells"]), data["note"])
        if freeze.digest != data["sha256"]:
            raise GuardViolation("Eingefrorene Liste wurde veraendert (Pruefsumme passt nicht).")
        return freeze


def guard_test(freeze_path: Path, results: Path, key: str) -> None:
    """Ein Testlauf ist nur erlaubt, wenn die Zelle eingefroren ist und noch nie getestet wurde."""
    period, scenario, strategy, variant = key.split("|")
    if period != TEST.name:
        return
    if not freeze_path.exists():
        raise GuardViolation("Test erst nach dem Einfrieren der Kandidaten (%s fehlt)." % freeze_path)
    freeze = Freeze.load(freeze_path)
    if "%s|%s|%s" % (scenario, strategy, variant) not in freeze.cells:
        raise GuardViolation("%s ist nicht eingefroren - kein Test." % key)
    if cell_path(results, key).exists():
        raise GuardViolation("%s wurde schon getestet - kein zweiter Blick auf die Testdaten." % key)


__all__ = ["Bundle", "Cell", "CostScenario", "DEFAULT_OPERATING", "Freeze", "OperatingAssumptions", "PERIODS",
           "SCENARIOS", "STANDARD_KEYS", "Scorecard", "StrategySpec", "TEST", "TRAIN", "GuardViolation", "TradeRow",
           "UNKNOWN", "VALIDATION", "VARIANTS", "Variant", "WARMUP_BARS", "cell_key", "cell_path", "count_ai_reviews",
           "guard_test", "load_cell", "run_cell", "save_cell", "scorecard", "standard_strategies", "stressed",
           "window_for", "would_ask_ai"]
