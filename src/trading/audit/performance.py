"""Kapitalkurve und Gesamtbericht.

Die Kapitalkurve bewertet zu jedem Zeitpunkt ALLE offenen Positionen zum aktuellen Kurs. Erst sie zeigt
den echten maximalen Drawdown - auch Verluste, die zwischenzeitlich in offenen Positionen steckten.

Begriffe (alles nach Kosten):
  realisiert    Summe der Netto-Ergebnisse ABGESCHLOSSENER Trades
  unrealisiert  Kursgewinn/-verlust OFFENER Positionen zum Bewertungskurs, abzueglich der fuer sie
                bereits bezahlten Kosten (Einstiegsgebuehr, Funding)
  Endkapital    Startkapital + realisiert + unrealisiert   (wird im Bericht exakt nachgerechnet)

Risikobereinigte Kennzahlen (Sharpe, Sortino) werden aus den Renditen zwischen den Punkten der Kurve
berechnet und auf ein Jahr hochgerechnet (365 Tage, risikofreier Zins 0). Bei wenigen Punkten oder
unregelmaessigen Abstaenden sind sie wenig aussagekraeftig - der Bericht weist beides aus.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, Sequence

from trading.audit.stats import PerformanceStats, compute_performance
from trading.domain.models import ClosedTrade, PortfolioState, Position

ZERO = Decimal(0)
SECONDS_PER_YEAR = Decimal(365 * 24 * 3600)


class AccountingError(RuntimeError):
    """Endkapital, Trades und offene Positionen passen nicht zusammen - das darf nie vorkommen."""


@dataclass(frozen=True)
class EquityPoint:
    time: datetime
    balance: Decimal  # realisiertes Guthaben
    unrealized_pnl: Decimal  # Kursgewinn/-verlust offener Positionen (ohne deren schon bezahlte Kosten)
    equity: Decimal
    gross_exposure: Decimal
    open_risk: Decimal
    open_positions: int

    @staticmethod
    def from_state(time: datetime, state: PortfolioState) -> "EquityPoint":
        return EquityPoint(time, state.balance, state.unrealized_pnl, state.equity,
                           state.gross_exposure, state.open_risk, state.open_count)


@dataclass(frozen=True)
class EquityStats:
    points: int
    start_equity: Decimal
    end_equity: Decimal
    total_return_pct: Decimal
    peak_equity: Decimal
    max_drawdown_pct: Decimal
    max_drawdown_abs: Decimal
    max_drawdown_peak_time: Optional[datetime]
    max_drawdown_trough_time: Optional[datetime]
    longest_underwater_seconds: Decimal  # laengste Zeit unter einem frueheren Hoechststand
    step_seconds: Optional[Decimal]  # haeufigster Abstand zwischen zwei Punkten
    regular_steps: bool
    volatility_per_step: Optional[Decimal]
    sharpe: Optional[Decimal]
    sortino: Optional[Decimal]
    time_in_market_pct: Decimal
    max_gross_exposure_pct: Decimal
    avg_gross_exposure_pct: Decimal


def compute_equity_stats(points: Sequence[EquityPoint], initial_equity: Decimal) -> EquityStats:
    """Kennzahlen der Kapitalkurve. Der Drawdown wird gegen den Hoechststand inkl. Startkapital gemessen."""
    if initial_equity <= 0:
        raise ValueError("Startkapital muss positiv sein.")
    for earlier, later in zip(points, points[1:]):
        if later.time <= earlier.time:
            raise ValueError("Die Punkte der Kapitalkurve muessen zeitlich streng aufsteigend sein.")
    n = len(points)
    end = points[-1].equity if points else initial_equity

    peak = initial_equity
    peak_time: Optional[datetime] = None
    dd_pct = dd_abs = ZERO
    dd_peak_time: Optional[datetime] = None
    dd_trough_time: Optional[datetime] = None
    underwater_since: Optional[datetime] = None
    longest = ZERO
    for p in points:
        if p.equity >= peak:
            peak, peak_time, underwater_since = p.equity, p.time, None
            continue
        if underwater_since is None:
            underwater_since = peak_time if peak_time is not None else points[0].time
        longest = max(longest, Decimal(str((p.time - underwater_since).total_seconds())))
        drop = (peak - p.equity) / peak
        if drop > dd_pct:
            dd_pct, dd_abs, dd_peak_time, dd_trough_time = drop, peak - p.equity, peak_time, p.time

    steps = [Decimal(str((b.time - a.time).total_seconds())) for a, b in zip(points, points[1:])]
    step = max(set(steps), key=lambda s: (steps.count(s), -s)) if steps else None
    returns = [b.equity / a.equity - 1 for a, b in zip(points, points[1:]) if a.equity > 0]
    volatility = sharpe = sortino = None
    if len(returns) >= 2 and step is not None and step > 0:
        mean = sum(returns, ZERO) / len(returns)
        variance = sum(((r - mean) ** 2 for r in returns), ZERO) / (len(returns) - 1)
        volatility = variance.sqrt()
        annual = (SECONDS_PER_YEAR / step).sqrt()
        if volatility > 0:
            sharpe = mean / volatility * annual
        downside = (sum((min(r, ZERO) ** 2 for r in returns), ZERO) / len(returns)).sqrt()
        if downside > 0:
            sortino = mean / downside * annual

    exposure = [p.gross_exposure / p.equity * 100 for p in points if p.equity > 0]
    return EquityStats(
        points=n,
        start_equity=initial_equity,
        end_equity=end,
        total_return_pct=(end / initial_equity - 1) * 100,
        peak_equity=peak,
        max_drawdown_pct=dd_pct * 100,
        max_drawdown_abs=dd_abs,
        max_drawdown_peak_time=dd_peak_time,
        max_drawdown_trough_time=dd_trough_time,
        longest_underwater_seconds=longest,
        step_seconds=step,
        regular_steps=len(set(steps)) <= 1,
        volatility_per_step=volatility,
        sharpe=sharpe,
        sortino=sortino,
        time_in_market_pct=Decimal(sum(1 for p in points if p.open_positions > 0)) / n * 100 if n else ZERO,
        max_gross_exposure_pct=max(exposure, default=ZERO),
        avg_gross_exposure_pct=sum(exposure, ZERO) / len(exposure) if exposure else ZERO,
    )


@dataclass(frozen=True)
class PerformanceReport:
    initial_equity: Decimal
    final_equity: Decimal
    net_result: Decimal  # final_equity - initial_equity
    realized_pnl: Decimal
    unrealized_pnl: Decimal  # offene Positionen, nach deren bereits bezahlten Kosten
    open_positions: int
    total_fees: Decimal  # abgeschlossene Trades + Einstiegsgebuehren offener Positionen
    total_funding: Decimal
    trades: PerformanceStats
    equity: EquityStats


def build_report(
    initial_equity: Decimal,
    trades: Sequence[ClosedTrade],
    points: Sequence[EquityPoint],
    open_positions: Sequence[Position],
) -> PerformanceReport:
    """Gesamtbericht. Rechnet exakt nach, dass Endkapital = Start + realisiert + unrealisiert gilt."""
    trade_stats = compute_performance(trades, initial_equity)
    equity_stats = compute_equity_stats(points, initial_equity)
    final = equity_stats.end_equity
    realized = trade_stats.net_pnl
    open_fees = sum((p.entry_fee for p in open_positions), ZERO)
    open_funding = sum((p.funding_paid for p in open_positions), ZERO)
    price_pnl = points[-1].unrealized_pnl if points else ZERO
    unrealized = price_pnl - open_fees - open_funding
    if (points or open_positions) and final != initial_equity + realized + unrealized:
        raise AccountingError(
            "Endkapital %s != Start %s + realisiert %s + unrealisiert %s"
            % (final, initial_equity, realized, unrealized)
        )
    if points and points[-1].open_positions != len(open_positions):
        raise AccountingError("Kapitalkurve und offene Positionen gehoeren nicht zum selben Zeitpunkt.")
    return PerformanceReport(
        initial_equity=initial_equity,
        final_equity=final,
        net_result=final - initial_equity,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        open_positions=len(open_positions),
        total_fees=trade_stats.total_fees + open_fees,
        total_funding=trade_stats.total_funding + open_funding,
        trades=trade_stats,
        equity=equity_stats,
    )
