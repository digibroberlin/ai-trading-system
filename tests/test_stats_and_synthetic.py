"""Performance-Statistik und synthetische Marktdaten."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from helpers import D, T0, inst

from trading.audit.stats import compute_performance
from trading.clock import FixedClock
from trading.domain.models import ClosedTrade, ExitReason, MarketType, Side
from trading.marketdata.synthetic import SyntheticMarket
from trading.safety.data_validator import SnapshotValidator
from trading.safety.limits import DEFAULT_LIMITS


def trade(net, risk=D(50), fees=D(1), funding=D(0)):
    return ClosedTrade(
        "P", "S", "test", inst("BTC-USD", MarketType.SPOT), Side.LONG, D(1), D(100), D(101), T0, T0,
        ExitReason.TAKE_PROFIT, net + fees + funding, fees, funding, net, risk,
    )


def test_stats_numbers():
    trades = [trade(D(100)), trade(D(-50)), trade(D(30)), trade(D(-80))]
    s = compute_performance(trades, D(10000))
    assert (s.trade_count, s.wins, s.losses) == (4, 2, 2)
    assert s.win_rate == D("0.5")
    assert s.net_pnl == D(0)
    assert s.gross_profit == D(130) and s.gross_loss == D(130)
    assert s.profit_factor == D(1)
    assert s.expectancy == D(0)
    assert s.total_fees == D(4)
    # Kapitalkurve: 10000 -> 10100 -> 10050 -> 10080 -> 10000; Tiefpunkt 10000 vs. Hoch 10100
    assert s.max_drawdown_pct == (D(100) / D(10100) * 100)
    assert s.avg_r == (D(2) + D(-1) + D("0.6") + D("-1.6")) / 4


def test_stats_without_trades_and_without_losses():
    empty = compute_performance([], D(1000))
    assert empty.trade_count == 0 and empty.win_rate == 0 and empty.profit_factor is None
    only_wins = compute_performance([trade(D(10)), trade(D(5))], D(1000))
    assert only_wins.profit_factor is None and only_wins.max_drawdown_pct == 0
    assert only_wins.return_pct == D("1.5")


def test_stats_reject_non_positive_start_equity():
    with pytest.raises(ValueError):
        compute_performance([], D(0))


# ------------------------------------------------------------------ synthetische Daten


def market(seed=1, **kw):
    return SyntheticMarket(start_time=T0, seed=seed, **kw)


def test_same_seed_gives_identical_candles_different_seed_does_not():
    assert market(5).candles(50) == market(5).candles(50)
    assert market(5).candles(50) != market(6).candles(50)


def test_candles_are_internally_consistent_and_contiguous():
    candles = market(3, volatility=0.01).candles(500)
    for prev, cur in zip(candles, candles[1:]):
        assert cur.open == prev.close
        assert cur.open_time == prev.close_time
    for c in candles:
        assert c.high >= max(c.open, c.close) and c.low <= min(c.open, c.close)
        assert c.low > 0 and c.volume > 0


def test_snapshots_pass_the_validator():
    m = market(9, market_type=MarketType.FUTURES, funding_rate=D("0.0001"))
    clock = FixedClock(T0)
    validator = SnapshotValidator(DEFAULT_LIMITS, clock)
    for candle in m.candles(200):
        snap = m.snapshot_for(candle)
        clock.set(snap.timestamp)
        assert validator.validate(snap).ok
        assert snap.bid < snap.ask and snap.bid <= snap.last <= snap.ask


def test_shock_moves_price_and_trips_the_jump_filter():
    m = market(2, volatility=0.0001)
    clock = FixedClock(T0)
    validator = SnapshotValidator(DEFAULT_LIMITS, clock)
    first = m.snapshot_for(m.next_candle())
    clock.set(first.timestamp)
    assert validator.validate(first).ok
    m.shock(-0.30)
    crash = m.snapshot_for(m.next_candle())
    clock.set(crash.timestamp)
    assert crash.last < first.last * D("0.8")
    assert not validator.validate(crash).ok  # -26 %: als fehlerhafte Daten abgelehnt


def test_synthetic_market_requires_aware_start_time():
    with pytest.raises(ValueError):
        SyntheticMarket(start_time=T0.replace(tzinfo=None))


def test_timestamps_are_utc_aware():
    c = market().next_candle()
    assert c.open_time.utcoffset() == timedelta(0)
    assert replace(c).close_time - c.open_time == timedelta(seconds=60)
