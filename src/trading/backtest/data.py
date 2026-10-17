"""Marktdaten fuer Backtests: gepruefte Datensaetze und die Schnittstelle fuer Datenquellen.

Ein `MarketDataset` laesst sich nur aus fehlerfreien Reihen bauen. Luecken, doppelte, falsch sortierte
oder formal fehlerhafte Kerzen fuehren zu einem `DataQualityError` mit allen Befunden - es wird nichts
stillschweigend repariert, aufgefuellt oder uebersprungen. Futures-Reihen ohne Funding-Daten werden
abgelehnt (sonst waeren Haltekosten faelschlich null).

`HistoricalDataSource` ist die Schnittstelle fuer spaetere Coinbase- und Bitget-Adapter. Sie liefert
ausschliesslich historische Marktdaten in der kanonischen Form dieses Projekts; sie hat bewusst KEINE
Methode, die eine Order senden koennte, und braucht keine Handels-Zugangsdaten.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from trading.audit.events import to_jsonable
from trading.domain.instrument import Exchange, InstrumentId, MarketType, is_instrument
from trading.domain.models import Candle, FundingEvent
from trading.domain.numeric import is_finite_decimal, is_positive_decimal
from trading.marketdata.candles import IssueKind, is_aware, validate_series
from trading.marketdata.synthetic import SyntheticMarket, funding_events
from trading.safety.limits import HARD_MAX_PRICE_JUMP


MAX_DECLARED_GAP = timedelta(hours=24)  # laengere Ausfaelle: Zeitraum anders waehlen, nie ueberbruecken


class DataQualityError(ValueError):
    """Der Datensatz ist fuer einen Backtest nicht geeignet. `issues` nennt jeden einzelnen Befund."""

    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = tuple(issues)
        shown = "\n  - ".join(self.issues[:20])
        more = "" if len(self.issues) <= 20 else "\n  ... und %d weitere" % (len(self.issues) - 20)
        super().__init__("Datensatz abgelehnt (%d Befunde):\n  - %s%s" % (len(self.issues), shown, more))


@dataclass(frozen=True)
class InstrumentSeries:
    """Kerzen (und bei Futures Funding-Abrechnungen) EINES Instruments in EINEM Intervall."""

    instrument: InstrumentId
    interval_s: int
    candles: Tuple[Candle, ...]
    funding: Tuple[FundingEvent, ...] = ()
    initial_funding_rate: Optional[Decimal] = None  # zu Beginn bekannter Satz (bis zur ersten Abrechnung)
    spread_bps: Decimal = Decimal("2")  # angenommener Bid/Ask-Spread fuer simulierte Ausfuehrungen
    # Von der Quelle AUSDRUECKLICH ausgewiesene Luecken (Beginn, Ende), z. B. Ausfall der Boerse - nur diese sind erlaubt,
    # jede andere Luecke bleibt ein Fehler. Nichts wird aufgefuellt; hoechstens MAX_DECLARED_GAP je Luecke (AP-20).
    declared_gaps: Tuple[Tuple[datetime, datetime], ...] = ()


def series_problems(series: InstrumentSeries, max_price_jump: Decimal) -> Tuple[List[str], List[str]]:
    """Vollstaendige Pruefung einer Reihe. Rueckgabe: (Fehler, Warnungen) in Klartext."""
    key = getattr(series.instrument, "key", repr(series.instrument))
    errors: List[str] = []
    warnings: List[str] = []

    def error(text: str) -> None:
        errors.append("%s: %s" % (key, text))

    if not is_instrument(series.instrument):
        error("Instrument formal ungueltig")
        return errors, warnings
    if not isinstance(series.interval_s, int) or series.interval_s <= 0:
        error("Intervall ungueltig")
    if not is_finite_decimal(series.spread_bps) or series.spread_bps < 0:
        error("Spread ungueltig")
    if not series.candles:
        error("keine Kerzen")
        return errors, warnings

    declared = set()
    for gap in series.declared_gaps:
        if (not isinstance(gap, tuple) or len(gap) != 2 or not all(is_aware(g) for g in gap) or gap[1] <= gap[0]
                or gap[1] - gap[0] > MAX_DECLARED_GAP):
            error("ausgewiesene Luecke ungueltig oder laenger als %s: %r" % (MAX_DECLARED_GAP, gap))
            continue
        declared.add(gap)
    report = validate_series(series.candles, max_price_jump)
    for issue in report.issues:
        text = "Kerze %d (%s): %s - %s" % (issue.index, issue.time, issue.kind.value, issue.detail)
        if issue.kind is IssueKind.GAP and 0 < issue.index < len(series.candles):
            gap = (series.candles[issue.index - 1].close_time, series.candles[issue.index].open_time)
            if gap in declared:
                warnings.append("%s: %s (von der Quelle ausgewiesen)" % (key, text))
                continue
        (errors if issue.is_error else warnings).append("%s: %s" % (key, text))
    for i, c in enumerate(series.candles):
        if isinstance(c, Candle) and (c.instrument != series.instrument or c.interval_s != series.interval_s):
            error("Kerze %d gehoert nicht zu dieser Reihe (%s/%ss)" % (i, c.instrument, c.interval_s))
            break

    futures = series.instrument.market_type is MarketType.FUTURES
    if not futures:
        if series.funding or series.initial_funding_rate is not None:
            error("Funding-Daten bei einem Spot-Instrument")
        return errors, warnings
    if not series.funding:
        error("Futures ohne Funding-Daten (Haltekosten waeren faelschlich null)")
    if series.initial_funding_rate is not None and not is_finite_decimal(series.initial_funding_rate):
        error("Anfangs-Funding-Satz ungueltig")
    last_close = series.candles[-1].close_time if isinstance(series.candles[-1], Candle) else None
    previous: Optional[datetime] = None
    for i, ev in enumerate(series.funding):
        if not isinstance(ev, FundingEvent) or ev.instrument != series.instrument:
            error("Funding %d gehoert nicht zu dieser Reihe" % i)
            continue
        if not is_aware(ev.time) or not is_finite_decimal(ev.rate) or not is_positive_decimal(ev.mark_price):
            error("Funding %d formal ungueltig" % i)
            continue
        if previous is not None and ev.time <= previous:
            error("Funding %d (%s) ist doppelt oder falsch sortiert" % (i, ev.time))
        if last_close is not None and is_aware(last_close) and ev.time > last_close:
            error("Funding %d (%s) liegt nach dem Ende der Kerzen" % (i, ev.time))
        previous = ev.time
    return errors, warnings


@dataclass(frozen=True)
class MarketDataset:
    """Gepruefter, unveraenderlicher Datensatz. Nur ueber `MarketDataset.build` zu erzeugen."""

    series: Tuple[InstrumentSeries, ...]
    warnings: Tuple[str, ...]
    fingerprint: str  # aendert sich, sobald sich irgendein Wert im Datensatz aendert

    @staticmethod
    def build(
        series: Sequence[InstrumentSeries], max_price_jump: Decimal = HARD_MAX_PRICE_JUMP
    ) -> "MarketDataset":
        errors: List[str] = []
        warnings: List[str] = []
        if not series:
            errors.append("Datensatz ist leer")
        seen: Dict[InstrumentId, int] = {}
        for s in series:
            if not isinstance(s, InstrumentSeries):
                errors.append("Eintrag ist keine InstrumentSeries")
                continue
            if isinstance(s.instrument, InstrumentId):
                seen[s.instrument] = seen.get(s.instrument, 0) + 1
            e, w = series_problems(s, max_price_jump)
            errors += e
            warnings += w
        errors += ["%s: Instrument mehrfach im Datensatz" % i.key for i, n in seen.items() if n > 1]
        if errors:
            raise DataQualityError(errors)
        ordered = tuple(sorted(series, key=lambda s: s.instrument.key))
        payload = json.dumps(to_jsonable(ordered), sort_keys=True, separators=(",", ":"))
        return MarketDataset(ordered, tuple(warnings), hashlib.sha256(payload.encode("utf-8")).hexdigest())

    @property
    def instruments(self) -> Tuple[InstrumentId, ...]:
        return tuple(s.instrument for s in self.series)

    @property
    def start(self) -> datetime:
        return min(s.candles[0].open_time for s in self.series)

    @property
    def end(self) -> datetime:
        return max(s.candles[-1].close_time for s in self.series)

    def truncated(self, until: datetime) -> "MarketDataset":
        """Derselbe Datensatz, abgeschnitten bei `until` (nur Kerzen, die bis dahin geschlossen haben)."""
        cut = []
        for s in self.series:
            candles = tuple(c for c in s.candles if c.close_time <= until)
            if candles:
                funding = tuple(f for f in s.funding if f.time <= candles[-1].close_time)
                cut.append(InstrumentSeries(s.instrument, s.interval_s, candles, funding,
                                            s.initial_funding_rate, s.spread_bps, _gaps_within(s, candles)))
        return MarketDataset.build(cut)

    def window(self, start: datetime, end: datetime) -> "MarketDataset":
        """Nur Kerzen, die ab `start` beginnen und bis `end` schliessen (z. B. Trainings-, Validierungs-, Testzeitraum).
        Bei Futures gilt als Anfangs-Funding der letzte VOR dem Zeitraum bekannte Satz - nichts aus dem Zeitraum selbst."""
        if not (is_aware(start) and is_aware(end)) or end <= start:
            raise ValueError("Zeitraum ungueltig (mit Zeitzone, Ende nach Beginn).")
        cut = []
        for s in self.series:
            candles = tuple(c for c in s.candles if c.open_time >= start and c.close_time <= end)
            if not candles:
                continue
            funding = tuple(f for f in s.funding if candles[0].open_time < f.time <= candles[-1].close_time)
            known = [f.rate for f in s.funding if f.time <= candles[0].open_time]
            initial = known[-1] if known else s.initial_funding_rate
            cut.append(InstrumentSeries(s.instrument, s.interval_s, candles, funding, initial, s.spread_bps,
                                        _gaps_within(s, candles)))
        if not cut:
            raise ValueError("Keine Kerzen im Zeitraum.")
        return MarketDataset.build(cut)


def _gaps_within(series: InstrumentSeries, candles: Tuple[Candle, ...]) -> Tuple[Tuple[datetime, datetime], ...]:
    """Ausgewiesene Luecken, die ganz zwischen der ersten und der letzten der ausgewaehlten Kerzen liegen."""
    return tuple(g for g in series.declared_gaps if candles[0].close_time <= g[0] and g[1] <= candles[-1].open_time)


class HistoricalDataSource(ABC):
    """Liefert historische Marktdaten EINER Boerse in der kanonischen Form (nur lesend).

    Fuer Coinbase und Bitget kommen spaeter eigene Unterklassen. Sie muessen:
      * boerseneigene Symbole in die kanonische Schreibweise uebersetzen ("BTCUSDT" -> "BTC-USDT"),
      * Zeitstempel als UTC mit Zeitzone liefern,
      * bei Futures die Funding-Abrechnungen samt Kurs mitliefern,
      * nichts auffuellen oder glaetten - Luecken bleiben Luecken und werden von `MarketDataset.build` gemeldet.
    """

    exchange: ClassVar[Exchange]

    @abstractmethod
    def load(
        self, instrument: InstrumentId, interval_s: int, start: datetime, end: datetime
    ) -> InstrumentSeries:
        """Kerzen mit open_time >= start und close_time <= end."""


@dataclass(frozen=True)
class SyntheticSpec:
    """Eigenschaften eines erfundenen Marktes."""

    start_price: Decimal = Decimal("100")
    drift: float = 0.0
    volatility: float = 0.003
    spread_bps: Decimal = Decimal("2")
    funding_rate: Decimal = Decimal("0.0001")  # nur Futures
    funding_interval_hours: int = 8
    tick: Decimal = Decimal("0.01")


@dataclass(frozen=True)
class SyntheticDataSource(HistoricalDataSource):
    """Erfundene, vollstaendig reproduzierbare Marktdaten (gleicher `seed` = gleiche Kerzen)."""

    seed: int = 1
    specs: Mapping[InstrumentId, SyntheticSpec] = field(default_factory=dict)
    exchange: ClassVar[Exchange] = Exchange.SYNTHETIC

    def load(
        self, instrument: InstrumentId, interval_s: int, start: datetime, end: datetime
    ) -> InstrumentSeries:
        if instrument.exchange is not Exchange.SYNTHETIC:
            raise ValueError("SyntheticDataSource liefert nur Instrumente der synthetischen Boerse.")
        spec = self.specs.get(instrument, SyntheticSpec())
        # Eigener, stabiler Zufallsstrom je Instrument (unabhaengig von Python-Hash und Reihenfolge).
        digest = hashlib.sha256(("%d|%s" % (self.seed, instrument.key)).encode("utf-8")).digest()
        market = SyntheticMarket(
            symbol=instrument.symbol, start_price=spec.start_price, seed=int.from_bytes(digest[:8], "big"),
            start_time=start, interval_s=interval_s, drift=spec.drift, volatility=spec.volatility,
            spread_bps=spec.spread_bps, market_type=instrument.market_type, tick=spec.tick,
        )
        count = int((end - start) // timedelta(seconds=interval_s))
        candles = tuple(market.candles(count))
        if instrument.market_type is MarketType.FUTURES:
            funding = tuple(funding_events(candles, spec.funding_rate, spec.funding_interval_hours))
            return InstrumentSeries(instrument, interval_s, candles, funding, spec.funding_rate, spec.spread_bps)
        return InstrumentSeries(instrument, interval_s, candles, spread_bps=spec.spread_bps)
