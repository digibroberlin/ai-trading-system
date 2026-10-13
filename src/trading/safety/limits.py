"""Unveraenderliche Sicherheitslimits.

Zwei Schutzebenen:
1. Die HARD_*-Konstanten sind die absolute Grenze. Sie stehen im Code und werden nur
   von einem Menschen per Code-Aenderung (mit Review) angepasst - nie zur Laufzeit,
   nie durch Strategien, nie durch KI-Ausgaben.
2. `SafetyLimits` ist ein eingefrorenes Objekt (frozen dataclass). Es kann nur
   *strenger* als die Grenzen konfiguriert werden; alles andere wirft einen Fehler.

Alle Grenzen gelten GLEICHZEITIG. Eine Order wird nur zugelassen, wenn der Zustand NACH der Order
jede einzelne Grenze einhaelt (siehe trading.safety.order_risk).

Agenten und Strategien erhalten KEINEN Zugriff auf Limits - nur der Risikomanager,
der Validator und der Executor kennen sie.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

# --- absolute Grenzen (nur per Code-Aenderung durch einen Menschen anpassbar) ---
HARD_MAX_PORTFOLIO_RISK = Decimal("0.02")  # 2 % geplantes Gesamtrisiko aller offenen Positionen
HARD_MAX_RISK_PER_TRADE = Decimal("0.005")  # 0,5 % je Trade
HARD_MAX_GROSS_EXPOSURE = Decimal("1.00")  # 100 % Brutto-Exposure (Summe aller Positionswerte / Equity)
HARD_MIN_STRESS_SHOCK = Decimal("0.10")  # simulierter Kursschock gegen JEDE Position: mindestens 10 %
HARD_MAX_STRESS_LOSS = Decimal("0.05")  # ... darf hoechstens 5 % der Equity kosten
HARD_MAX_LEVERAGE = Decimal("5")
HARD_MAX_OPEN_POSITIONS = 5
HARD_MAX_DAILY_LOSS = Decimal("0.03")  # ab 3 % Tagesverlust: keine neuen Trades
HARD_MAX_DRAWDOWN = Decimal("0.10")  # ab 10 % Drawdown vom Hoechststand: keine neuen Trades
HARD_MAX_DATA_AGE_S = 15  # aeltere Marktdaten gelten als veraltet
HARD_MAX_MARK_AGE_S = 900  # aeltere Bewertungskurse offener Positionen: Equity "nicht bestimmbar"
HARD_MAX_SPREAD_BPS = Decimal("50")
HARD_MAX_PRICE_JUMP = Decimal("0.20")  # >20 % Sprung zum letzten aktuellen Kurs = verdaechtige Daten
MIN_LIQUIDATION_BUFFER = Decimal("1.25")  # Liquidation muss mind. 1,25x so weit weg sein wie der Stop
MIN_MAINTENANCE_MARGIN_RATE = Decimal("0.005")


class LimitViolationError(ValueError):
    """Ein Limit wurde weniger streng als erlaubt konfiguriert."""


@dataclass(frozen=True)
class SafetyLimits:
    max_portfolio_risk: Decimal = HARD_MAX_PORTFOLIO_RISK
    max_risk_per_trade: Decimal = HARD_MAX_RISK_PER_TRADE
    max_gross_exposure: Decimal = HARD_MAX_GROSS_EXPOSURE
    stress_shock: Decimal = HARD_MIN_STRESS_SHOCK
    max_stress_loss: Decimal = HARD_MAX_STRESS_LOSS
    max_leverage: Decimal = HARD_MAX_LEVERAGE
    max_open_positions: int = HARD_MAX_OPEN_POSITIONS
    max_daily_loss: Decimal = HARD_MAX_DAILY_LOSS
    max_drawdown: Decimal = HARD_MAX_DRAWDOWN
    max_data_age_s: int = HARD_MAX_DATA_AGE_S
    max_mark_age_s: int = HARD_MAX_MARK_AGE_S
    max_spread_bps: Decimal = HARD_MAX_SPREAD_BPS
    max_price_jump: Decimal = HARD_MAX_PRICE_JUMP
    liquidation_buffer: Decimal = MIN_LIQUIDATION_BUFFER
    maintenance_margin_rate: Decimal = MIN_MAINTENANCE_MARGIN_RATE

    def __post_init__(self) -> None:
        # (Name, Obergrenze) -> Wert muss > 0 und <= Obergrenze sein
        upper = (
            ("max_portfolio_risk", HARD_MAX_PORTFOLIO_RISK),
            ("max_risk_per_trade", HARD_MAX_RISK_PER_TRADE),
            ("max_gross_exposure", HARD_MAX_GROSS_EXPOSURE),
            ("max_stress_loss", HARD_MAX_STRESS_LOSS),
            ("max_leverage", HARD_MAX_LEVERAGE),
            ("max_open_positions", HARD_MAX_OPEN_POSITIONS),
            ("max_daily_loss", HARD_MAX_DAILY_LOSS),
            ("max_drawdown", HARD_MAX_DRAWDOWN),
            ("max_data_age_s", HARD_MAX_DATA_AGE_S),
            ("max_mark_age_s", HARD_MAX_MARK_AGE_S),
            ("max_spread_bps", HARD_MAX_SPREAD_BPS),
            ("max_price_jump", HARD_MAX_PRICE_JUMP),
        )
        for name, cap in upper:
            value = getattr(self, name)
            if value <= 0:
                raise LimitViolationError("%s muss positiv sein (ist %s)." % (name, value))
            if value > cap:
                raise LimitViolationError(
                    "%s=%s ueberschreitet die absolute Obergrenze %s." % (name, value, cap)
                )
        if self.max_risk_per_trade > self.max_portfolio_risk:
            raise LimitViolationError("Risiko je Trade darf das Gesamtrisiko nicht uebersteigen.")
        # Diese gelten in die andere Richtung: groesser = strenger.
        if not (HARD_MIN_STRESS_SHOCK <= self.stress_shock < 1):
            raise LimitViolationError("stress_shock muss zwischen 10 % und 100 % liegen.")
        if self.liquidation_buffer < MIN_LIQUIDATION_BUFFER:
            raise LimitViolationError("liquidation_buffer unter dem Mindestwert.")
        if self.maintenance_margin_rate < MIN_MAINTENANCE_MARGIN_RATE:
            raise LimitViolationError("maintenance_margin_rate unter dem Mindestwert.")

    def tightened(self, **overrides: Any) -> "SafetyLimits":
        """Neue Limits mit strengeren Werten. Lockerung -> LimitViolationError."""
        return replace(self, **overrides)


DEFAULT_LIMITS = SafetyLimits()
