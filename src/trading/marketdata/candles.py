"""Pruefung von OHLCV-Kerzen - einzeln und als Reihe.

Grundsatz "fail closed": Eine Kerze, die formal nicht stimmt, wird nicht verarbeitet. Eine Reihe mit
Luecken, Duplikaten oder falscher Reihenfolge wird nicht fuer Backtests zugelassen.

Auffaellige, aber formal gueltige Kerzen (extreme Bewegung, kein Volumen) werden NICHT verworfen,
sondern als Warnung gemeldet: Verwirft man einen echten Crash als "Datenfehler", waeren die Ergebnisse
geschoent, weil Stops dann nie ausgeloest wuerden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, List, Optional, Sequence, Tuple

from trading.domain.instrument import InstrumentId, is_instrument
from trading.domain.models import Candle
from trading.domain.numeric import is_finite_decimal, is_positive_decimal
from trading.domain.reasons import RejectReason

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def is_aware(ts: Any) -> bool:
    return isinstance(ts, datetime) and ts.tzinfo is not None and ts.utcoffset() is not None


def candle_problems(
    candle: Any, expected: Optional[InstrumentId] = None
) -> Tuple[RejectReason, ...]:
    """Formale Pruefung EINER Kerze. Leeres Ergebnis = in Ordnung. Wirft nie eine Exception."""
    if not isinstance(candle, Candle):
        return (RejectReason.MISSING_DATA,)
    problems: List[RejectReason] = []
    if not is_instrument(candle.instrument):
        problems.append(RejectReason.INVALID_SYMBOL)
    elif expected is not None and candle.instrument != expected:
        problems.append(RejectReason.SYMBOL_MISMATCH)
    interval_ok = isinstance(candle.interval_s, int) and not isinstance(candle.interval_s, bool) \
        and candle.interval_s > 0
    if not is_aware(candle.open_time) or not interval_ok:
        problems.append(RejectReason.BAD_CANDLE)
    elif (candle.open_time - _EPOCH) % timedelta(seconds=candle.interval_s) != timedelta(0):
        problems.append(RejectReason.CANDLE_MISALIGNED)  # z. B. 12:00:30 bei 1-Minuten-Kerzen

    values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
    if any(v is None for v in values):
        problems.append(RejectReason.MISSING_DATA)
    elif not all(is_finite_decimal(v) for v in values):
        problems.append(RejectReason.NON_FINITE_VALUE)
    else:
        o, h, lo, c = candle.open, candle.high, candle.low, candle.close
        if not all(is_positive_decimal(v) for v in (o, h, lo, c)):
            problems.append(RejectReason.NON_POSITIVE_PRICE)
        elif not (h >= max(o, c) and lo <= min(o, c) and h >= lo) or candle.volume < 0:
            problems.append(RejectReason.BAD_CANDLE)
    return tuple(problems)


class IssueKind(str, Enum):
    INVALID = "invalid"  # formal fehlerhafte Kerze
    MIXED_SERIES = "mixed_series"  # anderes Instrument oder anderes Intervall in derselben Reihe
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    GAP = "gap"  # fehlende Kerze(n)
    PRICE_JUMP = "price_jump"  # Warnung
    NO_VOLUME = "no_volume"  # Warnung


_WARNINGS = frozenset({IssueKind.PRICE_JUMP, IssueKind.NO_VOLUME})


@dataclass(frozen=True)
class SeriesIssue:
    kind: IssueKind
    index: int
    time: Optional[datetime]
    detail: str

    @property
    def is_error(self) -> bool:
        return self.kind not in _WARNINGS


@dataclass(frozen=True)
class SeriesReport:
    count: int
    issues: Tuple[SeriesIssue, ...]

    @property
    def errors(self) -> Tuple[SeriesIssue, ...]:
        return tuple(i for i in self.issues if i.is_error)

    @property
    def warnings(self) -> Tuple[SeriesIssue, ...]:
        return tuple(i for i in self.issues if not i.is_error)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_series(candles: Sequence[Candle], max_price_jump: Decimal) -> SeriesReport:
    """Prueft eine Kerzenreihe EINES Instruments und EINES Intervalls vollstaendig.

    Fehler: formal ungueltig, gemischte Reihe (auch anderes Intervall), doppelt, falsche Reihenfolge, Luecke.
    Warnungen: Kurssprung ueber `max_price_jump` zur Vorkerze, Kerze ohne Volumen."""
    issues: List[SeriesIssue] = []
    prev: Optional[Candle] = None
    first: Optional[Candle] = None
    for i, c in enumerate(candles):
        problems = candle_problems(c)
        if problems:
            when = c.open_time if isinstance(c, Candle) and is_aware(c.open_time) else None
            issues.append(SeriesIssue(IssueKind.INVALID, i, when, ",".join(p.value for p in problems)))
            continue
        if first is None:
            first = c
        elif c.instrument != first.instrument or c.interval_s != first.interval_s:
            issues.append(SeriesIssue(IssueKind.MIXED_SERIES, i, c.open_time,
                                      "%s/%ss statt %s/%ss" % (c.instrument, c.interval_s,
                                                               first.instrument, first.interval_s)))
            continue
        if prev is not None:
            if c.open_time == prev.open_time:
                issues.append(SeriesIssue(IssueKind.DUPLICATE, i, c.open_time, "gleiche Startzeit wie die Vorkerze"))
                continue
            if c.open_time < prev.open_time:
                issues.append(SeriesIssue(IssueKind.OUT_OF_ORDER, i, c.open_time, "aelter als die Vorkerze"))
                continue
            # Eine teilweise Ueberlappung ist hier ausgeschlossen: Alle Kerzen haben dasselbe Intervall und
            # liegen auf dessen Zeitraster (siehe candle_problems) - sie sind also gleich, aelter oder spaeter.
            if c.open_time > prev.close_time:
                missing = (c.open_time - prev.close_time) // timedelta(seconds=c.interval_s)
                issues.append(SeriesIssue(IssueKind.GAP, i, c.open_time, "%d fehlende Kerze(n)" % missing))
            if max(abs(c.high - prev.close), abs(c.low - prev.close)) / prev.close > max_price_jump:
                issues.append(SeriesIssue(IssueKind.PRICE_JUMP, i, c.open_time,
                                          "Bewegung > %s %% zur Vorkerze" % (max_price_jump * 100)))
        if c.volume == 0:
            issues.append(SeriesIssue(IssueKind.NO_VOLUME, i, c.open_time, "kein Handel"))
        prev = c
    return SeriesReport(len(candles), tuple(issues))
