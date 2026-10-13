"""Strenge Zahlenpruefung. Nur endliche Decimal-Werte sind gueltig (kein float, kein NaN/Infinity)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def is_finite_decimal(x: Any) -> bool:
    return isinstance(x, Decimal) and x.is_finite()


def is_positive_decimal(x: Any) -> bool:
    return is_finite_decimal(x) and x > 0
