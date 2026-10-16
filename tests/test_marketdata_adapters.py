"""Marktdaten-Adapter (AP-14, D-025): Coinbase Spot, Bitget Spot und USDT-Futures - nur lesend, ohne Netz im Test.

Die Boersen werden nachgebildet (Antwortform und Seitenlogik wie in der Schnittstellenbeschreibung); es gibt keinen
einzigen echten Abruf. Der echte Abruf zur technischen Pruefung braucht Netzfreigabe (B-008).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.backtest.data import DataQualityError, MarketDataset
from trading.clock import FixedClock
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.marketdata.bitget import BitgetHistorySource
from trading.marketdata.coinbase import CoinbaseHistorySource
from trading.marketdata.daycache import DayCache
from trading.marketdata.history import RETRY_DELAYS_S, normalize
from trading.marketdata.public_http import (
    ALLOWED_HOSTS,
    FetchError,
    PublicHttpGet,
    _NoRedirect,
    checked_url,
    parse_json,
)
from trading.marketdata.ratelimit import RateLimiter

D = Decimal
UTC = timezone.utc
DAY0 = datetime(2026, 9, 1, tzinfo=UTC)
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
CB_BTC = InstrumentId(Exchange.COINBASE, MarketType.SPOT, "BTC-USD")
BG_BTC = InstrumentId(Exchange.BITGET, MarketType.SPOT, "BTC-USDT")
BG_PERP = InstrumentId(Exchange.BITGET, MarketType.FUTURES, "BTC-USDT")


def ts(moment):
    return int((moment - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds())


def candle_values(t):
    """Erfundene, aber exakte Kurse mit Nachkommastellen (pruefen, dass nichts ueber float laeuft)."""
    base = D("27000.01") + D(t % 86400) / D(100)
    return base, base + D("12.5"), base - D("7.25"), base + D("3.1"), D("1.23456789")


def no_sleep(_):
    pass


def fast(limiter_rate=50.0):
    return RateLimiter(limiter_rate, monotonic=lambda: 0.0, sleep=no_sleep)


# ------------------------------------------------------------------ nachgebildete Boersen


class FakeCoinbase:
    """GET /products/{id}/candles: Kerzen mit Beginn in [start, end] (einschliesslich), neueste zuerst, max. 300."""

    def __init__(self, interval=3600, skip=(), override=None, until=NOW):
        self.interval, self.skip, self.override, self.until = interval, set(skip), override or {}, until
        self.calls = []

    def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        assert url == "https://api.exchange.coinbase.com/products/BTC-USD/candles"
        g = int(params["granularity"])
        start = ts(datetime.strptime(params["start"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC))
        end = ts(datetime.strptime(params["end"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC))
        rows = []
        for t in range(start - start % g, end + 1, g):
            if t < start or t in self.skip or t + g > ts(self.until):
                continue
            o, h, low, c, v = candle_values(t)
            rows.append(self.override.get(t, [t, low, h, o, c, v]))
        if len(rows) > 300:
            raise FetchError("HTTP 400", 400, False)
        body = json.dumps([[float(x) if isinstance(x, D) else x for x in r] if isinstance(r, list) else r
                           for r in reversed(rows)], default=str)
        # Coinbase sendet Zahlen; die Zeichenfolge wird wie im Betrieb mit Decimal gelesen
        return parse_json(body.encode())


class FakeBitget:
    """Bitget v2: Kerzen mit Zeit < endTime (aufsteigend, max. limit); Funding neueste zuerst in Seiten."""

    def __init__(self, interval=3600, funding_hours=8, funding_skip=(), until=NOW, code="00000"):
        self.interval, self.until, self.code = interval, until, code
        start = ts(datetime(2026, 8, 1, tzinfo=UTC))
        self.funding = [t for t in range(start, ts(until) + 1, funding_hours * 3600) if t not in set(funding_skip)]
        self.calls = []

    def rate(self, t):
        return D("0.0001") if (t // 28800) % 2 else D("-0.00005")

    def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        if self.code != "00000":
            return {"code": self.code, "msg": "Parameter verification failed", "data": None}
        if url.endswith("/history-fund-rate"):
            assert params["productType"] == "USDT-FUTURES" and params["symbol"] == "BTCUSDT"
            size, page = int(params["pageSize"]), int(params["pageNo"])
            newest = list(reversed(self.funding))[(page - 1) * size: page * size]
            data = [{"symbol": "BTCUSDT", "fundingRate": str(self.rate(t)), "fundingTime": str(t * 1000)}
                    for t in newest]
            return {"code": "00000", "msg": "success", "requestTime": 1, "data": data}
        futures = "/mix/" in url
        expected = {60: ("1min", "1m"), 3600: ("1h", "1H")}[self.interval][futures]
        assert params["symbol"] == "BTCUSDT" and params["granularity"] == expected
        assert ("productType" in params) == futures
        end, limit, g = int(params["endTime"]) // 1000, int(params["limit"]), self.interval
        times = [t for t in range(end - limit * g, end, g) if t + g <= ts(self.until)]
        data = []
        for t in times:
            o, h, low, c, v = candle_values(t)
            row = [str(t * 1000), str(o), str(h), str(low), str(c), str(v), "123.4"]
            data.append(row + (["555.5"] if not futures else []))
        return {"code": "00000", "msg": "success", "requestTime": 1, "data": data}


def coinbase(http, cache=None, now=NOW):
    return CoinbaseHistorySource(http, FixedClock(now), cache, fast(), no_sleep)


def bitget(http, cache=None, now=NOW):
    return BitgetHistorySource(http, FixedClock(now), cache, fast(), no_sleep)


# ------------------------------------------------------------------ Coinbase


def test_coinbase_candles_are_complete_exact_and_pass_the_dataset_check():
    http = FakeCoinbase()
    series = coinbase(http).load(CB_BTC, 3600, DAY0, DAY0 + timedelta(days=2))
    assert len(series.candles) == 48 and len(http.calls) == 2  # ein Abruf je Tag (24 <= 300)
    first = series.candles[0]
    assert (first.open_time, first.open, first.volume) == (DAY0, D("27000.01"), D("1.23456789"))
    assert first.high == D("27012.51") and first.low == D("26992.76") and first.close == D("27003.11")
    dataset = MarketDataset.build([series])
    assert dataset.start == DAY0 and dataset.end == DAY0 + timedelta(days=2)
    assert http.calls[0][1] == {"granularity": "3600", "start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"}


def test_coinbase_pages_minute_candles_without_gaps_or_duplicates():
    http = FakeCoinbase(interval=60)
    series = coinbase(http).load(CB_BTC, 60, DAY0, DAY0 + timedelta(days=1))
    assert len(series.candles) == 1440 and len(http.calls) == 5  # 299er-Schritte, Ende einschliesslich
    assert len({c.open_time for c in series.candles}) == 1440
    MarketDataset.build([series])


def test_missing_or_contradicting_exchange_data_is_never_repaired():
    gap = ts(DAY0) + 5 * 3600
    with pytest.raises(DataQualityError) as err:
        MarketDataset.build([coinbase(FakeCoinbase(skip={gap})).load(CB_BTC, 3600, DAY0, DAY0 + timedelta(days=1))])
    assert "1 fehlende Kerze" in str(err.value)
    with pytest.raises(DataQualityError, match="nicht vollstaendig abgedeckt"):
        coinbase(FakeCoinbase(skip={ts(DAY0)})).load(CB_BTC, 3600, DAY0, DAY0 + timedelta(days=1))
    for bad in ([ts(DAY0) + 1800, 1, 2, 1, 2, 1], [ts(DAY0), "x", 2, 1, 2, 1], [ts(DAY0), True, 2, 1, 2, 1],
                [ts(DAY0), 1, 2, 1, 2]):
        with pytest.raises(DataQualityError):
            coinbase(FakeCoinbase(override={ts(DAY0): bad})).load(CB_BTC, 3600, DAY0, DAY0 + timedelta(days=1))
    rows = [("0", "1", "2", "1", "1", "1"), ("0", "1", "2", "1", "1", "1"), ("60", "1", "2", "1", "1", "1")]
    assert normalize(rows, "candles", 0, 120, 60, "k") == [rows[0], rows[2]]  # Seiten-Ueberlappung: einmal
    with pytest.raises(DataQualityError, match="verschiedenen Werten"):
        normalize(rows + [("60", "1", "2", "1", "1.5", "1")], "candles", 0, 120, 60, "k")
    with pytest.raises(DataQualityError, match="nicht auf dem Intervall"):
        normalize([("30", "1", "2", "1", "1", "1")], "candles", 0, 120, 60, "k")
    assert normalize([("60", "1", "2", "1", "1", "1")], "candles", 0, 100, 60, "k") == []  # Kerze laeuft noch


@pytest.mark.parametrize("args", [
    (InstrumentId(Exchange.BITGET, MarketType.SPOT, "BTC-USD"), 3600, DAY0, DAY0 + timedelta(days=1)),  # andere Boerse
    (InstrumentId(Exchange.COINBASE, MarketType.FUTURES, "BTC-USD"), 3600, DAY0, DAY0 + timedelta(days=1)),
    (CB_BTC, 1800, DAY0, DAY0 + timedelta(days=1)),  # Coinbase kennt kein 30-Minuten-Intervall
    (CB_BTC, 3600, DAY0 + timedelta(minutes=30), DAY0 + timedelta(days=1)),  # nicht auf dem Intervall
    (CB_BTC, 3600, DAY0, NOW + timedelta(hours=1)),  # Zukunft: nur abgeschlossene Kerzen
    (CB_BTC, 3600, DAY0.replace(tzinfo=None), (DAY0 + timedelta(days=1)).replace(tzinfo=None)),  # ohne Zeitzone
])
def test_invalid_requests_never_reach_the_exchange(args):
    http = FakeCoinbase()
    with pytest.raises(ValueError):
        coinbase(http).load(*args)
    assert http.calls == []


# ------------------------------------------------------------------ Zwischenspeicher


def test_complete_days_are_cached_the_running_day_is_not(tmp_path):
    cache = DayCache(tmp_path)
    http = FakeCoinbase()
    end = NOW - timedelta(hours=2)  # heute bis 10:00 - laufender Tag
    first = coinbase(http, cache).load(CB_BTC, 3600, DAY0 + timedelta(days=8), end)
    assert len(http.calls) == 2
    again = coinbase(http, cache).load(CB_BTC, 3600, DAY0 + timedelta(days=8), end)
    assert again == first and len(http.calls) == 3  # nur der laufende Tag wird erneut abgerufen
    files = sorted(p.name for p in tmp_path.rglob("*.json"))
    assert files == ["candles-3600-2026-09-09.json"]


def test_damaged_cache_files_are_discarded_and_reloaded(tmp_path):
    cache = DayCache(tmp_path)
    http = FakeCoinbase()
    span = (DAY0, DAY0 + timedelta(days=1))
    good = coinbase(http, cache).load(CB_BTC, 3600, *span)
    path = cache.path("candles", CB_BTC, 3600, DAY0)
    data = json.loads(path.read_text())
    data["rows"][3][4] = "1.00"  # Schlusskurs von Hand geaendert -> Fingerabdruck passt nicht
    path.write_text(json.dumps(data))
    assert coinbase(http, cache).load(CB_BTC, 3600, *span) == good and len(http.calls) == 2
    assert cache.discarded == [path]
    path.write_text("{kaputt")
    assert coinbase(http, cache).load(CB_BTC, 3600, *span) == good and len(http.calls) == 3


# ------------------------------------------------------------------ Bitget


def test_bitget_spot_pages_backwards_and_maps_the_symbol():
    http = FakeBitget()
    series = bitget(http).load(BG_BTC, 3600, DAY0, DAY0 + timedelta(days=1))
    assert len(series.candles) == 24 and series.funding == ()
    url, params = http.calls[0]
    assert url == "https://api.bitget.com/api/v2/spot/market/history-candles"
    assert params == {"symbol": "BTCUSDT", "granularity": "1h", "limit": "200",
                      "endTime": str(ts(DAY0 + timedelta(days=1)) * 1000)}
    minute = FakeBitget(interval=60)
    series = BitgetHistorySource(minute, FixedClock(NOW), None, fast(), no_sleep).load(
        BG_BTC, 60, DAY0, DAY0 + timedelta(days=1))
    assert len(series.candles) == 1440 and len(minute.calls) == 8  # 1440 / 200, rueckwaerts
    MarketDataset.build([series])


def test_bitget_futures_carry_exchange_funding_with_the_rate_known_before_the_start():
    http = FakeBitget()
    start, end = DAY0 + timedelta(hours=4), DAY0 + timedelta(days=2)
    series = bitget(http).load(BG_PERP, 3600, start, end)
    assert [e.time.hour for e in series.funding] == [8, 16, 0, 8, 16, 0]
    assert series.initial_funding_rate == http.rate(ts(DAY0))  # Abrechnung 00:00 VOR dem Beginn 04:00
    ev = series.funding[0]
    assert ev.rate == http.rate(ts(ev.time))
    closing = [c for c in series.candles if c.close_time == ev.time][0]
    assert ev.mark_price == closing.close  # Naeherung: Schluss der Kerze, die zur Abrechnung endet
    MarketDataset.build([series])
    fund_calls = [c for c in http.calls if c[0].endswith("history-fund-rate")]
    assert len(fund_calls) == 1  # EIN Abruf fuer alle Tage (Seite reicht weit genug zurueck)


def test_bitget_funding_gaps_or_errors_are_reported():
    hole = ts(DAY0 + timedelta(hours=16))
    with pytest.raises(DataQualityError, match="Funding unvollstaendig"):
        bitget(FakeBitget(funding_skip={hole, hole + 28800})).load(BG_PERP, 3600, DAY0, DAY0 + timedelta(days=2))
    late = FakeBitget()
    late.funding = [t for t in late.funding if t > ts(DAY0) + 3600]  # nichts vor dem Beginn bekannt
    with pytest.raises(DataQualityError, match="Anfangssatz fehlt"):
        bitget(late).load(BG_PERP, 3600, DAY0 + timedelta(hours=2), DAY0 + timedelta(days=1))
    with pytest.raises(FetchError, match="40034"):
        bitget(FakeBitget(code="40034")).load(BG_BTC, 3600, DAY0, DAY0 + timedelta(days=1))


# ------------------------------------------------------------------ Ratenbegrenzung und Wiederholungen


def test_rate_limiter_spaces_requests_evenly():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(round(s, 6))
        now[0] += s

    limiter = RateLimiter(5.0, monotonic=lambda: now[0], sleep=sleep)
    for _ in range(4):
        limiter.wait()
    assert slept == [0.2, 0.2, 0.2]
    now[0] += 10
    assert limiter.wait() == 0  # nach einer Pause sofort
    for bad in (0, -1, 51):
        with pytest.raises(ValueError):
            RateLimiter(bad)


def test_only_temporary_errors_are_retried_and_only_a_few_times():
    slept = []
    attempts = []

    def flaky(url, params):
        attempts.append(1)
        if len(attempts) <= 2:
            raise FetchError("HTTP 429", 429, True)
        return FakeCoinbase()(url, params)

    source = CoinbaseHistorySource(flaky, FixedClock(NOW), None, fast(), slept.append)
    assert len(source.load(CB_BTC, 3600, DAY0, DAY0 + timedelta(days=1)).candles) == 24
    assert slept == [1.0, 2.0] and source.requests == 3

    def always(url, params):
        attempts.append(1)
        raise FetchError("HTTP 503", 503, True)

    attempts.clear()
    with pytest.raises(FetchError):
        CoinbaseHistorySource(always, FixedClock(NOW), None, fast(), no_sleep).load(
            CB_BTC, 3600, DAY0, DAY0 + timedelta(days=1))
    assert len(attempts) == len(RETRY_DELAYS_S) + 1

    def forbidden(url, params):
        attempts.append(1)
        raise FetchError("HTTP 403", 403, False)

    attempts.clear()
    with pytest.raises(FetchError):
        CoinbaseHistorySource(forbidden, FixedClock(NOW), None, fast(), no_sleep).load(
            CB_BTC, 3600, DAY0, DAY0 + timedelta(days=1))
    assert len(attempts) == 1


# ------------------------------------------------------------------ Netzzugang: nur lesend, nur erlaubte Hosts


def test_only_https_get_to_known_public_market_data_hosts():
    assert ALLOWED_HOSTS == {"api.exchange.coinbase.com", "api.bitget.com"}
    assert checked_url("https://api.bitget.com/api/v2/x", {"b": "2", "a": "1"}) == "https://api.bitget.com/api/v2/x?a=1&b=2"
    for bad in ("http://api.bitget.com/x", "https://api.bitget.com.evil.example/x", "https://example.com/x",
                "https://user:pw@api.bitget.com/x", "https://api.bitget.com:8443/x", "https://api.bitget.com/x?q=1",
                "file:///etc/passwd"):
        with pytest.raises(FetchError):
            checked_url(bad, {})
    with pytest.raises(FetchError):
        checked_url("https://api.bitget.com/x", {"a": 1})


def test_disallowed_addresses_never_open_a_connection_and_redirects_are_not_followed(monkeypatch):
    getter = PublicHttpGet()
    opened = []
    monkeypatch.setattr(getter._opener, "open", lambda *a, **k: opened.append(a))
    with pytest.raises(FetchError):
        getter("https://example.com/x", {})
    assert opened == []
    assert _NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://example.com/") is None
    assert parse_json(b'[1700000000, 27000.10, 1e-8]') == [1700000000, D("27000.10"), D("1E-8")]
    with pytest.raises(FetchError):
        parse_json(b"<html>")


def test_command_line_loads_checks_and_reports(tmp_path):
    from trading.marketdata.__main__ import main

    lines = []
    code = main(["--exchange", "bitget", "--market", "futures", "--symbol", "BTC-USDT", "--interval", "3600",
                 "--start", "2026-09-01", "--end", "2026-09-03", "--cache", str(tmp_path)],
                http=FakeBitget(), clock=FixedClock(NOW), out=lines.append)
    assert code == 0 and "48 Kerzen, 6 Funding-Abrechnungen" in lines[0] and "in Ordnung" in lines[1]
    lines.clear()
    code = main(["--exchange", "coinbase", "--symbol", "BTC-USD", "--start", "2026-09-01", "--end", "2026-09-02",
                 "--cache", str(tmp_path)], http=FakeCoinbase(skip={ts(DAY0) + 7200}), clock=FixedClock(NOW),
                out=lines.append)
    assert code == 1 and lines[0].startswith("ABGELEHNT")
