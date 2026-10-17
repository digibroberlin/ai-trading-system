"""Belastbarkeit von Ergebnissen (AP-20): Tageswerte, risikobereinigte Kennzahlen, Block-Bootstrap, gepaarte Vergleiche.

Warum Tageswerte: Stundenrenditen sind stark von Null-Schritten (kein Trade) gepraegt und autokorreliert; Tageswerte
sind robuster und ueber Strategien und Varianten vergleichbar (gleiche Tage = gepaart). Annualisiert wird mit 365 Tagen
(Krypto handelt taeglich), risikofreier Zins 0.

Warum Block-Bootstrap: Renditen haengen zeitlich zusammen (Volatilitaets-Phasen). Ein einfacher Bootstrap einzelner Tage
unterschaetzt die Unsicherheit. Hier: zirkulaerer Block-Bootstrap (Bloecke von `block` Tagen, fester Startwert -> jeder
Lauf liefert dieselben Grenzen). Gleitkommazahlen sind hier richtig: Es geht um Statistik, nicht um Buchhaltung.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from trading.audit.performance import EquityPoint

DAYS_PER_YEAR = 365.0


def daily_equity(curve: Sequence[EquityPoint]) -> List[Tuple[date, float]]:
    """Letzter Stand jedes UTC-Tages (aufsteigend)."""
    out: Dict[date, float] = {}
    for p in curve:
        out[p.time.astimezone(timezone.utc).date()] = float(p.equity)
    return sorted(out.items())


def daily_pnl(daily: Sequence[Tuple[date, float]], initial: float) -> List[Tuple[date, float]]:
    """Tagesergebnis in Geld (Stand minus Vortag; der erste Tag gegen das Startkapital)."""
    out: List[Tuple[date, float]] = []
    previous = initial
    for day, equity in daily:
        out.append((day, equity - previous))
        previous = equity
    return out


def returns_of(daily: Sequence[Tuple[date, float]], initial: float) -> List[float]:
    out: List[float] = []
    previous = initial
    for _, equity in daily:
        out.append(equity / previous - 1.0 if previous > 0 else 0.0)
        previous = equity
    return out


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def sharpe(returns: Sequence[float]) -> Optional[float]:
    sd = _stdev(returns)
    return None if sd == 0 else _mean(returns) / sd * math.sqrt(DAYS_PER_YEAR)


def sortino(returns: Sequence[float]) -> Optional[float]:
    downside = [min(0.0, r) for r in returns]
    dd = math.sqrt(sum(d * d for d in downside) / len(downside)) if downside else 0.0
    return None if dd == 0 else _mean(returns) / dd * math.sqrt(DAYS_PER_YEAR)


def max_drawdown(daily: Sequence[Tuple[date, float]], initial: float) -> float:
    """Groesster Rueckgang vom Hoechststand in Prozent (auf Tagesschluss-Basis)."""
    peak, worst = initial, 0.0
    for _, equity in daily:
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst * 100.0


def cagr(initial: float, final: float, days: int) -> Optional[float]:
    if initial <= 0 or days <= 0 or final <= 0:
        return None
    growth: float = math.pow(final / initial, DAYS_PER_YEAR / days)
    return (growth - 1.0) * 100.0


@dataclass(frozen=True)
class Interval:
    estimate: float
    low: float
    high: float
    samples: int

    @property
    def excludes_zero_from_above(self) -> bool:
        return self.low > 0


def block_bootstrap_mean(values: Sequence[float], block: int = 20, resamples: int = 2000, seed: int = 7,
                         level: float = 0.95) -> Optional[Interval]:
    """Vertrauensbereich des Mittelwerts mit zirkulaerem Block-Bootstrap. None bei weniger als 2 Bloecken Daten."""
    n = len(values)
    if n < 2 * block or block < 1 or resamples < 100:
        return None
    rng = random.Random(seed)
    means: List[float] = []
    blocks = math.ceil(n / block)
    for _ in range(resamples):
        total, count = 0.0, 0
        for _ in range(blocks):
            start = rng.randrange(n)
            for k in range(block):
                if count == n:
                    break
                total += values[(start + k) % n]
                count += 1
        means.append(total / n)
    means.sort()
    alpha = (1.0 - level) / 2.0
    return Interval(_mean(values), means[int(alpha * resamples)], means[min(resamples - 1, int((1 - alpha) * resamples))],
                    n)


def paired_difference(a: Sequence[Tuple[date, float]], b: Sequence[Tuple[date, float]], block: int = 20,
                      resamples: int = 2000, seed: int = 7) -> Optional[Interval]:
    """Mittlerer Unterschied der Tagesergebnisse a - b an GLEICHEN Tagen (gepaart), mit Block-Bootstrap."""
    other = dict(b)
    diffs = [value - other[day] for day, value in a if day in other]
    return block_bootstrap_mean(diffs, block, resamples, seed)


def deflated_sharpe_ok(daily_returns: Sequence[float], trials: int, z: float = 1.645) -> Tuple[bool, Optional[float],
                                                                                              Optional[float]]:
    """(besteht?, Sharpe je Tag, geforderte Sharpe je Tag) - vereinfachte deflationierte Sharpe-Ratio wie in AP-18,
    aber auf TAGESrenditen und mit der Zahl ALLER Versuche der Untersuchungsreihe."""
    if trials < 1:
        raise ValueError("Mindestens ein Versuch.")
    n = len(daily_returns)
    sd = _stdev(daily_returns)
    if n < 2 or sd == 0:
        return False, None, None
    sr = _mean(daily_returns) / sd
    se = math.sqrt((1 + sr * sr / 2) / (n - 1))
    required = se * (z + (math.sqrt(2 * math.log(trials)) if trials > 1 else 0.0))
    return sr > required, sr, required


def as_float(value: Optional[Decimal]) -> Optional[float]:
    return None if value is None else float(value)


def utc_date(moment: datetime) -> date:
    return moment.astimezone(timezone.utc).date()


__all__ = ["Interval", "as_float", "block_bootstrap_mean", "cagr", "daily_equity", "daily_pnl", "deflated_sharpe_ok",
           "max_drawdown", "paired_difference", "returns_of", "sharpe", "sortino", "utc_date"]
