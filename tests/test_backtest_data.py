"""Datenqualitaet fuer Backtests: nur vollstaendige, geordnete, fehlerfreie Reihen werden zugelassen."""

import dataclasses
from datetime import timedelta
from decimal import Decimal

import pytest
from bt_helpers import BTC, ETHF, HOUR, bars, flat, series
from helpers import D, T0, inst

from trading.backtest import DataQualityError, InstrumentSeries, MarketDataset, SyntheticDataSource, SyntheticSpec
from trading.domain.instrument import Exchange
from trading.domain.models import FundingEvent, MarketType
from trading.marketdata.candles import IssueKind, validate_series

GOOD = [[100, 101, 99, 100], [100, 102, 100, 101], [101, 103, 100, 102], [102, 102, 101, 101]]


def fund(i=1, rate="0.0001", mark=100, instrument=ETHF):
    return FundingEvent(instrument, T0 + i * HOUR, D(rate), D(mark))


def issues(*s):
    with pytest.raises(DataQualityError) as err:
        MarketDataset.build(list(s))
    return "\n".join(err.value.issues)


# ------------------------------------------------------------------ einzelne Reihe (validate_series)


def kinds(candles):
    return [i.kind for i in validate_series(candles, D("0.20")).issues]


def test_clean_series_has_no_issues():
    report = validate_series(bars(BTC, GOOD), D("0.20"))
    assert report.ok and report.issues == () and report.count == 4


def test_missing_candles_are_found_and_counted():
    c = bars(BTC, GOOD)
    report = validate_series([c[0], c[3]], D("0.20"))
    assert [i.kind for i in report.errors] == [IssueKind.GAP]
    assert report.errors[0].detail == "2 fehlende Kerze(n)" and report.errors[0].time == c[3].open_time


def test_duplicate_out_of_order_and_overlapping_candles_are_found():
    c = bars(BTC, GOOD)
    assert kinds([c[0], c[1], c[1], c[2]]) == [IssueKind.DUPLICATE]
    # Kerze 1 fehlt an ihrer Stelle (Luecke) und taucht danach verspaetet auf; Kerze 3 folgt lueckenlos auf Kerze 2.
    assert kinds([c[0], c[2], c[1], c[3]]) == [IssueKind.GAP, IssueKind.OUT_OF_ORDER]
    half = dataclasses.replace(c[1], open_time=c[1].open_time - timedelta(minutes=30), interval_s=1800)
    assert IssueKind.MIXED_SERIES in kinds([c[0], half])
    long_bar = dataclasses.replace(c[1], open_time=c[0].open_time + timedelta(minutes=0), interval_s=3600)
    assert kinds([c[0], long_bar]) == [IssueKind.DUPLICATE]


@pytest.mark.parametrize("change", [
    {"close": Decimal("NaN")}, {"high": None}, {"low": D(0)}, {"high": D(50)}, {"volume": D(-1)},
    {"open": 100.0}, {"interval_s": -5}, {"open_time": T0.replace(tzinfo=None)},
    {"open_time": T0 + timedelta(seconds=7)}, {"instrument": "BTC-USD"},
])
def test_invalid_candles_are_found(change):
    c = bars(BTC, GOOD)
    assert IssueKind.INVALID in kinds([c[0], dataclasses.replace(c[1], **change), c[2]])


def test_mixed_instruments_or_intervals_are_found():
    c = bars(BTC, GOOD)
    other = bars(ETHF, GOOD)
    assert kinds([c[0], other[1], c[2]]) == [IssueKind.MIXED_SERIES, IssueKind.GAP]


def test_extreme_moves_and_empty_candles_are_warnings_not_errors():
    crash = bars(BTC, [[100, 101, 99, 100], [100, 100, 60, 65], [65, 66, 64, 65]])
    report = validate_series(crash, D("0.20"))
    assert report.ok and [w.kind for w in report.warnings] == [IssueKind.PRICE_JUMP]
    quiet = [dataclasses.replace(c, volume=D(0)) for c in bars(BTC, GOOD[:2])]
    assert [w.kind for w in validate_series(quiet, D("0.20")).warnings] == [IssueKind.NO_VOLUME] * 2


def test_garbage_never_raises():
    report = validate_series([None, "x", 3], D("0.20"))  # type: ignore[list-item]
    assert [i.kind for i in report.issues] == [IssueKind.INVALID] * 3


# ------------------------------------------------------------------ Datensatz


def test_valid_dataset_is_built_sorted_and_fingerprinted():
    ds = MarketDataset.build([series(ETHF, GOOD, [fund(1)], D("0.0001")), series(BTC, GOOD)])
    assert ds.instruments == (inst("ETH-USD", MarketType.FUTURES), BTC)  # nach Schluessel sortiert
    assert ds.start == T0 and ds.end == T0 + 4 * HOUR and ds.warnings == ()
    assert len(ds.fingerprint) == 64


def test_every_problem_is_reported_at_once():
    c = bars(BTC, GOOD)
    broken = InstrumentSeries(BTC, 3600, (c[0], c[2], c[2], dataclasses.replace(c[3], low=D(-1))))
    text = issues(broken, series(ETHF, GOOD))
    assert "gap" in text and "duplicate" in text and "invalid" in text
    assert "Futures ohne Funding-Daten" in text and text.count("\n") >= 3


@pytest.mark.parametrize("bad, expected", [
    (lambda: series(BTC, []), "keine Kerzen"),
    (lambda: series(BTC, GOOD, [fund(1, instrument=BTC)]), "Funding-Daten bei einem Spot-Instrument"),
    (lambda: series(BTC, GOOD, initial_rate=D("0.0001")), "Funding-Daten bei einem Spot-Instrument"),
    (lambda: series(ETHF, GOOD), "Futures ohne Funding-Daten"),
    (lambda: series(ETHF, GOOD, [fund(2), fund(1)]), "doppelt oder falsch sortiert"),
    (lambda: series(ETHF, GOOD, [fund(1), fund(1)]), "doppelt oder falsch sortiert"),
    (lambda: series(ETHF, GOOD, [fund(9)]), "nach dem Ende der Kerzen"),
    (lambda: series(ETHF, GOOD, [fund(1, rate="NaN")]), "formal ungueltig"),
    (lambda: series(ETHF, GOOD, [fund(1, mark=0)]), "formal ungueltig"),
    (lambda: series(ETHF, GOOD, [fund(1, instrument=inst("XRP-USD", MarketType.FUTURES))]), "gehoert nicht zu dieser Reihe"),
    (lambda: series(ETHF, GOOD, [fund(1)], initial_rate=Decimal("NaN")), "Anfangs-Funding-Satz ungueltig"),
    (lambda: series(BTC, GOOD, spread_bps=D(-1)), "Spread ungueltig"),
    (lambda: dataclasses.replace(series(BTC, GOOD), interval_s=1800), "gehoert nicht zu dieser Reihe"),
    (lambda: dataclasses.replace(series(BTC, GOOD), instrument=inst("btc")), "Instrument formal ungueltig"),
    (lambda: dataclasses.replace(series(BTC, GOOD), instrument=ETHF), "gehoert nicht zu dieser Reihe"),
])
def test_unusable_series_are_rejected_with_a_clear_reason(bad, expected):
    assert expected in issues(bad())


def test_empty_dataset_and_duplicate_instruments_are_rejected():
    with pytest.raises(DataQualityError, match="leer"):
        MarketDataset.build([])
    assert "mehrfach" in issues(series(BTC, GOOD), series(BTC, GOOD))


def test_warnings_are_kept_with_the_dataset():
    ds = MarketDataset.build([series(BTC, [[100, 101, 99, 100], [100, 100, 60, 65]])])
    assert len(ds.warnings) == 1 and "price_jump" in ds.warnings[0]


def test_fingerprint_changes_with_any_value():
    base = MarketDataset.build([series(BTC, GOOD)])
    assert MarketDataset.build([series(BTC, GOOD)]).fingerprint == base.fingerprint
    changed = [row[:] for row in GOOD]
    changed[2][3] = "102.01"
    assert MarketDataset.build([series(BTC, changed)]).fingerprint != base.fingerprint
    assert MarketDataset.build([series(BTC, GOOD, spread_bps=D(1))]).fingerprint != base.fingerprint
    assert MarketDataset.build([series(inst("BTC-USD", exchange=Exchange.COINBASE), GOOD)]).fingerprint != base.fingerprint


def test_truncation_keeps_only_closed_candles_and_matching_funding():
    ds = MarketDataset.build([series(ETHF, flat(10), [fund(2), fund(8)], D("0.0001")), series(BTC, flat(10))])
    cut = ds.truncated(T0 + 5 * HOUR)
    assert [len(s.candles) for s in cut.series] == [5, 5] and cut.end == T0 + 5 * HOUR
    assert [f.time for f in cut.series[0].funding] == [T0 + 2 * HOUR]


# ------------------------------------------------------------------ synthetische Quelle


def test_synthetic_source_is_reproducible_and_independent_per_instrument():
    a = SyntheticDataSource(seed=3).load(BTC, 3600, T0, T0 + timedelta(days=2))
    b = SyntheticDataSource(seed=3).load(BTC, 3600, T0, T0 + timedelta(days=2))
    assert a == b and len(a.candles) == 48 and MarketDataset.build([a]).warnings == ()
    assert SyntheticDataSource(seed=4).load(BTC, 3600, T0, T0 + timedelta(days=2)) != a
    eth = SyntheticDataSource(seed=3, specs={ETHF: SyntheticSpec(start_price=D(100))}).load(
        ETHF, 3600, T0, T0 + timedelta(days=2))
    assert [c.close for c in eth.candles] != [c.close for c in a.candles]  # eigener Zufallsstrom
    assert [f.time.hour for f in eth.funding] == [16, 0, 8, 16, 0, 8] and eth.initial_funding_rate == D("0.0001")
    assert all(f.mark_price == c.close for f in eth.funding for c in eth.candles if c.close_time == f.time)


def test_synthetic_source_refuses_real_exchanges():
    with pytest.raises(ValueError):
        SyntheticDataSource().load(inst("BTC-USD", exchange=Exchange.COINBASE), 3600, T0, T0 + HOUR)
