"""Kennzahlen ueber abgeschlossene Trades (von AKI genutzt). Reine Rechenfunktion ohne Seiteneffekte.

Alle Ergebnisse sind NETTO: nach Gebuehren, Slippage (steckt in den Ausfuehrungskursen) und Funding.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional, Sequence

from trading.domain.models import ClosedTrade

ZERO = Decimal(0)


@dataclass(frozen=True)
class PerformanceStats:
    trade_count: int
    wins: int  # Netto-Ergebnis > 0
    losses: int  # Netto-Ergebnis < 0
    win_rate: Decimal  # Gewinner / alle Trades (Nullsummen-Trades zaehlen NICHT als Gewinner)
    net_pnl: Decimal
    gross_profit: Decimal  # Summe der Netto-Gewinne der Gewinner
    gross_loss: Decimal  # Summe der Netto-Verluste der Verlierer (positiv)
    profit_factor: Optional[Decimal]  # None, wenn es keine Verluste gab
    expectancy: Decimal  # durchschnittlicher Netto-Gewinn je Trade
    avg_r: Decimal  # durchschnittliches Ergebnis in Vielfachen des geplanten Risikos
    max_drawdown_pct: Decimal  # nur aus abgeschlossenen Trades; die Kapitalkurve ist genauer
    total_fees: Decimal
    total_funding: Decimal
    return_pct: Decimal
    breakeven: int = 0  # Netto-Ergebnis genau 0
    avg_win: Decimal = ZERO  # durchschnittlicher Gewinn der Gewinner
    avg_loss: Decimal = ZERO  # durchschnittlicher Verlust der Verlierer (positiv)
    payoff_ratio: Optional[Decimal] = None  # avg_win / avg_loss
    largest_win: Decimal = ZERO
    largest_loss: Decimal = ZERO  # positiv
    max_consecutive_losses: int = 0
    avg_holding_seconds: Decimal = ZERO
    exits_by_reason: Optional[Dict[str, int]] = None


def compute_performance(trades: Sequence[ClosedTrade], initial_equity: Decimal) -> PerformanceStats:
    if initial_equity <= 0:
        raise ValueError("Startkapital muss positiv sein.")
    n = len(trades)
    winners = [t.net_pnl for t in trades if t.net_pnl > 0]
    losers = [-t.net_pnl for t in trades if t.net_pnl < 0]
    gross_profit, gross_loss = sum(winners, ZERO), sum(losers, ZERO)
    net = sum((t.net_pnl for t in trades), ZERO)

    equity = peak = initial_equity
    max_dd = ZERO
    streak = worst_streak = 0
    reasons: Dict[str, int] = {}
    for t in trades:
        equity += t.net_pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        streak = streak + 1 if t.net_pnl < 0 else 0
        worst_streak = max(worst_streak, streak)
        reasons[t.exit_reason.value] = reasons.get(t.exit_reason.value, 0) + 1

    avg_win = gross_profit / len(winners) if winners else ZERO
    avg_loss = gross_loss / len(losers) if losers else ZERO
    held = sum((Decimal(str((t.closed_at - t.opened_at).total_seconds())) for t in trades), ZERO)
    return PerformanceStats(
        trade_count=n,
        wins=len(winners),
        losses=len(losers),
        win_rate=Decimal(len(winners)) / n if n else ZERO,
        net_pnl=net,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=gross_profit / gross_loss if gross_loss > 0 else None,
        expectancy=net / n if n else ZERO,
        avg_r=sum((t.r_multiple for t in trades), ZERO) / n if n else ZERO,
        max_drawdown_pct=max_dd * 100,
        total_fees=sum((t.fees for t in trades), ZERO),
        total_funding=sum((t.funding for t in trades), ZERO),
        return_pct=net / initial_equity * 100,
        breakeven=n - len(winners) - len(losers),
        avg_win=avg_win,
        avg_loss=avg_loss,
        payoff_ratio=avg_win / avg_loss if avg_loss > 0 else None,
        largest_win=max(winners, default=ZERO),
        largest_loss=max(losers, default=ZERO),
        max_consecutive_losses=worst_streak,
        avg_holding_seconds=held / n if n else ZERO,
        exits_by_reason=dict(sorted(reasons.items())),
    )
