"""Lohnt sich eine teurere Analyse? Empirischer Vergleich zweier Entscheidungsmethoden NACH ihren Kosten.

Fragestellung (Vorgabe des Eigners): Liefern zusaetzliche KI-Analysen nachweislich bessere Handelsentscheidungen als
guenstigere Alternativen (Regeln, kleineres Modell)? Der Nutzen wird gemessen, nie vorausgesetzt.

Vorgehen (gepaarter Vergleich):
* Beide Methoden bewerten DIESELBEN Setups (z. B. im Backtest oder im Schattenbetrieb: die teurere Methode entscheidet
  nur auf dem Papier mit). Je Setup: Handelsergebnis der Entscheidung (nach Gebuehren/Funding; 0, wenn nicht gehandelt)
  minus Analysekosten dieser Methode. Mehrere Analysen je Setup werden vorher aufsummiert; Setups ohne Trade zaehlen mit
  (ihre Analysekosten sind echte Kosten).
* Kennzahl: mittlerer Unterschied (teuer - guenstig) je Setup, mit Bootstrap-Vertrauensbereich (fester Startwert,
  reproduzierbar).
* Entscheidung: Die teurere Methode wird nur bevorzugt, wenn die UNTERE Grenze des Vertrauensbereichs ueber 0 liegt.
  Sonst - auch bei "vergleichbar" oder zu wenig Daten - gewinnt die guenstigere. Im Zweifel: billiger.

Grenzen: Ein Backtest-Vorteil ist kein Beweis fuer die Zukunft; jede Variante, die man ausprobiert, erhoeht das Risiko
eines Zufallstreffers (Zaehlung der Versuche im Forschungsrahmen, Arbeitspaket AP-18).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import List, Mapping

from trading.domain.numeric import is_finite_decimal

ZERO = Decimal(0)


class Verdict(str, Enum):
    EXPENSIVE_PAYS_OFF = "TEURER_LOHNT"
    PREFER_CHEAPER = "GUENSTIGER_BEVORZUGEN"
    TOO_LITTLE_DATA = "ZU_WENIG_DATEN"  # gilt wie "guenstiger bevorzugen"


@dataclass(frozen=True)
class DecisionOutcome:
    """Ergebnis einer Methode fuer ein Setup."""

    trade_net_pnl: Decimal  # nach Handelskosten; 0, wenn die Methode nicht gehandelt hat
    analysis_cost: Decimal  # Summe aller Analysen dieser Methode fuer dieses Setup

    def __post_init__(self) -> None:
        if not is_finite_decimal(self.trade_net_pnl) or not is_finite_decimal(self.analysis_cost) \
                or self.analysis_cost < 0:
            raise ValueError("Ergebnis ungueltig.")

    @property
    def net(self) -> Decimal:
        return self.trade_net_pnl - self.analysis_cost


@dataclass(frozen=True)
class MethodComparison:
    samples: int
    mean_net_cheap: Decimal
    mean_net_expensive: Decimal
    mean_difference: Decimal  # teuer - guenstig, je Setup, nach Analysekosten
    ci_low: Decimal
    ci_high: Decimal
    extra_analysis_cost: Decimal  # Mehrkosten der teureren Methode insgesamt
    verdict: Verdict


def compare_methods(
    cheap: Mapping[str, DecisionOutcome],
    expensive: Mapping[str, DecisionOutcome],
    min_samples: int = 30,
    resamples: int = 2000,
    confidence: Decimal = Decimal("0.95"),
    seed: int = 7,
) -> MethodComparison:
    if set(cheap) != set(expensive):
        raise ValueError("Beide Methoden muessen DIESELBEN Setups bewerten (gepaarter Vergleich).")
    if not ZERO < confidence < 1 or resamples < 100:
        raise ValueError("Vertrauensniveau zwischen 0 und 1, mindestens 100 Wiederholungen.")
    keys = sorted(cheap)
    n = len(keys)
    diffs: List[Decimal] = [expensive[k].net - cheap[k].net for k in keys]
    extra = sum((expensive[k].analysis_cost - cheap[k].analysis_cost for k in keys), ZERO)
    if n == 0:
        return MethodComparison(0, ZERO, ZERO, ZERO, ZERO, ZERO, ZERO, Verdict.TOO_LITTLE_DATA)
    mean_cheap = sum((cheap[k].net for k in keys), ZERO) / n
    mean_exp = sum((expensive[k].net for k in keys), ZERO) / n
    mean_diff = sum(diffs, ZERO) / n
    rng = random.Random(seed)
    means = sorted(sum((diffs[rng.randrange(n)] for _ in range(n)), ZERO) / n for _ in range(resamples))
    tail = (1 - confidence) / 2
    low = means[int(tail * resamples)]
    high = means[min(resamples - 1, int((1 - tail) * resamples))]
    if n < min_samples:
        verdict = Verdict.TOO_LITTLE_DATA
    elif low > 0:
        verdict = Verdict.EXPENSIVE_PAYS_OFF
    else:
        verdict = Verdict.PREFER_CHEAPER
    return MethodComparison(n, mean_cheap, mean_exp, mean_diff, low, high, extra, verdict)


__all__ = ["DecisionOutcome", "MethodComparison", "Verdict", "compare_methods"]
