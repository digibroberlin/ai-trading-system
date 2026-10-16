"""Zeitliche Aufteilung der Daten (AP-18): Training, Validierung und Test strikt nacheinander, nie gemischt.

  * `split_periods`: ein Zeitraum -> Training | Validierung | Test (Anteile, optional mit Sperrabstand dazwischen, damit
    nichts ueber Positionen oder Indikatoren von einem Abschnitt in den naechsten "durchsickert").
  * `walk_forward`: rollierende Folge von (Training, Test)-Fenstern; jedes Testfenster liegt NACH seinem Training, die
    Testfenster ueberlappen sich nicht.
Alle Grenzen sind Zeitpunkte mit Zeitzone; die Daten selbst schneidet `MarketDataset.window` zu.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional

from trading.marketdata.candles import is_aware

D = Decimal
ZERO_TIME = timedelta(0)


@dataclass(frozen=True)
class Period:
    name: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if not (is_aware(self.start) and is_aware(self.end)) or self.end <= self.start:
            raise ValueError("Zeitraum %r ungueltig (mit Zeitzone, Ende nach Beginn)." % self.name)


@dataclass(frozen=True)
class DataSplit:
    train: Period
    validation: Period
    test: Period


@dataclass(frozen=True)
class Fold:
    index: int
    train: Period
    test: Period


def split_periods(start: datetime, end: datetime, train: Decimal = D("0.6"), validation: Decimal = D("0.2"),
                  gap: timedelta = ZERO_TIME) -> DataSplit:
    """Training | Validierung | Test in dieser Reihenfolge; der Rest nach Training und Validierung ist Test."""
    if not (D(0) < train and D(0) < validation and train + validation < D(1)):
        raise ValueError("Anteile: 0 < train, 0 < validation, train + validation < 1.")
    if gap < ZERO_TIME:
        raise ValueError("Sperrabstand darf nicht negativ sein.")
    total = end - start
    usable = total - 2 * gap
    if usable <= ZERO_TIME:
        raise ValueError("Zeitraum zu kurz fuer den Sperrabstand.")
    t_end = start + usable * float(train)
    v_start = t_end + gap
    v_end = v_start + usable * float(validation)
    return DataSplit(Period("train", start, t_end), Period("validation", v_start, v_end),
                     Period("test", v_end + gap, end))


def walk_forward(start: datetime, end: datetime, train: timedelta, test: timedelta, step: Optional[timedelta] = None,
                 gap: timedelta = ZERO_TIME) -> List[Fold]:
    """Rollierende Fenster: Training [s, s+train), Test [s+train+gap, s+train+gap+test); weiter um `step` (Standard =
    Testlaenge, damit sich Testfenster nie ueberlappen)."""
    step = step or test
    if train <= ZERO_TIME or test <= ZERO_TIME or step < test or gap < ZERO_TIME:
        raise ValueError("Fenster: train, test > 0; step >= test (keine ueberlappenden Tests); gap >= 0.")
    folds: List[Fold] = []
    cursor = start
    while cursor + train + gap + test <= end:
        t_end = cursor + train
        folds.append(Fold(len(folds), Period("train-%d" % len(folds), cursor, t_end),
                          Period("test-%d" % len(folds), t_end + gap, t_end + gap + test)))
        cursor += step
    if not folds:
        raise ValueError("Zeitraum zu kurz fuer ein einziges Fenster.")
    return folds


__all__ = ["DataSplit", "Fold", "Period", "split_periods", "walk_forward"]
