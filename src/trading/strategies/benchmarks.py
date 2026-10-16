"""Vergleichsmassstaebe (AP-16): Nicht-Handeln und Kaufen-und-Halten.

Eine Strategie ist nur interessant, wenn sie nach ALLEN Kosten besser ist als diese beiden:
  * Nicht-Handeln (`NoTradeStrategy`) - laeuft durch dieselbe Engine; Ergebnis: Startkapital, null Risiko.
  * Kaufen-und-Halten (`buy_and_hold`) - analytisch, weil ein Dauer-Long ohne Stop mit den Sicherheitsregeln
    (Stop-Pflicht, hoechstens 10 % Abstand) gar nicht handelbar waere. Das Kapital wird gleichmaessig auf die
    Instrumente verteilt, zum ERSTEN Eroeffnungskurs gekauft (Ask + Slippage + Gebuehr), zu jedem Schlusskurs bewertet,
    Funding der Futures wird abgezogen, am Ende zum letzten Schlusskurs verkauft (Bid - Slippage - Gebuehr).
    Ohne Hebel. Die Kennzahlen kommen aus derselben Funktion wie bei Backtests (`compute_equity_stats`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Tuple

from trading.audit.performance import EquityPoint, EquityStats, compute_equity_stats
from trading.backtest.data import InstrumentSeries, MarketDataset
from trading.backtest.strategy import MarketView, Strategy, StrategyDecision
from trading.domain.instrument import InstrumentId
from trading.execution.costs import CostModel

ZERO = Decimal(0)


class NoTradeStrategy(Strategy):
    """Handelt nie. Massstab: Jede Strategie muss nach Kosten mehr bringen als Nichtstun."""

    name = "no-trade"

    def on_bar(self, view: MarketView) -> StrategyDecision:
        return StrategyDecision()


@dataclass(frozen=True)
class BenchmarkResult:
    name: str
    initial_equity: Decimal
    final_equity: Decimal  # nach Verkauf, Gebuehren und Funding
    fees: Decimal
    funding: Decimal  # positiv = bezahlt
    equity_curve: Tuple[EquityPoint, ...]
    stats: EquityStats

    @property
    def return_pct(self) -> Decimal:
        return (self.final_equity / self.initial_equity - 1) * 100


@dataclass
class _Holding:
    series: InstrumentSeries
    quantity: Decimal
    funding: Decimal = ZERO


def _half_spread(series: InstrumentSeries) -> Decimal:
    return series.spread_bps / 20000


def buy_and_hold(dataset: MarketDataset, initial_equity: Decimal, costs: CostModel) -> BenchmarkResult:
    if not isinstance(initial_equity, Decimal) or not initial_equity.is_finite() or initial_equity <= 0:
        raise ValueError("initial_equity muss eine positive Decimal-Zahl sein.")
    usable = [s for s in dataset.series if s.candles]
    if not usable:
        raise ValueError("Datensatz ohne Kerzen.")
    share = initial_equity / len(usable)
    slip = costs.slippage_bps / 10000
    holdings: Dict[InstrumentId, _Holding] = {}
    fees = ZERO
    for s in usable:
        price = s.candles[0].open * (1 + _half_spread(s)) * (1 + slip)  # Ask + Slippage
        notional = share / (1 + costs.taker_fee)
        fees += share - notional
        holdings[s.instrument] = _Holding(s, notional / price)

    # Bewertung zu jedem Zeitpunkt, an dem mindestens eine Kerze schliesst (letzter bekannter Schluss je Instrument)
    events: Dict[datetime, List[Tuple[InstrumentId, str, Decimal]]] = {}
    for s in usable:
        for c in s.candles:
            events.setdefault(c.close_time, []).append((s.instrument, "close", c.close))
        for f in s.funding:
            if f.time >= s.candles[0].open_time:
                events.setdefault(f.time, []).append((s.instrument, "funding", f.rate * f.mark_price))
    last: Dict[InstrumentId, Decimal] = {s.instrument: s.candles[0].open for s in usable}
    curve: List[EquityPoint] = []
    for t in sorted(events):
        for instrument, kind, value in sorted(events[t], key=lambda e: (e[0].key, e[1])):
            h = holdings[instrument]
            if kind == "close":
                last[instrument] = value
            else:  # Long zahlt bei positivem Satz
                h.funding += h.quantity * value
        exposure = sum((h.quantity * last[i] for i, h in holdings.items()), ZERO)
        paid = sum((h.funding for h in holdings.values()), ZERO)
        equity = exposure - paid
        curve.append(EquityPoint(t, initial_equity - fees - paid, equity - (initial_equity - fees), equity, exposure,
                                 ZERO, len(holdings)))

    final = ZERO
    for i, h in holdings.items():
        exit_price = last[i] * (1 - _half_spread(h.series)) * (1 - slip)  # Bid - Slippage
        proceeds = h.quantity * exit_price
        fee = proceeds * costs.taker_fee
        fees += fee
        final += proceeds - fee - h.funding
    funding = sum((h.funding for h in holdings.values()), ZERO)
    return BenchmarkResult("buy-and-hold", initial_equity, final, fees, funding, tuple(curve),
                           compute_equity_stats(curve, initial_equity))


__all__ = ["BenchmarkResult", "NoTradeStrategy", "buy_and_hold"]
