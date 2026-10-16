"""Technische Indikatoren fuer Strategien und Marktphasen - rein, deterministisch, mit Decimal.

Jede Funktion sieht nur die uebergebenen Werte (aelteste zuerst) und rechnet fuer den LETZTEN Wert. Reichen die Daten
nicht, kommt `None` - nie ein geschaetzter oder aufgefuellter Wert. So kann keine Strategie versehentlich mit zu
kurzer Historie oder mit Daten aus der Zukunft rechnen.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from trading.domain.models import Candle

ZERO = Decimal(0)
ONE = Decimal(1)


def _check_period(n: int) -> None:
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("Periode muss eine ganze Zahl >= 1 sein, nicht %r." % (n,))


def sma(values: Sequence[Decimal], n: int) -> Optional[Decimal]:
    """Einfacher gleitender Durchschnitt der letzten `n` Werte."""
    _check_period(n)
    if len(values) < n:
        return None
    return sum(values[-n:], ZERO) / n


def ema(values: Sequence[Decimal], n: int) -> Optional[Decimal]:
    """Exponentieller Durchschnitt (Gewicht 2/(n+1)), gestartet mit dem einfachen Durchschnitt der ersten `n` Werte."""
    _check_period(n)
    if len(values) < n:
        return None
    alpha = Decimal(2) / (n + 1)
    value = sum(values[:n], ZERO) / n
    for v in values[n:]:
        value = alpha * v + (ONE - alpha) * value
    return value


def stdev(values: Sequence[Decimal], n: int) -> Optional[Decimal]:
    """Standardabweichung (Grundgesamtheit) der letzten `n` Werte."""
    mean = sma(values, n)
    if mean is None:
        return None
    var = sum(((v - mean) ** 2 for v in values[-n:]), ZERO) / n
    return var.sqrt()


def true_ranges(candles: Sequence[Candle]) -> List[Decimal]:
    """Wahre Spanne je Kerze: max(Hoch-Tief, |Hoch-Vorschluss|, |Tief-Vorschluss|); die erste ohne Vorschluss."""
    out: List[Decimal] = []
    prev_close: Optional[Decimal] = None
    for c in candles:
        tr = c.high - c.low
        if prev_close is not None:
            tr = max(tr, abs(c.high - prev_close), abs(c.low - prev_close))
        out.append(tr)
        prev_close = c.close
    return out


def atr(candles: Sequence[Candle], n: int) -> Optional[Decimal]:
    """Durchschnittliche wahre Spanne der letzten `n` Kerzen (braucht n+1 Kerzen, damit jede einen Vorschluss hat)."""
    _check_period(n)
    if len(candles) < n + 1:
        return None
    return sum(true_ranges(candles[-(n + 1):])[1:], ZERO) / n


def rsi(closes: Sequence[Decimal], n: int) -> Optional[Decimal]:
    """Relative Staerke nach Wilder (0..100) ueber alle uebergebenen Schlusskurse; braucht n+1 Werte."""
    _check_period(n)
    if len(closes) < n + 1:
        return None
    changes = [b - a for a, b in zip(closes[:-1], closes[1:])]
    gain = sum((c for c in changes[:n] if c > 0), ZERO) / n
    loss = sum((-c for c in changes[:n] if c < 0), ZERO) / n
    for c in changes[n:]:
        gain = (gain * (n - 1) + (c if c > 0 else ZERO)) / n
        loss = (loss * (n - 1) + (-c if c < 0 else ZERO)) / n
    if loss == 0:
        return Decimal(100) if gain > 0 else Decimal(50)
    return Decimal(100) - Decimal(100) / (ONE + gain / loss)


def roc(closes: Sequence[Decimal], n: int) -> Optional[Decimal]:
    """Veraenderungsrate ueber `n` Kerzen (0,05 = +5 %)."""
    _check_period(n)
    if len(closes) < n + 1 or closes[-1 - n] <= 0:
        return None
    return closes[-1] / closes[-1 - n] - ONE


def donchian(candles: Sequence[Candle], n: int) -> Optional[Tuple[Decimal, Decimal]]:
    """(hoechstes Hoch, tiefstes Tief) der `n` Kerzen VOR der letzten - der Kanal, den die letzte Kerze durchbrechen kann."""
    _check_period(n)
    if len(candles) < n + 1:
        return None
    window = candles[-(n + 1):-1]
    return max(c.high for c in window), min(c.low for c in window)


def bollinger(closes: Sequence[Decimal], n: int, k: Decimal) -> Optional[Tuple[Decimal, Decimal, Decimal]]:
    """(Mitte, oberes Band, unteres Band) = Durchschnitt +/- k Standardabweichungen der letzten `n` Schlusskurse."""
    mid = sma(closes, n)
    sd = stdev(closes, n)
    if mid is None or sd is None:
        return None
    return mid, mid + k * sd, mid - k * sd


def bandwidth(closes: Sequence[Decimal], n: int, k: Decimal) -> Optional[Decimal]:
    """Breite der Bollinger-Baender relativ zur Mitte (Mass fuer die Schwankungsbreite)."""
    bands = bollinger(closes, n, k)
    if bands is None or bands[0] <= 0:
        return None
    mid, upper, lower = bands
    return (upper - lower) / mid


def efficiency_ratio(closes: Sequence[Decimal], n: int) -> Optional[Decimal]:
    """Kaufman-Effizienz (0..1): Nettobewegung / Summe aller Einzelbewegungen der letzten `n` Kerzen.
    Nahe 1 = gerichteter Trend, nahe 0 = Seitwaerts-Gezappel."""
    _check_period(n)
    if len(closes) < n + 1:
        return None
    window = closes[-(n + 1):]
    path = sum((abs(b - a) for a, b in zip(window[:-1], window[1:])), ZERO)
    if path == 0:
        return ZERO
    return abs(window[-1] - window[0]) / path


__all__ = [
    "atr", "bandwidth", "bollinger", "donchian", "efficiency_ratio", "ema", "roc", "rsi", "sma", "stdev",
    "true_ranges",
]
