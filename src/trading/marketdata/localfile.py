"""Historische Kerzen aus LOKALEN Dateien (AP-20) - ohne Netz, mit Herkunftsnachweis, nichts erfunden.

Wozu: Die Boersen-Schnittstellen sind in der Cloud gesperrt (B-008). Oeffentliche, kostenlose Datensaetze, die ausserhalb
dieses Pakets heruntergeladen wurden (z. B. die taeglich aus der oeffentlichen Bitstamp-Schnittstelle erzeugte Kopie der
BTC/USD-1-Minuten-Kerzen), werden hier gelesen, streng geprueft und zu groeberen Kerzen verdichtet:

  * Format: CSV (auch .gz) mit Kopfzeile `timestamp,open,high,low,close,volume`; Zeitstempel = Beginn der Minute in
    Sekunden (UTC). Zahlen werden als Text exakt uebernommen (Decimal, nie float). Jede Unstimmigkeit (Form, Zahl,
    Raster, Reihenfolge, widerspruechliche Doppelte, Hoch/Tief) bricht mit allen Befunden ab.
  * Verdichtung (z. B. 60 s -> 3600 s): nur Minuten MIT Umsatz zaehlen. Die Quelle fuellt fehlende Minuten mit flachen
    Kerzen ohne Umsatz auf - das sind keine Kurse, sondern Fuellwerte. Eine Kerze ohne eine einzige gehandelte Minute
    gibt es nicht: Sie fehlt und wird als BEKANNTE LUECKE ausgewiesen (`InstrumentSeries.declared_gaps`), nie aufgefuellt.
  * Ehrliche Kennzeichnung: Das Instrument traegt die Boerse, von der die Kurse stammen (z. B. `bitstamp:spot:BTC-USD`).
    Die Herkunft (Beschreibung, Lizenz, je Datei Name, Groesse und SHA-256, Verfahren) gehoert in jeden Bericht.
  * Futures gibt es in solchen Spot-Daten nicht. `futures_proxy` baut ausdruecklich eine NAEHERUNG: dieselben Kurse,
    Funding als SZENARIO (fester Satz je 8 h, kein echter Satz der Boerse) - das steht in der Herkunft.

Ergebnisse koennen in einem Zwischenspeicher abgelegt werden (`cache_dir`): Schluessel = Pruefsummen der Quelldateien +
Verfahren + Zeitraum; der Inhalt traegt eine eigene Pruefsumme. Passt etwas nicht, wird neu gerechnet.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from trading.backtest.data import DataQualityError, InstrumentSeries
from trading.domain.instrument import InstrumentId, MarketType, is_instrument
from trading.domain.models import Candle, FundingEvent
from trading.marketdata.candles import is_aware

D = Decimal
MINUTE = 60
HEADER = ("timestamp", "open", "high", "low", "close", "volume")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
METHOD_VERSION = "1"  # aendert sich, wenn sich das Verfahren aendert -> Zwischenspeicher wird neu gerechnet
FUNDING_HOURS = 8
Gap = Tuple[datetime, datetime]
Row = Tuple[int, str, str, str, str, str]


def _at(seconds: int) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def _seconds(moment: datetime) -> int:
    return int((moment - EPOCH).total_seconds())


@dataclass(frozen=True)
class SourceFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Provenance:
    description: str  # woher die Kurse stammen (Boerse, Weg der Kopie, Stand)
    license: str
    files: Tuple[SourceFile, ...]
    method: str  # wie verdichtet wurde
    funding: str  # "keine (Spot)" oder ausdruecklich "SZENARIO ..."

    def lines(self) -> List[str]:
        out = ["Quelle: %s" % self.description, "Lizenz: %s" % self.license, "Verfahren: %s" % self.method,
               "Funding: %s" % self.funding]
        out += ["Datei %s: %d Bytes, SHA-256 %s" % (f.name, f.size, f.sha256) for f in self.files]
        return out


@dataclass(frozen=True)
class LocalSeries:
    series: InstrumentSeries
    provenance: Provenance
    minutes: int  # gelesene Minuten im Zeitraum
    traded_minutes: int  # davon mit Umsatz (nur diese zaehlen)

    @property
    def gaps(self) -> Tuple[Gap, ...]:
        return self.series.declared_gaps


def describe_file(path: Path) -> SourceFile:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return SourceFile(path.name, size, digest.hexdigest())


def _open_text(path: Path) -> io.TextIOBase:
    if path.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", newline="")
    return open(path, "r", encoding="utf-8", newline="")


def _number(text: str, where: str) -> Decimal:
    try:
        value = D(text.strip())
    except InvalidOperation:
        raise DataQualityError(["%s: keine Zahl %r" % (where, text)]) from None
    if not value.is_finite():
        raise DataQualityError(["%s: Zahl nicht endlich %r" % (where, text)])
    return value


def read_minutes(paths: Sequence[Path], start: int, end: int) -> Iterator[Row]:
    """Minuten mit Beginn in [start, end) aus den Dateien (in dieser Reihenfolge), streng geprueft und aufsteigend.
    Gleiche Zeile doppelt (Dateigrenze) -> einmal; gleicher Zeitpunkt mit anderen Werten -> Fehler."""
    previous: Optional[Row] = None
    for path in paths:
        with _open_text(path) as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None or tuple(h.strip().lower() for h in header) != HEADER:
                raise DataQualityError(["%s: Kopfzeile %r statt %s" % (path.name, header, ",".join(HEADER))])
            for line_no, fields in enumerate(reader, 2):
                where = "%s Zeile %d" % (path.name, line_no)
                if len(fields) != 6:
                    raise DataQualityError(["%s: %d statt 6 Werte" % (where, len(fields))])
                try:
                    t = int(fields[0].strip())
                except ValueError:
                    raise DataQualityError(["%s: Zeitstempel %r" % (where, fields[0])]) from None
                if t % MINUTE:
                    raise DataQualityError(["%s: Zeitstempel nicht auf der Minute" % where])
                if t >= end:
                    return
                row: Row = (t, fields[1].strip(), fields[2].strip(), fields[3].strip(), fields[4].strip(),
                            fields[5].strip())
                if previous is not None:
                    if t == previous[0]:
                        if row != previous:
                            raise DataQualityError(["%s: %s doppelt mit anderen Werten" % (where, _at(t))])
                        continue
                    if t < previous[0]:
                        raise DataQualityError(["%s: %s aelter als die Vorzeile" % (where, _at(t))])
                previous = row
                if t >= start:
                    yield row


@dataclass
class _Bucket:
    open_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def aggregate(rows: Iterator[Row], instrument: InstrumentId, interval_s: int, start: int,
              end: int) -> Tuple[List[Candle], List[Gap], int, int]:
    """Verdichtet Minuten zu Kerzen von `interval_s`. Nur Minuten mit Umsatz zaehlen; Kerzen ohne eine gehandelte Minute
    fehlen (Luecken, zusammengefasst). Rueckgabe: (Kerzen, Luecken, Minuten, gehandelte Minuten)."""
    candles: List[Candle] = []
    minutes = traded = 0
    bucket: Optional[_Bucket] = None
    first_minute: Optional[int] = None
    last_minute: Optional[int] = None

    def flush(b: _Bucket) -> None:
        candles.append(Candle(instrument, _at(b.open_time), interval_s, b.open, b.high, b.low, b.close, b.volume))

    for t, o, h, lo, c, v in rows:
        where = "%s %s" % (instrument.key, _at(t))
        first_minute = t if first_minute is None else first_minute
        if last_minute is not None and t != last_minute + MINUTE:
            raise DataQualityError(["%s: Minuten fehlen in der Quelldatei (vor %s)" % (instrument.key, _at(t))])
        last_minute = t
        minutes += 1
        volume = _number(v, where)
        if volume < 0:
            raise DataQualityError(["%s: negatives Volumen" % where])
        if volume == 0:
            continue  # Fuellwert der Quelle oder Minute ohne Handel: kein Kurs
        values = [_number(x, where) for x in (o, h, lo, c)]
        if min(values) <= 0 or not (values[1] >= max(values[0], values[3]) and values[2] <= min(values[0], values[3])):
            raise DataQualityError(["%s: Kerze formal fehlerhaft (%s)" % (where, ",".join((o, h, lo, c)))])
        traded += 1
        open_time = t - t % interval_s
        if bucket is not None and bucket.open_time != open_time:
            flush(bucket)
            bucket = None
        if bucket is None:
            bucket = _Bucket(open_time, values[0], values[1], values[2], values[3], volume)
        else:
            bucket.high = max(bucket.high, values[1])
            bucket.low = min(bucket.low, values[2])
            bucket.close = values[3]
            bucket.volume += volume
    if bucket is not None:
        flush(bucket)
    if first_minute is None or last_minute is None or first_minute > start or last_minute < end - MINUTE:
        raise DataQualityError(["%s: Quelldateien decken %s bis %s nicht vollstaendig ab"
                                % (instrument.key, _at(start), _at(end))])
    return candles, _gaps(candles, interval_s, start, end), minutes, traded


def _gaps(candles: Sequence[Candle], interval_s: int, start: int, end: int) -> List[Gap]:
    """Zusammenhaengende fehlende Kerzen im Raster [start, end) - als (Beginn, Ende) der Luecke."""
    step = timedelta(seconds=interval_s)
    gaps: List[Gap] = []
    expected = _at(start)
    for c in candles:
        if c.open_time > expected:
            gaps.append((expected, c.open_time))
        expected = c.open_time + step
    if expected < _at(end):
        gaps.append((expected, _at(end)))
    return gaps


def _key(files: Sequence[SourceFile], instrument: InstrumentId, interval_s: int, start: int, end: int) -> str:
    body = json.dumps({"files": [[f.name, f.size, f.sha256] for f in files], "instrument": instrument.key,
                       "interval_s": interval_s, "start": start, "end": end, "method": METHOD_VERSION},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _rows_digest(rows: List[List[str]], gaps: List[List[int]], counts: List[int]) -> str:
    body = json.dumps([rows, gaps, counts], separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _load_cached(path: Path, key: str) -> Optional[Tuple[List[List[str]], List[List[int]], List[int]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows, gaps, counts = data["rows"], data["gaps"], data["counts"]
        if data.get("key") != key or data.get("sha256") != _rows_digest(rows, gaps, counts):
            return None
        return rows, gaps, counts
    except (OSError, ValueError, KeyError, TypeError):
        return None


def build_series(paths: Sequence[Path], instrument: InstrumentId, interval_s: int, start: datetime, end: datetime,
                 description: str, license_text: str, spread_bps: Decimal = D("2"),
                 cache_dir: Optional[Path] = None) -> LocalSeries:
    """Spot-Reihe aus lokalen 1-Minuten-Dateien. Zeitraum [start, end) mit Zeitzone, auf `interval_s` ausgerichtet."""
    if not is_instrument(instrument) or instrument.market_type is not MarketType.SPOT:
        raise ValueError("Nur Spot-Instrumente der Herkunftsboerse (Futures: futures_proxy).")
    if not isinstance(interval_s, int) or interval_s < MINUTE or interval_s % MINUTE or 86400 % interval_s:
        raise ValueError("interval_s: Vielfaches von 60 s, das einen Tag ganz teilt.")
    if not (is_aware(start) and is_aware(end)) or end <= start:
        raise ValueError("Zeitraum ungueltig (mit Zeitzone, Ende nach Beginn).")
    lo, hi = _seconds(start), _seconds(end)
    if lo % interval_s or hi % interval_s:
        raise ValueError("Beginn und Ende muessen auf dem Intervall liegen.")
    if not paths:
        raise ValueError("Keine Quelldateien.")
    files = tuple(describe_file(Path(p)) for p in paths)
    key = _key(files, instrument, interval_s, lo, hi)
    cached_path = None if cache_dir is None else Path(cache_dir) / ("%s-%d-%s.json" % (
        instrument.key.replace(":", "_"), interval_s, key[:16]))
    cached = _load_cached(cached_path, key) if cached_path is not None and cached_path.exists() else None
    if cached is not None:
        rows, raw_gaps, counts = cached
        candles = [Candle(instrument, _at(int(r[0])), interval_s, D(r[1]), D(r[2]), D(r[3]), D(r[4]), D(r[5]))
                   for r in rows]
        gaps = [(_at(a), _at(b)) for a, b in raw_gaps]
        minutes, traded = counts
    else:
        candles, gaps, minutes, traded = aggregate(read_minutes([Path(p) for p in paths], lo, hi), instrument,
                                                    interval_s, lo, hi)
        if cached_path is not None:
            rows = [[str(_seconds(c.open_time)), str(c.open), str(c.high), str(c.low), str(c.close), str(c.volume)]
                    for c in candles]
            raw_gaps = [[_seconds(a), _seconds(b)] for a, b in gaps]
            counts = [minutes, traded]
            cached_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"key": key, "rows": rows, "gaps": raw_gaps, "counts": counts,
                                       "sha256": _rows_digest(rows, raw_gaps, counts)}, separators=(",", ":")),
                           encoding="utf-8")
            os.replace(tmp, cached_path)  # atomar
    method = ("1-Minuten-Kerzen -> %d s; nur Minuten mit Umsatz (Fuellwerte der Quelle ausgelassen); Kerzen ohne "
              "gehandelte Minute fehlen und sind als Luecken ausgewiesen (%d); nichts aufgefuellt" % (interval_s, len(gaps)))
    provenance = Provenance(description, license_text, files, method, "keine (Spot)")
    series = InstrumentSeries(instrument, interval_s, tuple(candles), spread_bps=spread_bps,
                              declared_gaps=tuple(gaps))
    return LocalSeries(series, provenance, minutes, traded)


def futures_proxy(spot: LocalSeries, rate_per_8h: Decimal) -> LocalSeries:
    """NAEHERUNG einer Futures-Reihe: gleiche Kurse wie `spot`, Funding als SZENARIO (fester Satz alle 8 h um
    00/08/16 UTC; positiv = Long zahlt). Kein echter Funding-Satz - so in der Herkunft vermerkt."""
    if not isinstance(rate_per_8h, Decimal) or not rate_per_8h.is_finite() or abs(rate_per_8h) > D("0.01"):
        raise ValueError("rate_per_8h: Decimal, |Satz| <= 1 % je 8 h.")
    s = spot.series
    instrument = InstrumentId(s.instrument.exchange, MarketType.FUTURES, s.instrument.symbol)
    candles = tuple(replace(c, instrument=instrument) for c in s.candles)
    closes = [c.close_time for c in candles]
    events: List[FundingEvent] = []
    step = timedelta(hours=FUNDING_HOURS)
    t = candles[0].open_time + timedelta(seconds=(-_seconds(candles[0].open_time)) % (FUNDING_HOURS * 3600))
    if t == candles[0].open_time:
        t += step  # die erste Abrechnung liegt nach dem Beginn; davor gilt der Anfangssatz
    index = 0
    while t <= candles[-1].close_time:
        while index < len(closes) and closes[index] <= t:
            index += 1
        mark = candles[index - 1].close if index else candles[0].open  # Naeherung wie bei echten Daten (AP-14)
        events.append(FundingEvent(instrument, t, rate_per_8h, mark))
        t += step
    series = InstrumentSeries(instrument, s.interval_s, candles, tuple(events), rate_per_8h, s.spread_bps,
                              declared_gaps=s.declared_gaps)
    funding = ("SZENARIO: %s je 8 h (positiv = Long zahlt), KEIN echter Satz - Futures-NAEHERUNG aus Spot-Kursen"
               % rate_per_8h)
    return LocalSeries(series, replace(spot.provenance, funding=funding), spot.minutes, spot.traded_minutes)


__all__ = ["HEADER", "LocalSeries", "Provenance", "SourceFile", "aggregate", "build_series", "describe_file",
           "futures_proxy", "read_minutes"]
