"""Betriebskosten und Nettoergebnis NACH ALLEN Kosten (AKI).

Wirtschaftliches Ziel des Systems: langfristig moeglichst hoher risikobereinigter Nettogewinn nach saemtlichen Kosten.
Dafuer wird hier zusammengefuehrt:
* Handel: Ergebnis der abgeschlossenen Trades. Gebuehren und Funding sind darin einzeln ausgewiesen; Spread und Slippage
  stecken in den Ausfuehrungskursen (zu Ungunsten gerechnet, siehe execution/costs.py) und sind damit ebenfalls enthalten.
* KI-API: aus den Messpunkten des Orchestrators (jeder bezahlte Aufruf mit Tokens und Kosten).
* Server, Marktdaten, Sonstiges: als Betriebskosten mit Zeitraum (z. B. Server je Monat) - anteilig je Auswertungszeitraum.

Doppelzaehlung vermeiden: Gemessene KI-Aufrufe NIE zusaetzlich als Betriebskosten buchen. Die Kategorie AI_API im
Kostenbuch ist nur fuer KI-Kosten, die nicht je Aufruf gemessen werden (z. B. feste Monatsgebuehren, Rechnungsdifferenzen).

Waehrung: alles in USD (wie das Paper-Konto). Betraege sind Decimal, anteilige Kosten werden aufgerundet.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set

from trading.ai.orchestrator import AICallRecord, Source
from trading.domain.models import ClosedTrade
from trading.domain.numeric import is_finite_decimal
from trading.execution.costs import cost_up
from trading.marketdata.candles import is_aware

ZERO = Decimal(0)


class CostCategory(str, Enum):
    AI_API = "AI_API"  # nur NICHT je Aufruf gemessene KI-Kosten (siehe Moduldoku)
    SERVER = "SERVER"
    MARKET_DATA = "MARKET_DATA"
    OTHER = "OTHER"


@dataclass(frozen=True)
class OperatingCost:
    """Betriebskosten fuer einen Zeitraum [start, end). Einmalige Kosten: start == end."""

    category: CostCategory
    amount: Decimal
    start: datetime
    end: datetime
    description: str

    def __post_init__(self) -> None:
        if not isinstance(self.category, CostCategory):
            raise ValueError("Kostenart ungueltig: %r" % (self.category,))
        if not is_finite_decimal(self.amount) or self.amount < 0:
            raise ValueError("Betrag ungueltig: %r" % (self.amount,))
        if not (is_aware(self.start) and is_aware(self.end)) or self.end < self.start:
            raise ValueError("Zeitraum ungueltig (mit Zeitzone, Ende nicht vor Beginn).")
        # Intern immer UTC: Mit einer Ortszeit (z. B. Europe/Berlin) rechnete Python die Dauer ueber eine
        # Sommerzeit-Umstellung hinweg falsch (eine Stunde zu viel oder zu wenig).
        object.__setattr__(self, "start", self.start.astimezone(timezone.utc))
        object.__setattr__(self, "end", self.end.astimezone(timezone.utc))

    def share(self, start: datetime, end: datetime) -> Decimal:
        """Anteil dieser Kosten, der in [start, end) faellt (zeitanteilig, aufgerundet)."""
        if not (is_aware(start) and is_aware(end)):  # sonst deutete astimezone() sie still als Ortszeit des Rechners
            raise ValueError("Zeitraum ohne Zeitzone.")
        start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        if self.start == self.end:
            return self.amount if start <= self.start < end else ZERO
        overlap = (min(end, self.end) - max(start, self.start)).total_seconds()
        if overlap <= 0:
            return ZERO
        whole = (self.end - self.start).total_seconds()
        return cost_up(self.amount * Decimal(str(overlap)) / Decimal(str(whole)))


class CostLedger:
    """Kostenbuch fuer Betriebskosten (nur anhaengen)."""

    def __init__(self) -> None:
        self._costs: List[OperatingCost] = []

    def add(self, cost: OperatingCost) -> None:
        if not isinstance(cost, OperatingCost):
            raise ValueError("Nur OperatingCost.")
        self._costs.append(cost)

    @property
    def costs(self) -> Sequence[OperatingCost]:
        return tuple(self._costs)

    def total(self, start: datetime, end: datetime, category: Optional[CostCategory] = None) -> Decimal:
        return sum((c.share(start, end) for c in self._costs if category is None or c.category is category), ZERO)


COST_ENV = {
    CostCategory.SERVER: "COST_SERVER_MONTHLY_USD",
    CostCategory.MARKET_DATA: "COST_MARKET_DATA_MONTHLY_USD",
    CostCategory.OTHER: "COST_OTHER_MONTHLY_USD",
}


def operating_costs_from_env(env: Mapping[str, str], start: datetime, months: int = 24) -> CostLedger:
    """Monatliche Betriebskosten (AP-28) aus der Umgebung, z. B. COST_SERVER_MONTHLY_USD=5.50 fuer den Server.

    Je Kostenart ein Eintrag je Kalendermonat ab dem Monat von `start` - so rechnet AKI zeitanteilig genau mit.
    Fehlt ein Wert: 0. Ein ungueltiger Wert ist ein Fehler (lieber kein Start als eine falsche Netto-Rechnung)."""
    if not is_aware(start) or months < 1:
        raise ValueError("Beginn mit Zeitzone und mindestens ein Monat.")
    ledger = CostLedger()
    utc = start.astimezone(timezone.utc)
    for category, name in COST_ENV.items():
        raw = (env.get(name) or "0").strip()
        try:
            amount = Decimal(raw)
        except ArithmeticError:
            raise ValueError("%s=%r ist keine Zahl." % (name, raw)) from None
        if not is_finite_decimal(amount) or amount < 0:
            raise ValueError("%s=%r muss eine nicht-negative Zahl sein." % (name, raw))
        if amount == 0:
            continue
        year, month = utc.year, utc.month
        for _ in range(months):
            begin = datetime(year, month, 1, tzinfo=timezone.utc)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
            ledger.add(OperatingCost(category, amount, begin, datetime(year, month, 1, tzinfo=timezone.utc),
                                     "%s (monatlich)" % name))
    return ledger


@dataclass(frozen=True)
class AICostSummary:
    total_cost: Decimal
    paid_calls_by_agent: Mapping[str, int]
    analyses_by_agent: Mapping[str, int]
    input_tokens_by_agent: Mapping[str, int]  # inkl. Cache-Lesen/-Schreiben
    output_tokens_by_agent: Mapping[str, int]
    cost_by_agent: Mapping[str, Decimal]
    cost_by_model: Mapping[str, Decimal]
    cost_by_day: Mapping[date, Decimal]
    cost_by_month: Mapping[str, Decimal]  # "JJJJ-MM"
    analyses: int
    paid_analyses: int
    cost_per_analysis: Optional[Decimal]  # Gesamtkosten / alle Analysen (auch kostenlose)
    cost_per_paid_analysis: Optional[Decimal]
    signals: int  # verschiedene Setups mit mindestens einer Analyse
    cost_per_signal: Optional[Decimal]  # Kosten der Analysen MIT Setup / Zahl dieser Setups
    cost_without_signal: Decimal  # Analysen ohne Setup-Bezug (z. B. Marktphasen-Pruefungen)
    cache_hits: int
    rule_decisions: int
    skipped_by_reason: Mapping[str, int]


def summarize_ai_costs(
    records: Iterable[AICallRecord], start: Optional[datetime] = None, end: Optional[datetime] = None
) -> AICostSummary:
    rows = [r for r in records if (start is None or r.time >= start) and (end is None or r.time < end)]
    paid: Dict[str, int] = defaultdict(int)
    tokens_in: Dict[str, int] = defaultdict(int)
    tokens_out: Dict[str, int] = defaultdict(int)
    by_agent: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_model: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_day: Dict[date, Decimal] = defaultdict(lambda: ZERO)
    by_month: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    analyses: Dict[str, str] = {}
    analysis_cost: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    analysis_setup: Dict[str, Optional[str]] = {}
    skipped: Dict[str, int] = defaultdict(int)
    cache_hits = rule_decisions = 0
    for r in rows:
        analyses.setdefault(r.analysis_id, r.agent)
        analysis_setup.setdefault(r.analysis_id, r.setup_id)
        tokens_in[r.agent] += r.usage.total_input
        tokens_out[r.agent] += r.usage.output_tokens
        if r.source is Source.AI:
            paid[r.agent] += 1
        elif r.source is Source.CACHE:
            cache_hits += 1
        elif r.source is Source.RULES:
            rule_decisions += 1
        else:
            skipped[r.reason] += 1
        if r.cost:
            by_agent[r.agent] += r.cost
            by_model[r.model or "?"] += r.cost
            by_day[r.time.date()] += r.cost
            by_month["%04d-%02d" % (r.time.year, r.time.month)] += r.cost
            analysis_cost[r.analysis_id] += r.cost
    total = sum(analysis_cost.values(), ZERO)
    per_agent: Dict[str, int] = defaultdict(int)
    for agent in analyses.values():
        per_agent[agent] += 1
    setups: Set[str] = {s for s in analysis_setup.values() if s}
    signal_cost = sum((c for a, c in analysis_cost.items() if analysis_setup.get(a)), ZERO)
    paid_count = sum(1 for c in analysis_cost.values() if c > 0)
    return AICostSummary(
        total_cost=total,
        paid_calls_by_agent=dict(paid), analyses_by_agent=dict(per_agent),
        input_tokens_by_agent=dict(tokens_in), output_tokens_by_agent=dict(tokens_out),
        cost_by_agent=dict(by_agent), cost_by_model=dict(by_model), cost_by_day=dict(by_day),
        cost_by_month=dict(by_month),
        analyses=len(analyses), paid_analyses=paid_count,
        cost_per_analysis=_div(total, len(analyses)), cost_per_paid_analysis=_div(total, paid_count),
        signals=len(setups), cost_per_signal=_div(signal_cost, len(setups)),
        cost_without_signal=total - signal_cost,
        cache_hits=cache_hits, rule_decisions=rule_decisions, skipped_by_reason=dict(skipped),
    )


@dataclass(frozen=True)
class TradeCostAttribution:
    """KI-Kosten je Trade - mehrere Analysen koennen zu einem Trade gehoeren, manche fuehren zu keinem.

    Eine Analyse liegt vor dem Einstieg, der Trade wird oft in einem spaeteren Zeitraum geschlossen. Deshalb zaehlen fuer
    die direkte Zuordnung ALLE Analysen bis zum Ende des Zeitraums, deren Setup in einem Trade dieses Zeitraums steckt.
    """

    trades: int
    ai_cost_of_traded_setups: Decimal  # Analysen (auch aus frueheren Zeitraeumen) der hier geschlossenen Trades
    ai_cost_without_trade_in_period: Decimal  # Analysen dieses Zeitraums ohne hier geschlossenen Trade
    # (abgelehnte Setups, Marktbeobachtung - oder Positionen, die noch offen sind bzw. spaeter geschlossen werden)
    direct_cost_per_trade: Optional[Decimal]  # nur zugeordnete Analysen
    # ALLE KI-Kosten der Trades und des Zeitraums / Trades - der ehrliche Betriebswert: zugeordnete Analysen (auch aus
    # frueheren Zeitraeumen) + Analysen ohne Trade + nicht je Aufruf gemessene KI-Kosten (Kostenbuch). Nie kleiner als
    # der direkte Wert.
    fully_loaded_cost_per_trade: Optional[Decimal]


def attribute_ai_costs(
    period_records: Iterable[AICallRecord], trades: Sequence[ClosedTrade],
    earlier_records: Iterable[AICallRecord] = (), unmeasured_ai_cost: Decimal = ZERO,
) -> TradeCostAttribution:
    traded = {t.setup_id for t in trades}
    period = list(period_records)
    on_trades = sum((r.cost for r in list(earlier_records) + period if r.setup_id and r.setup_id in traded), ZERO)
    without = sum((r.cost for r in period if not (r.setup_id and r.setup_id in traded)), ZERO)
    total = on_trades + without + unmeasured_ai_cost
    return TradeCostAttribution(
        len(trades), on_trades, without, _div(on_trades, len(trades)), _div(total, len(trades)),
    )


@dataclass(frozen=True)
class NetProfitReport:
    start: datetime
    end: datetime
    trades: int
    gross_pnl: Decimal  # vor Gebuehren und Funding (Spread/Slippage stecken in den Kursen)
    trading_fees: Decimal
    funding: Decimal  # positiv = bezahlt
    trading_net_pnl: Decimal  # nach Gebuehren und Funding
    ai_cost: Decimal  # gemessene Aufrufe + nicht gemessene KI-Kosten aus dem Kostenbuch
    server_cost: Decimal
    market_data_cost: Decimal
    other_cost: Decimal
    operating_cost: Decimal  # Summe aller Betriebskosten (inkl. KI)
    net_after_all_costs: Decimal  # DAS Ergebnis, an dem das System gemessen wird
    ai: AICostSummary
    attribution: TradeCostAttribution


def build_net_profit_report(
    trades: Sequence[ClosedTrade], ai_records: Sequence[AICallRecord], ledger: CostLedger,
    start: datetime, end: datetime,
) -> NetProfitReport:
    if not (is_aware(start) and is_aware(end)) or end <= start:
        raise ValueError("Zeitraum ungueltig (UTC, Ende nach Beginn).")
    period_trades = [t for t in trades if start <= t.closed_at < end]
    records = [r for r in ai_records if start <= r.time < end]
    earlier = [r for r in ai_records if r.time < start]
    ai = summarize_ai_costs(records)
    gross = sum((t.gross_pnl for t in period_trades), ZERO)
    fees = sum((t.fees for t in period_trades), ZERO)
    funding = sum((t.funding for t in period_trades), ZERO)
    net = sum((t.net_pnl for t in period_trades), ZERO)
    unmeasured_ai = ledger.total(start, end, CostCategory.AI_API)  # z. B. Grundgebuehren, nicht je Aufruf gemessen
    ai_cost = ai.total_cost + unmeasured_ai
    server = ledger.total(start, end, CostCategory.SERVER)
    data = ledger.total(start, end, CostCategory.MARKET_DATA)
    other = ledger.total(start, end, CostCategory.OTHER)
    operating = ai_cost + server + data + other
    return NetProfitReport(
        start, end, len(period_trades), gross, fees, funding, net, ai_cost, server, data, other, operating,
        net - operating, ai, attribute_ai_costs(records, period_trades, earlier, unmeasured_ai),
    )


def _div(amount: Decimal, count: int) -> Optional[Decimal]:
    return cost_up(amount / count) if count else None


__all__ = [
    "AICostSummary", "CostCategory", "CostLedger", "NetProfitReport", "OperatingCost", "TradeCostAttribution",
    "attribute_ai_costs", "build_net_profit_report", "summarize_ai_costs",
]
