"""Lokale Marktdaten-Dateien (AP-20): strenge Pruefung, Verdichtung ohne erfundene Kerzen, ausgewiesene Luecken,
Herkunft, Zwischenspeicher, Futures-Naeherung - alles mit erfundenen Minuten in temporaeren Dateien (kein Netz)."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.backtest.data import DataQualityError, InstrumentSeries, MarketDataset
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.domain.models import Candle
from trading.marketdata.localfile import build_series, futures_proxy

D = Decimal
UTC = timezone.utc
T0 = datetime(2024, 1, 1, tzinfo=UTC)
SPOT = InstrumentId(Exchange.BITSTAMP, MarketType.SPOT, "BTC-USD")
HEADER = "timestamp,open,high,low,close,volume\n"


def ts(moment):
    return int((moment - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds())


def minutes(hours, zero_hours=(), zero_minutes=()):
    """Je Minute eine Zeile; Stunden in `zero_hours` und Minuten in `zero_minutes` sind Fuellwerte ohne Umsatz."""
    rows = []
    price = D("100")
    for m in range(hours * 60):
        t = T0 + timedelta(minutes=m)
        hour = m // 60
        if hour in zero_hours or m in zero_minutes:
            rows.append("%d,%s,%s,%s,%s,0.0" % (ts(t), price, price, price, price))
            continue
        o = price
        c = price + D("0.5") if m % 2 else price - D("0.25")
        rows.append("%d,%s,%s,%s,%s,%s" % (ts(t), o, max(o, c) + D("0.1"), min(o, c) - D("0.1"), c, D("0.5")))
        price = c
    return rows


def write(tmp_path, name, rows, gz=False):
    path = tmp_path / name
    text = HEADER + "\n".join(rows) + "\n"
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def build(paths, hours=4, **kw):
    return build_series(paths, SPOT, 3600, T0, T0 + timedelta(hours=hours), "Test", "CC BY-SA 4.0", **kw)


def test_hours_are_built_only_from_traded_minutes_and_split_files_are_joined(tmp_path):
    rows = minutes(4, zero_minutes={0, 1, 125})
    a = write(tmp_path, "a.csv.gz", rows[:100], gz=True)
    b = write(tmp_path, "b.csv", rows[100:])
    local = build([a, b])
    candles = local.series.candles
    assert [c.open_time for c in candles] == [T0 + timedelta(hours=h) for h in range(4)]
    assert local.minutes == 240 and local.traded_minutes == 237 and not local.gaps
    # Stunde 0 beginnt mit der ersten GEHANDELTEN Minute (2), nicht mit dem Fuellwert
    first_traded = rows[2].split(",")
    assert candles[0].open == D(first_traded[1])
    hour1 = [r.split(",") for r in rows[60:120]]
    assert candles[1].high == max(D(r[2]) for r in hour1) and candles[1].low == min(D(r[3]) for r in hour1)
    assert candles[1].close == D(hour1[-1][4]) and candles[1].volume == D("30.0")
    assert isinstance(candles[0].open, Decimal) and str(candles[0].open) == first_traded[1]  # exakt aus dem Text
    lines = local.provenance.lines()
    assert any("a.csv.gz" in line and "SHA-256" in line for line in lines) and "Funding: keine (Spot)" in lines


def test_an_hour_without_any_trade_is_a_declared_gap_never_a_filled_candle(tmp_path):
    local = build([write(tmp_path, "m.csv", minutes(5, zero_hours={2}))], hours=5)
    assert [c.open_time.hour for c in local.series.candles] == [0, 1, 3, 4]
    assert local.gaps == ((T0 + timedelta(hours=2), T0 + timedelta(hours=3)),)
    dataset = MarketDataset.build([local.series])  # ausgewiesen -> nur Warnung
    assert any("von der Quelle ausgewiesen" in w for w in dataset.warnings)
    undeclared = InstrumentSeries(SPOT, 3600, local.series.candles)
    with pytest.raises(DataQualityError, match="gap"):
        MarketDataset.build([undeclared])  # dieselbe Luecke ohne Ausweis bleibt ein Fehler


def test_declared_gaps_are_limited_and_must_match_exactly(tmp_path):
    local = build([write(tmp_path, "m.csv", minutes(5, zero_hours={2}))], hours=5)
    wrong = InstrumentSeries(SPOT, 3600, local.series.candles,
                             declared_gaps=((T0 + timedelta(hours=2), T0 + timedelta(hours=4)),))
    with pytest.raises(DataQualityError, match="gap"):
        MarketDataset.build([wrong])
    candles = tuple(Candle(SPOT, T0 + timedelta(hours=h), 3600, D(100), D(101), D(99), D(100), D(1))
                    for h in (0, 30))
    too_long = InstrumentSeries(SPOT, 3600, candles,
                                declared_gaps=((T0 + timedelta(hours=1), T0 + timedelta(hours=30)),))
    with pytest.raises(DataQualityError, match="laenger"):
        MarketDataset.build([too_long])


def test_window_keeps_only_gaps_inside_the_window(tmp_path):
    local = build([write(tmp_path, "m.csv", minutes(8, zero_hours={2}))], hours=8)
    dataset = MarketDataset.build([local.series])
    assert dataset.window(T0, T0 + timedelta(hours=8)).series[0].declared_gaps == local.gaps
    assert dataset.window(T0 + timedelta(hours=3), T0 + timedelta(hours=8)).series[0].declared_gaps == ()


@pytest.mark.parametrize("mutate,match", [
    (lambda rows: ["timestamp,open,high,low"] + rows, "Kopfzeile|Werte"),
    (lambda rows: rows[:5] + [rows[5].replace(",", ",x", 1)] + rows[6:], "keine Zahl"),
    (lambda rows: rows[:5] + ["%d,1,1,1,1,1" % (ts(T0) + 330)] + rows[5:], "nicht auf der Minute"),
    (lambda rows: rows[:5] + [rows[3]] + rows[5:], "aelter"),
    (lambda rows: rows[:5] + [rows[4].rsplit(",", 1)[0] + ",9.9"] + rows[5:], "doppelt"),
    (lambda rows: rows[:5] + rows[6:], "Minuten fehlen"),
    (lambda rows: rows[:5] + ["%s,-1" % rows[5].rsplit(",", 1)[0]] + rows[6:], "negatives Volumen"),
    (lambda rows: rows[:5] + ["%d,100,99,98,100,1" % (ts(T0) + 300)] + rows[6:], "formal fehlerhaft"),
    (lambda rows: rows[:-30], "nicht vollstaendig"),
])
def test_every_inconsistency_aborts_with_a_reason(tmp_path, mutate, match):
    rows = minutes(4)
    path = tmp_path / "bad.csv"
    body = mutate(rows)
    path.write_text((HEADER if not body[0].startswith("timestamp") else "") + "\n".join(body) + "\n", encoding="utf-8")
    with pytest.raises(DataQualityError, match=match):
        build([path])


def test_identical_duplicate_rows_at_a_file_border_count_once(tmp_path):
    rows = minutes(4)
    local = build([write(tmp_path, "a.csv", rows[:121]), write(tmp_path, "b.csv", rows[120:])])
    assert local.minutes == 240


def test_cache_returns_the_same_series_and_is_rebuilt_when_changed(tmp_path):
    src = write(tmp_path, "m.csv", minutes(4, zero_hours={1}))
    cache = tmp_path / "cache"
    first = build([src], cache_dir=cache)
    stored = list(cache.glob("*.json"))
    assert len(stored) == 1
    assert build([src], cache_dir=cache).series == first.series
    data = json.loads(stored[0].read_text())
    data["rows"][0][4] = "999"  # Inhalt veraendert, Pruefsumme passt nicht mehr -> neu gerechnet
    stored[0].write_text(json.dumps(data))
    assert build([src], cache_dir=cache).series == first.series
    src.write_text(src.read_text().replace(",0.5\n", ",0.6\n", 1))  # Quelldatei geaendert -> anderer Schluessel
    assert build([src], cache_dir=cache).series.candles[0].volume != first.series.candles[0].volume


def test_futures_proxy_is_labelled_and_settles_every_eight_hours(tmp_path):
    local = build([write(tmp_path, "m.csv", minutes(26))], hours=26)
    proxy = futures_proxy(local, D("0.0001"))
    s = proxy.series
    assert s.instrument == InstrumentId(Exchange.BITSTAMP, MarketType.FUTURES, "BTC-USD")
    assert [e.time for e in s.funding] == [T0 + timedelta(hours=h) for h in (8, 16, 24)]
    assert all(e.rate == D("0.0001") for e in s.funding) and s.initial_funding_rate == D("0.0001")
    eight = next(c for c in s.candles if c.close_time == T0 + timedelta(hours=8))
    assert s.funding[0].mark_price == eight.close  # Schluss der letzten geschlossenen Kerze
    assert "SZENARIO" in proxy.provenance.funding and "NAEHERUNG" in proxy.provenance.funding
    MarketDataset.build([s])
    with pytest.raises(ValueError):
        futures_proxy(local, D("0.5"))


def test_only_spot_sources_and_aligned_ranges_are_accepted(tmp_path):
    src = write(tmp_path, "m.csv", minutes(4))
    with pytest.raises(ValueError):
        build_series([src], InstrumentId(Exchange.BITSTAMP, MarketType.FUTURES, "BTC-USD"), 3600, T0,
                     T0 + timedelta(hours=4), "x", "y")
    with pytest.raises(ValueError):
        build_series([src], SPOT, 3600, T0 + timedelta(minutes=30), T0 + timedelta(hours=4), "x", "y")
    with pytest.raises(ValueError):
        build_series([src], SPOT, 7, T0, T0 + timedelta(hours=4), "x", "y")


def test_research_only_exchange_cannot_be_configured_for_the_paper_service():
    from trading.config import ConfigError
    from trading.runtime.service import ServiceConfig

    with pytest.raises(ConfigError):
        ServiceConfig(Exchange.BITSTAMP, MarketType.SPOT, ("BTC-USD",))
    with pytest.raises(ConfigError):
        ServiceConfig.from_env({"TRADING_EXCHANGE": "bitstamp", "TRADING_SYMBOLS": "BTC-USD"})
