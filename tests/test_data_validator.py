"""Fehlerhafte oder veraltete Daten muessen Trades blockieren."""

import dataclasses
from datetime import timedelta
from decimal import Decimal

from helpers import D, T0, flat_candle, make_candle, snapshot

from trading.clock import FixedClock
from trading.domain.models import MarketType
from trading.domain.reasons import RejectReason as R
from trading.safety.data_validator import SnapshotValidator
from trading.safety.limits import DEFAULT_LIMITS


def make_validator():
    clock = FixedClock(T0)
    return clock, SnapshotValidator(DEFAULT_LIMITS, clock)


def test_fresh_valid_snapshot_passes():
    clock, v = make_validator()
    assert v.validate(snapshot(ts=T0 - timedelta(seconds=1))).ok


def test_stale_snapshot_is_blocked():
    clock, v = make_validator()
    res = v.validate(snapshot(ts=T0 - timedelta(seconds=DEFAULT_LIMITS.max_data_age_s + 1)))
    assert not res.ok and R.STALE_DATA in res.reasons


def test_data_exactly_at_age_limit_is_still_ok():
    clock, v = make_validator()
    assert v.validate(snapshot(ts=T0 - timedelta(seconds=DEFAULT_LIMITS.max_data_age_s))).ok


def test_future_timestamp_is_blocked():
    clock, v = make_validator()
    res = v.validate(snapshot(ts=T0 + timedelta(seconds=30)))
    assert R.FUTURE_TIMESTAMP in res.reasons


def test_naive_timestamp_is_blocked():
    clock, v = make_validator()
    res = v.validate(snapshot(ts=T0.replace(tzinfo=None)))
    assert res.reasons == (R.NAIVE_TIMESTAMP,)


def test_non_positive_prices_are_blocked():
    clock, v = make_validator()
    assert R.NON_POSITIVE_PRICE in v.validate(snapshot(mid=D(0))).reasons
    assert R.NON_POSITIVE_PRICE in v.validate(snapshot(mid=D(-5))).reasons


def test_crossed_book_is_blocked():
    clock, v = make_validator()
    s = dataclasses.replace(snapshot(), bid=D(101), ask=D(100))
    assert R.CROSSED_BOOK in v.validate(s).reasons


def test_wide_spread_is_blocked():
    clock, v = make_validator()
    assert R.SPREAD_TOO_WIDE in v.validate(snapshot(mid=D(100), half_spread=D(10))).reasons


def test_price_jump_is_blocked_until_reset():
    clock, v = make_validator()
    assert v.validate(snapshot(mid=D(100))).ok
    assert R.PRICE_JUMP in v.validate(snapshot(mid=D(130))).reasons
    # Der Fehltick vergiftet den Zustand nicht: ein normaler Tick danach ist wieder ok.
    assert v.validate(snapshot(mid=D(101))).ok
    # Ein echter, bewusst bestaetigter Marktsprung erfordert ein manuelles Zuruecksetzen.
    assert R.PRICE_JUMP in v.validate(snapshot(mid=D(140))).reasons
    v.reset("BTC-USD")
    assert v.validate(snapshot(mid=D(140))).ok


def test_out_of_order_tick_is_blocked():
    clock, v = make_validator()
    assert v.validate(snapshot(ts=T0)).ok
    assert R.OUT_OF_ORDER in v.validate(snapshot(ts=T0 - timedelta(seconds=3))).reasons


def test_inconsistent_candle_is_blocked():
    clock, v = make_validator()
    bad = make_candle(T0, D(100), D(99), D(98), D(101))  # high < close
    assert R.BAD_CANDLE in v.validate(snapshot(candle=bad)).reasons
    wrong_symbol = flat_candle(T0, symbol="ETH-USD")
    assert R.BAD_CANDLE in v.validate(snapshot(candle=wrong_symbol)).reasons


def test_futures_without_funding_rate_is_blocked():
    clock, v = make_validator()
    res = v.validate(snapshot(market_type=MarketType.FUTURES, funding=None))
    assert R.FUNDING_MISSING in res.reasons
    assert v.validate(snapshot(market_type=MarketType.FUTURES, funding=Decimal("0.0001"))).ok


def test_multiple_problems_are_all_reported():
    clock, v = make_validator()
    res = v.validate(snapshot(ts=T0 - timedelta(seconds=999), mid=D(0)))
    assert {R.STALE_DATA, R.NON_POSITIVE_PRICE} <= set(res.reasons)


# ------------------------------------------------------------------ Haertung: unvollstaendige / ungueltige Werte


def test_missing_fields_are_rejected_without_exception():
    clock, v = make_validator()
    base = snapshot()
    for field in ("bid", "ask", "last"):
        res = v.validate(dataclasses.replace(base, **{field: None}))
        assert not res.ok and R.MISSING_DATA in res.reasons, field
    assert v.validate(dataclasses.replace(base, timestamp=None)).reasons == (R.MISSING_DATA,)


def test_nan_infinity_and_float_values_are_rejected_without_exception():
    clock, v = make_validator()
    base = snapshot()
    for bad in (Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity"), 100.0, "100"):
        res = v.validate(dataclasses.replace(base, last=bad))
        assert not res.ok and R.NON_FINITE_VALUE in res.reasons, bad


def test_nonfinite_funding_rate_is_rejected():
    clock, v = make_validator()
    res = v.validate(snapshot(market_type=MarketType.FUTURES, funding=Decimal("NaN")))
    assert R.NON_FINITE_VALUE in res.reasons


def test_candle_with_missing_or_nonfinite_values_is_rejected():
    clock, v = make_validator()
    good = flat_candle(T0)
    for bad in (dataclasses.replace(good, high=None), dataclasses.replace(good, low=Decimal("NaN")),
                dataclasses.replace(good, volume=None), dataclasses.replace(good, open_time=None),
                dataclasses.replace(good, interval_s=0)):
        assert R.BAD_CANDLE in v.validate(snapshot(candle=bad)).reasons


def test_invalid_tick_does_not_poison_validator_state():
    clock, v = make_validator()
    assert v.validate(snapshot(mid=D(100))).ok
    assert not v.validate(dataclasses.replace(snapshot(), last=Decimal("NaN"))).ok
    assert v.validate(snapshot(mid=D(101))).ok
