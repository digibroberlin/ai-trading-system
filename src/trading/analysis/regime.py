"""Marktphasen-Erkennung (AP-17): Trend oder Seitwaerts, ruhig oder unruhig - regelbasiert, ohne KI.

Grundlage fuer KIBO und YUZU (und spaeter fuer Strategie-Auswahl und Ereignisse der Laufzeit-Engine):
  * Trend: Kaufman-Effizienz (gerichtete Bewegung statt Gezappel) UND Steigung des exponentiellen Durchschnitts UND
    Kurs auf der richtigen Seite des Durchschnitts. Sonst: seitwaerts.
  * Volatilitaet: ATR in Prozent des Kurses, eingeordnet gegen die eigene juengere Geschichte (Rang 0..1).
Zu wenig Daten -> `None` (keine geratene Phase).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Sequence

from trading.domain.models import Candle
from trading.analysis import indicators as ind

D = Decimal


class Trend(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"


class Volatility(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


@dataclass(frozen=True)
class RegimeConfig:
    trend_period: int = 50  # exponentieller Durchschnitt fuer Richtung und Steigung
    slope_bars: int = 5  # Steigung = Veraenderung des Durchschnitts ueber so viele Kerzen
    efficiency_period: int = 20
    trend_efficiency: Decimal = D("0.3")  # ab hier gilt die Bewegung als gerichtet
    atr_period: int = 14
    volatility_lookback: int = 100  # Vergleichsfenster fuer den Rang der Volatilitaet
    low_rank: Decimal = D("0.2")
    high_rank: Decimal = D("0.8")

    def __post_init__(self) -> None:
        for name in ("trend_period", "slope_bars", "efficiency_period", "atr_period", "volatility_lookback"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError("%s muss eine ganze Zahl >= 1 sein." % name)
        for name in ("trend_efficiency", "low_rank", "high_rank"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or not D(0) <= value <= D(1):
                raise ValueError("%s muss zwischen 0 und 1 liegen." % name)
        if self.low_rank >= self.high_rank:
            raise ValueError("low_rank muss kleiner als high_rank sein.")

    @property
    def min_candles(self) -> int:
        return max(self.trend_period + self.slope_bars, self.efficiency_period + 1,
                   self.atr_period + self.volatility_lookback)


DEFAULT_REGIME = RegimeConfig()


@dataclass(frozen=True)
class MarketRegime:
    trend: Trend
    volatility: Volatility
    efficiency: Decimal  # 0..1
    slope: Decimal  # relative Steigung des Durchschnitts ueber `slope_bars` Kerzen (0,01 = +1 %)
    atr_pct: Decimal  # ATR in Prozent des Kurses (0,02 = 2 %)
    volatility_rank: Decimal  # Rang der aktuellen Volatilitaet im Vergleichsfenster (0 = ruhigste, 1 = wildeste)

    def as_payload(self) -> Dict[str, str]:
        """Kompakte, strukturierte Form (z. B. als Eingabe einer KI-Analyse)."""
        return {"trend": self.trend.value, "volatility": self.volatility.value,
                "efficiency": str(round(self.efficiency, 3)), "slope": str(round(self.slope, 4)),
                "atr_pct": str(round(self.atr_pct, 4)), "volatility_rank": str(round(self.volatility_rank, 2))}


def _atr_pct_series(candles: Sequence[Candle], period: int, count: int) -> Optional[List[Decimal]]:
    out: List[Decimal] = []
    for end in range(len(candles) - count + 1, len(candles) + 1):
        window = candles[:end]
        value = ind.atr(window, period)
        close = window[-1].close
        if value is None or close <= 0:
            return None
        out.append(value / close)
    return out


def detect_regime(candles: Sequence[Candle], config: RegimeConfig = DEFAULT_REGIME) -> Optional[MarketRegime]:
    """Marktphase nach der letzten (abgeschlossenen) Kerze - oder None bei zu wenig Daten."""
    if len(candles) < config.min_candles:
        return None
    closes = [c.close for c in candles]
    efficiency = ind.efficiency_ratio(closes, config.efficiency_period)
    now, before = ind.ema(closes, config.trend_period), ind.ema(closes[:-config.slope_bars], config.trend_period)
    vols = _atr_pct_series(candles, config.atr_period, config.volatility_lookback)
    if efficiency is None or now is None or before is None or before <= 0 or not vols:
        return None
    slope = now / before - 1
    price = closes[-1]
    if efficiency >= config.trend_efficiency and slope > 0 and price > now:
        trend = Trend.UP
    elif efficiency >= config.trend_efficiency and slope < 0 and price < now:
        trend = Trend.DOWN
    else:
        trend = Trend.SIDEWAYS
    current = vols[-1]
    earlier = vols[:-1]
    # Mittlerer Rang: Gleichstaende zaehlen halb - eine gleichbleibende Volatilitaet liegt so in der Mitte (0,5)
    rank = ((D(sum(1 for v in earlier if v < current)) + D(sum(1 for v in earlier if v == current)) / 2)
            / D(len(earlier)) if earlier else D("0.5"))
    if rank <= config.low_rank:
        volatility = Volatility.LOW
    elif rank >= config.high_rank:
        volatility = Volatility.HIGH
    else:
        volatility = Volatility.NORMAL
    return MarketRegime(trend, volatility, efficiency, slope, current, rank)


__all__ = ["DEFAULT_REGIME", "MarketRegime", "RegimeConfig", "Trend", "Volatility", "detect_regime"]
