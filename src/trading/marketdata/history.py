"""Historische Kerzen und Funding echter Boersen (AP-14, D-025) - nur lesend, oeffentliche Schnittstellen, ohne Schluessel.

Gemeinsamer Ablauf fuer alle Boersen (`ExchangeHistorySource.load`):
  1. Anfrage pruefen: Instrument dieser Boerse und Marktart, unterstuetztes Intervall, Zeitraum mit Zeitzone, auf das
     Intervall ausgerichtet und nicht in der Zukunft (nur abgeschlossene Kerzen).
  2. Je UTC-Tag: aus dem Zwischenspeicher (nur abgeschlossene Tage) oder von der Boerse - mit Ratenbegrenzung und wenigen
     Wiederholungen bei voruebergehenden Fehlern (429, 5xx, Netz).
  3. Strikte Pruefung jeder Zeile: Form, Zahlen, Zeitstempel auf dem Intervall, im Tag. Gleiche Zeile doppelt (Seiten
     ueberlappen) -> einmal; gleicher Zeitpunkt mit ANDEREN Werten -> Fehler. Nichts wird aufgefuellt oder geglaettet:
     Luecken bleiben Luecken und werden von `MarketDataset.build` gemeldet.
  4. Futures: Funding-Abrechnungen der Boerse im Zeitraum, Anfangssatz = letzte Abrechnung VOR dem Zeitraum. Der Kurs zur
     Abrechnung ist eine Naeherung fuer den Mark-Preis: Schlusskurs der letzten bis dahin geschlossenen Kerze (sonst
     Eroeffnung der Kerze, in der die Abrechnung liegt). Die Abweichung wirkt nur auf den Funding-BETRAG und ist klein.
"""

from __future__ import annotations

import time
from abc import abstractmethod
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Sequence

from trading.backtest.data import DataQualityError, HistoricalDataSource, InstrumentSeries
from trading.clock import Clock
from trading.domain.instrument import Exchange, InstrumentId, MarketType, is_instrument
from trading.domain.models import Candle, FundingEvent
from trading.marketdata.candles import is_aware
from trading.marketdata.daycache import DayCache, Row
from trading.marketdata.public_http import FetchError, JsonGet
from trading.marketdata.ratelimit import RateLimiter

D = Decimal
DAY = timedelta(days=1)
SECOND = timedelta(seconds=1)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
RETRY_DELAYS_S = (1.0, 2.0, 4.0)
MAX_PAGES = 2_000  # Schutz gegen eine Schnittstelle, die immer weiter Seiten liefert
MAX_FUNDING_GAP_S = 12 * 3600  # Bitget rechnet hoechstens alle 8 h ab; laengere Pausen = fehlende Daten


def to_seconds(moment: datetime) -> int:
    return int((moment - EPOCH).total_seconds())


def from_seconds(seconds: int) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def utc_days(start: datetime, end: datetime) -> List[datetime]:
    """Beginn jedes UTC-Tages, der [start, end) beruehrt."""
    utc = start.astimezone(timezone.utc)
    day = datetime(utc.year, utc.month, utc.day, tzinfo=timezone.utc)
    out = []
    while day < end:
        out.append(day)
        day += DAY
    return out


def decimal_text(value: Any) -> str:
    """Zahl aus der Boersen-Antwort als exakter Text; nur endliche Zahlen (Text oder Decimal/int, nie float/bool)."""
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("keine Zahl: %r" % (value,))
    try:
        number = D(str(value).strip())
    except InvalidOperation:
        raise ValueError("keine Zahl: %r" % (value,)) from None
    if not number.is_finite():
        raise ValueError("Zahl nicht endlich: %r" % (value,))
    return str(number)


def int_seconds(value: Any, unit_ms: bool) -> int:
    """Zeitstempel (Sekunden oder Millisekunden, als Zahl oder Text) -> ganze Sekunden; Bruchteile sind ein Fehler."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("kein Zeitstempel: %r" % (value,))
    raw = int(str(value).strip())
    if unit_ms:
        if raw % 1000:
            raise ValueError("Zeitstempel nicht auf ganze Sekunden: %r" % (value,))
        raw //= 1000
    if raw < 0:
        raise ValueError("negativer Zeitstempel: %r" % (value,))
    return raw


class ExchangeHistorySource(HistoricalDataSource):
    """Grundlage der Boersen-Adapter. Unterklassen liefern nur die Rohzeilen einer Boerse (`fetch_candles`,
    `fetch_funding`) als (Zeit in s, Eroeffnung, Hoch, Tief, Schluss, Volumen) bzw. (Zeit in s, Satz) - als Text."""

    exchange: ClassVar[Exchange]
    markets: ClassVar[FrozenSet[MarketType]]
    intervals: ClassVar[FrozenSet[int]]

    def __init__(
        self,
        http: JsonGet,
        clock: Clock,
        cache: Optional[DayCache] = None,
        limiter: Optional[RateLimiter] = None,
        sleep: Callable[[float], None] = time.sleep,
        spread_bps: Decimal = D("2"),
    ) -> None:
        self._http = http
        self._clock = clock
        self._cache = cache
        self._limiter = limiter or RateLimiter(5.0)
        self._sleep = sleep
        self._spread_bps = spread_bps
        self.requests = 0  # Zahl der Abrufe bei der Boerse (fuer Pruefung und Tests)

    # ------------------------------------------------------------------ oeffentlich

    def load(self, instrument: InstrumentId, interval_s: int, start: datetime, end: datetime) -> InstrumentSeries:
        self._check(instrument, interval_s, start, end)
        rows = self._rows("candles", instrument, interval_s, utc_days(start, end), per_day=True,
                          fetch=lambda a, b: self.fetch_candles(instrument, interval_s, a, b))
        lo, hi = to_seconds(start), to_seconds(end)
        candles = tuple(self._candle(instrument, interval_s, r) for r in rows
                        if lo <= int(r[0]) and int(r[0]) + interval_s <= hi)
        if not candles or candles[0].open_time != start or candles[-1].close_time != end:
            raise DataQualityError(["%s: Zeitraum nicht vollstaendig abgedeckt (%s Kerzen, erste %s, letzte bis %s)" % (
                instrument.key, len(candles), candles[0].open_time if candles else "-",
                candles[-1].close_time if candles else "-")])
        if instrument.market_type is not MarketType.FUTURES:
            return InstrumentSeries(instrument, interval_s, candles, spread_bps=self._spread_bps)
        # Tag davor: Anfangssatz (Abrechnung mindestens alle 8 h); bis einschliesslich `end` (Abrechnung genau zum Ende
        # gehoert dazu, wie bei `MarketDataset.window`); EIN Abruf fuer alle fehlenden Tage
        funding = self._rows("funding", instrument, 0, utc_days(start - DAY, end + SECOND), per_day=False,
                             fetch=lambda a, b: self.fetch_funding(instrument, a, b))
        before = [r for r in funding if int(r[0]) <= lo]
        seq = [int(before[-1][0])] if before else [lo]
        seq += [int(r[0]) for r in funding if lo < int(r[0]) <= hi] + [hi]
        holes = [(a, b) for a, b in zip(seq, seq[1:]) if b - a > MAX_FUNDING_GAP_S]
        if not before or holes:
            raise DataQualityError(["%s: Funding unvollstaendig (Anfangssatz %s, Luecken %s)" % (
                instrument.key, "fehlt" if not before else "ok",
                ", ".join("%s..%s" % (from_seconds(a), from_seconds(b)) for a, b in holes[:5]) or "keine")])
        closes = [to_seconds(c.close_time) for c in candles]
        events = tuple(FundingEvent(instrument, from_seconds(int(r[0])), D(r[1]), _mark(candles, closes, int(r[0])))
                       for r in funding if lo < int(r[0]) <= hi)
        return InstrumentSeries(instrument, interval_s, candles, events, D(before[-1][1]), self._spread_bps)

    # ------------------------------------------------------------------ je Boerse

    @abstractmethod
    def fetch_candles(self, instrument: InstrumentId, interval_s: int, start: datetime, end: datetime) -> List[Row]:
        """Rohzeilen (Zeit s, o, h, l, c, v) mit Beginn in [start, end) - Reihenfolge und Doppelte egal."""

    def fetch_funding(self, instrument: InstrumentId, start: datetime, end: datetime) -> List[Row]:
        """Rohzeilen (Zeit s, Satz) mit Abrechnung in [start, end)."""
        raise NotImplementedError("%s liefert kein Funding." % type(self).__name__)

    def get(self, url: str, params: Mapping[str, str]) -> Any:
        """Ein Abruf mit Ratenbegrenzung und wenigen Wiederholungen bei voruebergehenden Fehlern."""
        for attempt in range(len(RETRY_DELAYS_S) + 1):
            self._limiter.wait()
            self.requests += 1
            try:
                return self._http(url, params)
            except FetchError as exc:
                if not exc.retryable or attempt == len(RETRY_DELAYS_S):
                    raise
                self._sleep(RETRY_DELAYS_S[attempt])
        raise AssertionError("unerreichbar")  # pragma: no cover

    # ------------------------------------------------------------------ intern

    def _check(self, instrument: InstrumentId, interval_s: int, start: datetime, end: datetime) -> None:
        if not is_instrument(instrument) or instrument.exchange is not self.exchange:
            raise ValueError("%s liefert nur Instrumente von %s." % (type(self).__name__, self.exchange.value))
        if instrument.market_type not in self.markets:
            raise ValueError("Marktart %s wird nicht unterstuetzt." % instrument.market_type.value)
        if interval_s not in self.intervals:
            raise ValueError("Intervall %r s wird nicht unterstuetzt (%s)." % (interval_s, sorted(self.intervals)))
        if not (is_aware(start) and is_aware(end)) or end <= start:
            raise ValueError("Zeitraum ungueltig (mit Zeitzone, Ende nach Beginn).")
        if to_seconds(start) % interval_s or to_seconds(end) % interval_s:
            raise ValueError("Beginn und Ende muessen auf dem Intervall liegen.")
        if end > self._clock.now():
            raise ValueError("Zeitraum reicht in die Zukunft - nur abgeschlossene Kerzen.")

    def _rows(self, kind: str, instrument: InstrumentId, interval_s: int, days: Sequence[datetime], per_day: bool,
              fetch: Callable[[datetime, datetime], List[Row]]) -> List[Row]:
        """Zeilen aller Tage: abgeschlossene aus dem Zwischenspeicher, fehlende von der Boerse (je Tag oder in einem
        Abruf fuer alle fehlenden Tage) - geprueft und danach (nur abgeschlossene Tage) gespeichert."""
        now = self._clock.now()
        found: Dict[datetime, List[Row]] = {}
        missing: List[datetime] = []
        for day in days:
            cached = None
            if self._cache is not None and day + DAY <= now:
                cached = self._cache.get(kind, instrument, interval_s, day)
            if cached is None:
                missing.append(day)
            else:
                found[day] = cached
        groups = [[d] for d in missing] if per_day else ([missing] if missing else [])
        for group in groups:
            a, b = group[0], min(group[-1] + DAY, now)
            rows = normalize(fetch(a, b), kind, to_seconds(a), to_seconds(b), interval_s, instrument.key)
            for day in group:
                part = [r for r in rows if to_seconds(day) <= int(r[0]) < to_seconds(day + DAY)]
                found[day] = part
                if self._cache is not None and day + DAY <= now:
                    self._cache.put(kind, instrument, interval_s, day, part)
        return [r for day in days for r in found[day]]

    @staticmethod
    def _candle(instrument: InstrumentId, interval_s: int, row: Row) -> Candle:
        t, o, h, low, c, v = row
        return Candle(instrument, from_seconds(int(t)), interval_s, D(o), D(h), D(low), D(c), D(v))


def normalize(raw: Sequence[Row], kind: str, lo: int, hi: int, interval_s: int, key: str) -> List[Row]:
    """Sortiert, entfernt gleiche Doppelte, prueft Form und Ausrichtung; nur Zeilen mit Beginn in [lo, hi) und - bei
    Kerzen - Ende bis hi (laufende Kerze nie). Widersprueche -> DataQualityError mit allen Befunden."""
    width = 6 if kind == "candles" else 2
    issues: List[str] = []
    seen: Dict[int, Row] = {}
    for row in raw:
        if len(row) != width:
            issues.append("%s: Zeile mit %d statt %d Werten" % (key, len(row), width))
            continue
        t = int(row[0])
        if kind == "candles" and t % interval_s:
            issues.append("%s: Kerze %s nicht auf dem Intervall" % (key, from_seconds(t)))
            continue
        if not lo <= t < hi or (kind == "candles" and t + interval_s > hi):
            continue
        if t in seen and seen[t] != row:
            issues.append("%s: %s doppelt mit verschiedenen Werten" % (key, from_seconds(t)))
            continue
        seen[t] = row
    if issues:
        raise DataQualityError(issues)
    return [seen[t] for t in sorted(seen)]


def _mark(candles: Sequence[Candle], closes: Sequence[int], t: int) -> Decimal:
    """Naeherung fuer den Mark-Preis zur Abrechnung: Schluss der letzten bis dahin geschlossenen Kerze, sonst
    Eroeffnung der ersten Kerze (Abrechnung in der ersten Kerze)."""
    i = bisect_right(closes, t)
    return candles[i - 1].close if i else candles[0].open


__all__ = ["DAY", "ExchangeHistorySource", "decimal_text", "from_seconds", "int_seconds", "normalize", "to_seconds",
           "utc_days"]
