"""Scalping-Pruefung (AP-20, Protokoll Abschnitt 7): Reicht die Kursbewegung in kurzen Zeitraeumen fuer die Kosten?

Auf echten 1-Minuten-Kerzen (nur Minuten mit Umsatz) wird fuer jeden Einstieg zum Eroeffnungskurs einer Minute und
Haltedauern von k Minuten gemessen:
  * |Bewegung| bis zum Schluss nach k Minuten,
  * die groesste GUENSTIGE Bewegung innerhalb der k Minuten (Long: hoechstes Hoch, Short: tiefstes Tief) - eine obere
    Schranke dessen, was ein perfekter Ausstieg erreichen koennte (nur als Hoechstwert, nicht handelbar),
und mit den Rundreise-Kosten jedes Kostenszenarios verglichen (Einstieg Taker + Ausstieg Taker bzw. Maker, Spread,
Slippage). Ausfuehrung frueher als zum Eroeffnungskurs der naechsten Minute wird nicht angenommen; ohne Tick- und
Orderbuchdaten gibt es keine Aussage zu Warteschlange, Teilausfuehrung oder Latenz an der Boerse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from trading.execution.costs import CostModel
from trading.marketdata.localfile import read_minutes

HOLDS = (1, 5, 15, 60)


def round_trip_pct(costs: CostModel, spread_bps: Decimal, maker_exit: bool = False) -> float:
    """Kosten einer Rundreise in Prozent des Einstiegswerts (Naeherung erster Ordnung)."""
    fees = costs.taker_fee + (costs.maker_fee if maker_exit else costs.taker_fee)
    slip = costs.slippage_bps * (1 if maker_exit else 2) / 10000
    return float((fees + spread_bps / 10000 + slip) * 100)


@dataclass(frozen=True)
class HoldStats:
    minutes: int
    samples: int
    median_abs_move_pct: float
    p90_abs_move_pct: float
    median_best_move_pct: float  # groesste guenstige Bewegung (Long und Short gemittelt) - obere Schranke
    share_best_above: Dict[str, float]  # Anteil der Einstiege, bei denen selbst der perfekte Ausstieg die Kosten deckt
    share_close_above: Dict[str, float]  # Anteil, bei dem die Schlussbewegung (richtige Richtung) die Kosten deckt


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def analyse(paths: Sequence[Path], start: datetime, end: datetime, hurdles: Dict[str, float],
            holds: Sequence[int] = HOLDS, stride: int = 7) -> Tuple[List[HoldStats], int]:
    """`stride`: nur jede n-te Minute als Einstieg (gleichmaessige Stichprobe, spart Zeit; 7 ist teilerfremd zu 60)."""
    lo = int(start.timestamp())
    hi = int(end.timestamp())
    times: List[int] = []
    opens: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    closes: List[float] = []
    for t, o, h, low, c, v in read_minutes(paths, lo, hi):
        if float(v) <= 0:
            continue  # Fuellwert der Quelle: kein Kurs
        times.append(t)
        opens.append(float(o))
        highs.append(float(h))
        lows.append(float(low))
        closes.append(float(c))
    index = {t: i for i, t in enumerate(times)}
    out: List[HoldStats] = []
    for k in holds:
        moves: List[float] = []
        best: List[float] = []
        for i in range(0, len(times), stride):
            j = index.get(times[i] + (k - 1) * 60)
            if j is None or j - i != k - 1:
                continue  # Luecke im Zeitraum: nur vollstaendig gehandelte Fenster
            entry = opens[i]
            move = (closes[j] / entry - 1) * 100
            up = (max(highs[i:j + 1]) / entry - 1) * 100
            down = (1 - min(lows[i:j + 1]) / entry) * 100
            moves.append(move)
            best.append((up + down) / 2)
        absolute = [abs(m) for m in moves]
        share_best = {name: (sum(1 for b in best if b > cost) / len(best) if best else 0.0)
                      for name, cost in hurdles.items()}
        share_close = {name: (sum(1 for m in absolute if m > cost) / len(absolute) if absolute else 0.0)
                       for name, cost in hurdles.items()}
        out.append(HoldStats(k, len(moves), _percentile(absolute, 0.5), _percentile(absolute, 0.9),
                             _percentile(best, 0.5), share_best, share_close))
    return out, len(times)


__all__ = ["HOLDS", "HoldStats", "analyse", "round_trip_pct"]
