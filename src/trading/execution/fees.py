"""Gebuehrenstufen nach Handelsvolumen (AP-13): Viele Boersen senken Maker-/Taker-Gebuehren ab einem bestimmten
30-Tage-Volumen. `FeeSchedule.costs_for` liefert das Kostenmodell der passenden Stufe (Slippage usw. bleiben)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Tuple

from trading.domain.numeric import is_finite_decimal
from trading.execution.costs import CostModel


@dataclass(frozen=True)
class FeeTier:
    min_volume_30d: Decimal  # ab diesem Handelsvolumen der letzten 30 Tage (Kontowaehrung)
    maker_fee: Decimal
    taker_fee: Decimal


@dataclass(frozen=True)
class FeeSchedule:
    """Stufen aufsteigend nach Volumen; die erste beginnt bei 0. Hoeheres Volumen darf nie teurer werden."""

    tiers: Tuple[FeeTier, ...]

    def __post_init__(self) -> None:
        if not self.tiers or self.tiers[0].min_volume_30d != 0:
            raise ValueError("Die erste Gebuehrenstufe muss bei Volumen 0 beginnen.")
        for t in self.tiers:
            if not all(is_finite_decimal(x) and x >= 0 for x in (t.min_volume_30d, t.maker_fee, t.taker_fee)):
                raise ValueError("Volumen und Gebuehren muessen nicht-negative Decimal-Zahlen sein.")
        for a, b in zip(self.tiers, self.tiers[1:]):
            if b.min_volume_30d <= a.min_volume_30d:
                raise ValueError("Stufen muessen nach Volumen streng aufsteigend sein.")
            if b.maker_fee > a.maker_fee or b.taker_fee > a.taker_fee:
                raise ValueError("Eine hoehere Stufe darf nicht teurer sein (Tippfehler?).")

    def tier_for(self, volume_30d: Decimal) -> FeeTier:
        if not is_finite_decimal(volume_30d) or volume_30d < 0:
            raise ValueError("Volumen ungueltig.")
        return [t for t in self.tiers if t.min_volume_30d <= volume_30d][-1]

    def costs_for(self, volume_30d: Decimal, base: CostModel) -> CostModel:
        tier = self.tier_for(volume_30d)
        return replace(base, maker_fee=tier.maker_fee, taker_fee=tier.taker_fee)


__all__ = ["FeeSchedule", "FeeTier"]
