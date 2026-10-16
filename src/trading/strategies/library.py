"""Strategie-Bibliothek (AP-16): Trendfolge, Momentum, Mean Reversion, Breakout, Volatilitaet, Scalping.

Alle Strategien sind regelbasiert, deterministisch und sehen nur `MarketView` (abgeschlossene Kerzen bis jetzt und die
eigenen Positionen). Sie SCHLAGEN VOR - ob gehandelt wird, entscheiden RIN, SORA, KIBO, YUZU, der Risikomanager und
der Executor. Keine Strategie ist optimiert oder als profitabel belegt; das prueft erst der Forschungsrahmen (AP-18)
auf getrennten Daten, nach allen Kosten (AP-19/20).

Gemeinsame Regeln:
  * je Instrument in fester Reihenfolge; mit offener Position wird nur ueber den Ausstieg entschieden;
  * Short nur bei Futures und nur, wenn `allow_short`;
  * Stop-Abstand = max(stop_atr x ATR, min_stop_pct x Kurs) - nie enger als das Rauschen, nie Null;
  * Ziel = Einstieg +/- reward_risk x Stop-Abstand (einzelne Strategien setzen ein fachliches Ziel, z. B. die Mitte);
  * zu kurze Historie: kein Signal (Indikatoren liefern dann `None`).
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import List, Optional, Sequence, Tuple

from trading.backtest.strategy import EntryProposal, ExitProposal, MarketView, Strategy, StrategyDecision
from trading.domain.instrument import InstrumentId, MarketType
from trading.domain.models import Candle, Horizon, Position, Side
from trading.strategies import indicators as ind

D = Decimal
PRICE_STEP = D("0.00000001")  # Vorschlaege auf 8 Nachkommastellen; Boersen-Schritte je Instrument folgen mit AP-13


def _positive_int(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("%s muss eine ganze Zahl >= 1 sein, nicht %r." % (name, value))


def _positive_decimal(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError("%s muss eine positive Decimal-Zahl sein, nicht %r." % (name, value))


@dataclass(frozen=True)
class StopRules:
    """Wie weit Stop-Loss und Ziel vom Einstieg liegen."""

    atr_period: int = 14
    stop_atr: Decimal = D("2")  # Stop-Abstand in ATR
    reward_risk: Decimal = D("2.5")  # Ziel-Abstand in Stop-Abstaenden (RIN verlangt nach Kosten mind. 1,5)
    min_stop_pct: Decimal = D("0.005")  # nie enger als 0,5 % (sonst fressen Kosten und Rauschen den Stop)

    def __post_init__(self) -> None:
        _positive_int("atr_period", self.atr_period)
        for label, value in (("stop_atr", self.stop_atr), ("reward_risk", self.reward_risk),
                             ("min_stop_pct", self.min_stop_pct)):
            _positive_decimal(label, value)
        if self.reward_risk < 1:
            raise ValueError("reward_risk muss mindestens 1 sein (sonst lohnt kein Trade die Kosten).")
        if self.min_stop_pct >= D("0.5"):
            raise ValueError("min_stop_pct muss unter 0,5 liegen.")


DEFAULT_STOPS = StopRules()


@dataclass(frozen=True)
class Signal:
    side: Side
    rationale: str
    target: Optional[Decimal] = None  # fachliches Ziel; sonst reward_risk x Stop-Abstand


class RuleStrategy(Strategy):
    """Gemeinsamer Rahmen der Bibliothek. Unterklassen liefern nur `entry_signal` und `exit_reason`."""

    horizon: Horizon = Horizon.INTRADAY

    def __init__(self, name: str, min_bars: int, stops: StopRules, allow_short: bool) -> None:
        _positive_int("min_bars", min_bars)
        if not isinstance(stops, StopRules):
            raise ValueError("stops muss StopRules sein.")
        self.name, self.stops, self.allow_short = name, stops, bool(allow_short)
        self.min_bars = max(min_bars, stops.atr_period + 1)

    # ------------------------------------------------------------------ von Unterklassen

    @abstractmethod
    def entry_signal(self, candles: Sequence[Candle]) -> Optional[Signal]:
        """Einstieg nach der letzten Kerze (oder None)."""

    @abstractmethod
    def exit_reason(self, candles: Sequence[Candle], position: Position) -> Optional[str]:
        """Grund fuer einen vorzeitigen Ausstieg (oder None - Stop-Loss/Take-Profit gelten ohnehin)."""

    # ------------------------------------------------------------------ Ablauf

    def on_bar(self, view: MarketView) -> StrategyDecision:
        entries: List[EntryProposal] = []
        exits: List[ExitProposal] = []
        for instrument in sorted(view.bars, key=lambda i: i.key):
            candles = view.bars[instrument]
            if len(candles) < self.min_bars:
                continue
            position = view.position(instrument)
            if position is not None:
                reason = self.exit_reason(candles, position)
                if reason:
                    exits.append(ExitProposal(instrument, reason))
                continue
            signal = self.entry_signal(candles)
            if signal is None or (signal.side is Side.SHORT and not self._may_short(instrument)):
                continue
            proposal = self._proposal(instrument, candles, signal)
            if proposal is not None:
                entries.append(proposal)
        return StrategyDecision(tuple(entries), tuple(exits))

    def _may_short(self, instrument: InstrumentId) -> bool:
        return self.allow_short and instrument.market_type is MarketType.FUTURES

    def _proposal(self, instrument: InstrumentId, candles: Sequence[Candle], signal: Signal) -> Optional[EntryProposal]:
        price = candles[-1].close
        volatility = ind.atr(candles, self.stops.atr_period)
        if volatility is None or price <= 0:
            return None
        distance = max(volatility * self.stops.stop_atr, price * self.stops.min_stop_pct)
        sign = 1 if signal.side is Side.LONG else -1
        target = signal.target if signal.target is not None else price + sign * distance * self.stops.reward_risk
        # gerundet zu Ungunsten: Stop eher weiter weg (Risiko wird mit dem echten Stop bemessen), Ziel eher naeher dran
        rounding = ROUND_FLOOR if signal.side is Side.LONG else ROUND_CEILING
        stop = (price - sign * distance).quantize(PRICE_STEP, rounding=rounding)
        target = target.quantize(PRICE_STEP, rounding=rounding)
        if stop <= 0 or target <= 0 or (target - price) * sign <= 0:
            return None  # kein sinnvolles Ziel auf der richtigen Seite: lieber kein Vorschlag
        return EntryProposal(instrument, signal.side, stop, target, self.horizon, signal.rationale)


def _side_of(position: Position) -> int:
    return 1 if position.side is Side.LONG else -1


def _closes(candles: Sequence[Candle]) -> Tuple[Decimal, ...]:
    return tuple(c.close for c in candles)


# ====================================================================== Trendfolge


class TrendFollowingStrategy(RuleStrategy):
    """Exponentielle Durchschnitte: frisches Kreuzen des schnellen ueber den langsamen (Kurs ueber dem langsamen) ->
    Long; spiegelbildlich Short. Ausstieg, sobald der Trend dreht."""

    horizon = Horizon.SWING

    def __init__(self, fast: int = 20, slow: int = 50, *, stops: StopRules = DEFAULT_STOPS, allow_short: bool = True, name: str = "") -> None:
        _positive_int("fast", fast)
        _positive_int("slow", slow)
        if fast >= slow:
            raise ValueError("Es muss fast < slow gelten.")
        self.fast, self.slow = fast, slow
        super().__init__(name or "trend-ema%d-%d" % (fast, slow), slow + 1, stops, allow_short)

    def _up(self, closes: Sequence[Decimal]) -> Optional[bool]:
        fast, slow = ind.ema(closes, self.fast), ind.ema(closes, self.slow)
        return None if fast is None or slow is None else fast > slow

    def entry_signal(self, candles: Sequence[Candle]) -> Optional[Signal]:
        closes = _closes(candles)
        now_up, was_up = self._up(closes), self._up(closes[:-1])
        slow = ind.ema(closes, self.slow)
        if now_up is None or was_up is None or slow is None or now_up == was_up:
            return None
        if now_up and closes[-1] > slow:
            return Signal(Side.LONG, "EMA%d kreuzt EMA%d aufwaerts" % (self.fast, self.slow))
        if not now_up and closes[-1] < slow:
            return Signal(Side.SHORT, "EMA%d kreuzt EMA%d abwaerts" % (self.fast, self.slow))
        return None

    def exit_reason(self, candles: Sequence[Candle], position: Position) -> Optional[str]:
        up = self._up(_closes(candles))
        if up is not None and up != (position.side is Side.LONG):
            return "Trend gedreht"
        return None


# ====================================================================== Momentum


class MomentumStrategy(RuleStrategy):
    """Veraenderungsrate ueber `lookback` Kerzen steigt frisch ueber die Schwelle, Kurs ueber dem Trend-Durchschnitt ->
    Long (spiegelbildlich Short). Ausstieg, wenn das Momentum die Richtung verliert (Rate kreuzt 0)."""

    def __init__(self, lookback: int = 20, threshold: Decimal = D("0.03"), trend: int = 50, *,
                 stops: StopRules = DEFAULT_STOPS, allow_short: bool = True, name: str = "") -> None:
        _positive_int("lookback", lookback)
        _positive_int("trend", trend)
        _positive_decimal("threshold", threshold)
        self.lookback, self.threshold, self.trend = lookback, threshold, trend
        super().__init__(name or "momentum-%d" % lookback, max(lookback, trend) + 2, stops, allow_short)

    def entry_signal(self, candles: Sequence[Candle]) -> Optional[Signal]:
        closes = _closes(candles)
        now, before = ind.roc(closes, self.lookback), ind.roc(closes[:-1], self.lookback)
        trend = ind.sma(closes, self.trend)
        if now is None or before is None or trend is None:
            return None
        if now > self.threshold >= before and closes[-1] > trend:
            return Signal(Side.LONG, "Momentum %+.1f %% ueber %d Kerzen" % (now * 100, self.lookback))
        if now < -self.threshold <= before and closes[-1] < trend:
            return Signal(Side.SHORT, "Momentum %+.1f %% ueber %d Kerzen" % (now * 100, self.lookback))
        return None

    def exit_reason(self, candles: Sequence[Candle], position: Position) -> Optional[str]:
        rate = ind.roc(_closes(candles), self.lookback)
        if rate is not None and rate * _side_of(position) < 0:
            return "Momentum verloren"
        return None


# ====================================================================== Mean Reversion


class MeanReversionStrategy(RuleStrategy):
    """Schluss unter dem unteren Bollinger-Band UND RSI ueberverkauft -> Long mit Ziel Mitte (spiegelbildlich Short).
    Ausstieg an der Mitte. Gegen den Trend - deshalb bewusst kurze Haltedauer und fachliches Ziel."""

    def __init__(self, period: int = 20, k: Decimal = D("2"), rsi_period: int = 14, oversold: Decimal = D("30"),
                 overbought: Decimal = D("70"), *, stops: StopRules = StopRules(stop_atr=D("1.5")),
                 allow_short: bool = True, name: str = "") -> None:
        _positive_int("period", period)
        _positive_int("rsi_period", rsi_period)
        _positive_decimal("k", k)
        if not D(0) < oversold < overbought < D(100):
            raise ValueError("Es muss 0 < oversold < overbought < 100 gelten.")
        self.period, self.k, self.rsi_period = period, k, rsi_period
        self.oversold, self.overbought = oversold, overbought
        super().__init__(name or "meanrev-bb%d" % period, max(period, rsi_period + 1), stops, allow_short)

    def entry_signal(self, candles: Sequence[Candle]) -> Optional[Signal]:
        closes = _closes(candles)
        bands, strength = ind.bollinger(closes, self.period, self.k), ind.rsi(closes, self.rsi_period)
        if bands is None or strength is None:
            return None
        mid, upper, lower = bands
        if closes[-1] < lower and strength < self.oversold:
            return Signal(Side.LONG, "unter unterem Band, RSI %.0f" % strength, target=mid)
        if closes[-1] > upper and strength > self.overbought:
            return Signal(Side.SHORT, "ueber oberem Band, RSI %.0f" % strength, target=mid)
        return None

    def exit_reason(self, candles: Sequence[Candle], position: Position) -> Optional[str]:
        mid = ind.sma(_closes(candles), self.period)
        if mid is not None and (candles[-1].close - mid) * _side_of(position) >= 0:
            return "Mitte erreicht"
        return None


# ====================================================================== Breakout


class BreakoutStrategy(RuleStrategy):
    """Donchian-Ausbruch: Schluss ueber dem hoechsten Hoch der letzten `entry` Kerzen -> Long (spiegelbildlich Short).
    Ausstieg, wenn der Schluss das Gegenteil des kuerzeren `exit`-Kanals verletzt (Turtle-Prinzip)."""

    horizon = Horizon.SWING

    def __init__(self, entry: int = 20, exit: int = 10, *, stops: StopRules = DEFAULT_STOPS, allow_short: bool = True, name: str = "") -> None:
        _positive_int("entry", entry)
        _positive_int("exit", exit)
        if exit > entry:
            raise ValueError("Der Ausstiegskanal darf nicht laenger als der Einstiegskanal sein.")
        self.entry, self.exit = entry, exit
        super().__init__(name or "breakout-%d-%d" % (entry, exit), entry + 1, stops, allow_short)

    def entry_signal(self, candles: Sequence[Candle]) -> Optional[Signal]:
        channel = ind.donchian(candles, self.entry)
        if channel is None:
            return None
        high, low = channel
        close = candles[-1].close
        if close > high:
            return Signal(Side.LONG, "Ausbruch ueber %d-Kerzen-Hoch" % self.entry)
        if close < low:
            return Signal(Side.SHORT, "Ausbruch unter %d-Kerzen-Tief" % self.entry)
        return None

    def exit_reason(self, candles: Sequence[Candle], position: Position) -> Optional[str]:
        channel = ind.donchian(candles, self.exit)
        if channel is None:
            return None
        high, low = channel
        close = candles[-1].close
        if position.side is Side.LONG and close < low:
            return "unter %d-Kerzen-Tief" % self.exit
        if position.side is Side.SHORT and close > high:
            return "ueber %d-Kerzen-Hoch" % self.exit
        return None


# ====================================================================== Volatilitaet


class VolatilitySqueezeStrategy(RuleStrategy):
    """Volatilitaets-Ausbruch: Nach einer Phase ungewoehnlich enger Bollinger-Baender (Breite auf dem Tiefstand der
    letzten `lookback` Kerzen, innerhalb von `window` Kerzen) bricht der Schluss aus dem Band aus -> Einstieg in
    Ausbruchsrichtung. Ausstieg, wenn der Schluss die Mitte wieder kreuzt."""

    def __init__(self, period: int = 20, k: Decimal = D("2"), lookback: int = 60, window: int = 5, *,
                 stops: StopRules = DEFAULT_STOPS, allow_short: bool = True, name: str = "") -> None:
        for label, value in (("period", period), ("lookback", lookback), ("window", window)):
            _positive_int(label, value)
        _positive_decimal("k", k)
        if window > lookback:
            raise ValueError("window darf nicht groesser als lookback sein.")
        self.period, self.k, self.lookback, self.window = period, k, lookback, window
        super().__init__(name or "squeeze-bb%d" % period, period + lookback, stops, allow_short)

    def _squeezed_recently(self, closes: Sequence[Decimal]) -> bool:
        widths: List[Decimal] = []
        for end in range(len(closes) - self.lookback, len(closes)):  # Breite je Kerze der letzten `lookback` Kerzen
            width = ind.bandwidth(closes[: end + 1], self.period, self.k)
            if width is None:
                return False
            widths.append(width)
        lowest = min(widths)
        return any(w == lowest for w in widths[-self.window - 1:-1])  # Tiefstand kurz VOR der aktuellen Kerze

    def entry_signal(self, candles: Sequence[Candle]) -> Optional[Signal]:
        closes = _closes(candles)
        bands = ind.bollinger(closes, self.period, self.k)
        if bands is None or not self._squeezed_recently(closes):
            return None
        _, upper, lower = bands
        if closes[-1] > upper:
            return Signal(Side.LONG, "Ausbruch nach enger Phase (oberes Band)")
        if closes[-1] < lower:
            return Signal(Side.SHORT, "Ausbruch nach enger Phase (unteres Band)")
        return None

    def exit_reason(self, candles: Sequence[Candle], position: Position) -> Optional[str]:
        mid = ind.sma(_closes(candles), self.period)
        if mid is not None and (candles[-1].close - mid) * _side_of(position) < 0:
            return "zurueck ueber die Mitte"
        return None


# ====================================================================== Scalping


class ScalpingStrategy(RuleStrategy):
    """Kurzfristige Gegenbewegung im Trend (RSI(2)-Prinzip): Im Aufwaertstrend (Kurs ueber dem Trend-Durchschnitt)
    ein kurzer Ruecksetzer mit sehr niedrigem RSI -> Long (spiegelbildlich Short). Ausstieg bei Erholung oder spaetestens
    nach `max_hold` Kerzen. Hohe Kosten je Gewinn - erst nach allen Kosten bewertbar (AP-19)."""

    horizon = Horizon.SCALP

    def __init__(self, rsi_period: int = 2, entry_level: Decimal = D("10"), exit_level: Decimal = D("70"),
                 trend: int = 50, max_hold: int = 5, *,
                 stops: StopRules = StopRules(stop_atr=D("1.5"), reward_risk=D("2")), allow_short: bool = True,
                 name: str = "") -> None:
        for label, value in (("rsi_period", rsi_period), ("trend", trend), ("max_hold", max_hold)):
            _positive_int(label, value)
        if not D(0) < entry_level < exit_level < D(100):
            raise ValueError("Es muss 0 < entry_level < exit_level < 100 gelten.")
        self.rsi_period, self.entry_level, self.exit_level = rsi_period, entry_level, exit_level
        self.trend, self.max_hold = trend, max_hold
        super().__init__(name or "scalp-rsi%d" % rsi_period, max(trend, rsi_period + 1), stops, allow_short)

    def entry_signal(self, candles: Sequence[Candle]) -> Optional[Signal]:
        closes = _closes(candles)
        strength, trend = ind.rsi(closes, self.rsi_period), ind.sma(closes, self.trend)
        if strength is None or trend is None:
            return None
        if closes[-1] > trend and strength < self.entry_level:
            return Signal(Side.LONG, "Ruecksetzer im Aufwaertstrend, RSI(%d) %.0f" % (self.rsi_period, strength))
        if closes[-1] < trend and strength > 100 - self.entry_level:
            return Signal(Side.SHORT, "Erholung im Abwaertstrend, RSI(%d) %.0f" % (self.rsi_period, strength))
        return None

    def exit_reason(self, candles: Sequence[Candle], position: Position) -> Optional[str]:
        held = sum(1 for c in candles if c.open_time >= position.opened_at)
        if held >= self.max_hold:
            return "maximale Haltedauer (%d Kerzen)" % self.max_hold
        strength = ind.rsi(_closes(candles), self.rsi_period)
        if strength is None:
            return None
        if position.side is Side.LONG and strength > self.exit_level:
            return "erholt, RSI %.0f" % strength
        if position.side is Side.SHORT and strength < 100 - self.exit_level:
            return "zurueckgekommen, RSI %.0f" % strength
        return None


__all__ = [
    "DEFAULT_STOPS", "BreakoutStrategy", "MeanReversionStrategy", "MomentumStrategy", "RuleStrategy",
    "ScalpingStrategy", "Signal", "StopRules", "TrendFollowingStrategy", "VolatilitySqueezeStrategy",
]
