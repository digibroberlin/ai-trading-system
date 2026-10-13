"""Performance-Kennzahlen (von AKI genutzt). Reine Rechenfunktion ohne Seiteneffekte."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from trading.domain.models import ClosedTrade

ZERO = Decimal(0)


@dataclass(frozen=True)
class PerformanceStats:
    trade_count: int
    wins: int
    losses: int
    win_rate: Decimal
    net_pnl: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: Optional[Decimal]  # None, wenn es keine Verluste gab
    expectancy: Decimal  # durchschnittlicher Netto-Gewinn je Trade
    avg_r: Decimal  # durchschnittliches Ergebnis in Vielfachen des geplanten Risikos
    max_drawdown_pct: Decimal  # auf Basis der Kapitalkurve der abgeschlossenen Trades
    total_fees: Decimal
    total_funding: Decimal
    return_pct: Decimal


def compute_performance(trades: Sequence[ClosedTrade], initial_equity: Decimal) -> PerformanceStats:
    if initial_equity <= 0:
        raise ValueError("Startkapital muss positiv sein.")
    n = len(trades)
    wins = sum(1 for t in trades if t.net_pnl > 0)
    losses = sum(1 for t in trades if t.net_pnl < 0)
    gross_profit = sum((t.net_pnl for t in trades if t.net_pnl > 0), ZERO)
    gross_loss = -sum((t.net_pnl for t in trades if t.net_pnl < 0), ZERO)
    net = sum((t.net_pnl for t in trades), ZERO)

    equity = initial_equity
    peak = initial_equity
    max_dd = ZERO
    for t in trades:
        equity += t.net_pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)

    return PerformanceStats(
        trade_count=n,
        wins=wins,
        losses=losses,
        win_rate=Decimal(wins) / n if n else ZERO,
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
    )
