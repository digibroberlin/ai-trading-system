"""Strategie-Bibliothek (AP-16): Indikatoren, Signale je Strategie, kein Blick in die Zukunft, Determinismus,
Anbindung an die volle Pipeline und die Vergleichsmassstaebe Nicht-Handeln / Kaufen-und-Halten."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from bt_helpers import BTC, ETHF, FREE, bars, config, series, synthetic
from helpers import T0

from trading.backtest import BacktestEngine, MarketDataset, MarketView
from trading.domain.models import FundingEvent, Horizon, Position, Side
from trading.execution.costs import CostModel
from trading.strategies import (
    STRATEGY_TYPES,
    BreakoutStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    NoTradeStrategy,
    ScalpingStrategy,
    StopRules,
    TrendFollowingStrategy,
    VolatilitySqueezeStrategy,
    build_strategy,
    buy_and_hold,
    default_strategies,
)
from trading.analysis import indicators as ind

D = Decimal
HOUR = timedelta(hours=1)


def view(instrument, ohlc, positions=()):
    candles = tuple(bars(instrument, ohlc))
    return MarketView(candles[-1].close_time, {instrument: candles}, tuple(positions))


def pos(instrument, side, opened=T0, entry=100):
    long = side is Side.LONG
    return Position("P1", "s1", "x", instrument, side, D(1), D(entry), D(entry) * D("0.9" if long else "1.1"),
                    D(entry) * D("1.2" if long else "0.8"), D(1), opened, D(entry), D(10), D(0), None, opened)


def closes_to_ohlc(closes, wiggle=D("0.5")):
    rows, prev = [], closes[0]
    for c in closes:
        rows.append([prev, max(prev, c) + wiggle, min(prev, c) - wiggle, c])
        prev = c
    return rows


# ------------------------------------------------------------------ Indikatoren


def test_moving_averages_and_spread_measures():
    v = [D(x) for x in (1, 2, 3, 4, 5)]
    assert ind.sma(v, 3) == D(4) and ind.sma(v, 6) is None
    assert ind.ema(v, 3) == D(4)  # Start 2, dann 0,5*4+0,5*2 = 3, dann 0,5*5+0,5*3 = 4
    assert ind.stdev([D(x) for x in (2, 4, 4, 4, 5, 5, 7, 9)], 8) == D(2)
    mid, upper, lower = ind.bollinger([D(10)] * 20, 20, D(2))
    assert mid == upper == lower == D(10) and ind.bandwidth([D(10)] * 20, 20, D(2)) == 0
    assert ind.roc([D(100), D(110)], 1) == D("0.1") and ind.roc([D(100)], 1) is None
    assert ind.efficiency_ratio([D(x) for x in (1, 2, 3, 4)], 3) == 1
    assert ind.efficiency_ratio([D(x) for x in (1, 2, 1, 2, 1)], 4) == 0


def test_rsi_follows_wilder():
    assert ind.rsi([D(x) for x in (1, 2, 1, 2, 1)], 2) == D("37.5")  # von Hand nachgerechnet
    assert ind.rsi([D(x) for x in range(1, 10)], 3) == 100 and ind.rsi([D(5)] * 9, 3) == 50
    assert ind.rsi([D(1), D(2)], 2) is None


def test_true_range_atr_and_donchian_use_only_the_given_candles():
    c = bars(BTC, [[100, 101, 99, 100], [100, 101, 99, 100], [105, 106, 104, 105]])  # Kursluecke
    assert ind.true_ranges(c) == [D(2), D(2), D(6)]  # 106 - Vorschluss 100
    assert ind.atr(c, 2) == D(4) and ind.atr(c, 3) is None
    assert ind.donchian(c, 2) == (D(101), D(99))  # der Kanal VOR der letzten Kerze


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_invalid_periods_are_rejected(bad):
    with pytest.raises(ValueError):
        ind.sma([D(1)], bad)


# ------------------------------------------------------------------ Signale je Strategie


FLAT = [[100, 101, 99, 100]] * 20


def test_breakout_long_on_spot_and_short_only_on_futures():
    s = BreakoutStrategy()
    entry = s.on_bar(view(BTC, FLAT + [[100, 104, 100, 103]])).entries[0]
    assert entry.side is Side.LONG and entry.stop_loss < D(103) < entry.take_profit and "Ausbruch" in entry.rationale
    assert entry.horizon is Horizon.SWING
    assert abs((entry.take_profit - D(103)) - (D(103) - entry.stop_loss) * D("2.5")) < D("0.0000001")  # 2,5 x Stop
    assert entry.stop_loss == D("98.71428571") and entry.take_profit == D("113.71428571")  # ATR 30/14, zu Ungunsten
    assert s.on_bar(view(BTC, FLAT + [[100, 100, 96, 97]])).entries == ()  # Spot: kein Short
    short = s.on_bar(view(ETHF, FLAT + [[100, 100, 96, 97]])).entries[0]
    assert short.side is Side.SHORT and short.take_profit < D(97) < short.stop_loss
    assert BreakoutStrategy(allow_short=False).on_bar(view(ETHF, FLAT + [[100, 100, 96, 97]])).entries == ()
    assert s.on_bar(view(BTC, FLAT + [[100, 101, 99, 100.5]])).entries == ()  # im Kanal


def test_breakout_exit_on_the_shorter_channel_and_no_entry_with_open_position():
    s = BreakoutStrategy(entry=20, exit=10)
    d = s.on_bar(view(BTC, FLAT + [[100, 100, 97, 98]], [pos(BTC, Side.LONG)]))
    assert d.entries == () and d.exits[0].rationale == "unter 10-Kerzen-Tief"
    d = s.on_bar(view(BTC, FLAT + [[100, 104, 100, 103]], [pos(BTC, Side.LONG)]))
    assert d.entries == () and d.exits == ()  # Position offen: nie ein zweiter Einstieg


def test_trend_following_signals_only_on_a_fresh_cross_and_exits_when_the_trend_turns():
    closes = [D(200 - i) for i in range(80)] + [D(121 + 3 * i) for i in range(40)]
    rows = closes_to_ohlc(closes)
    s = TrendFollowingStrategy(fast=5, slow=20)
    signals = [i for i in range(25, len(rows)) if s.on_bar(view(BTC, rows[: i + 1])).entries]
    assert len(signals) == 1  # genau am Kreuzungspunkt, nicht danach
    entry = s.on_bar(view(BTC, rows[: signals[0] + 1])).entries[0]
    assert entry.side is Side.LONG and "aufwaerts" in entry.rationale
    falling = closes_to_ohlc([D(200 - i) for i in range(60)])
    assert s.on_bar(view(BTC, falling, [pos(BTC, Side.LONG)])).exits[0].rationale == "Trend gedreht"
    assert s.on_bar(view(BTC, falling, [pos(BTC, Side.SHORT)])).exits == ()


def test_momentum_needs_a_fresh_jump_above_the_threshold_in_the_trend_direction():
    base = [D(100) + D(i) / 10 for i in range(60)]  # langsam steigend, Momentum unter 3 %
    s = MomentumStrategy(lookback=10, threshold=D("0.03"), trend=20)
    assert s.on_bar(view(BTC, closes_to_ohlc(base))).entries == ()
    jumped = closes_to_ohlc(base + [base[-1] * D("1.05")])
    entry = s.on_bar(view(BTC, jumped)).entries[0]
    assert entry.side is Side.LONG and "Momentum" in entry.rationale
    falling = closes_to_ohlc(base + [base[-1] * D("0.9")])
    assert s.on_bar(view(BTC, falling, [pos(BTC, Side.LONG)])).exits[0].rationale == "Momentum verloren"


def test_mean_reversion_targets_the_middle_band():
    calm = [D(100) + (D("0.3") if i % 2 else D("-0.3")) for i in range(40)]
    rows = closes_to_ohlc(calm + [D(98), D(95), D(91)], wiggle=D("0.2"))  # unter dem Band, RSI ~23
    entry = MeanReversionStrategy().on_bar(view(BTC, rows)).entries[0]
    mid = ind.sma([D(str(r[3])) for r in rows], 20)
    assert entry.side is Side.LONG and entry.take_profit == mid and entry.stop_loss < D(91) < mid
    up = closes_to_ohlc(calm + [D(102), D(105), D(109)], wiggle=D("0.2"))  # ueber dem Band, RSI ~78
    assert MeanReversionStrategy().on_bar(view(ETHF, up)).entries[0].side is Side.SHORT
    back = closes_to_ohlc(calm + [D(91), D(101)], wiggle=D("0.2"))
    assert MeanReversionStrategy().on_bar(view(BTC, back, [pos(BTC, Side.LONG)])).exits[0].rationale == "Mitte erreicht"


def test_volatility_squeeze_needs_a_quiet_phase_before_the_breakout():
    wide = [D(100) + (D(5) if i % 2 else D(-5)) for i in range(60)]
    quiet = [D(100) + (D("0.2") if i % 2 else D("-0.2")) for i in range(30)]
    s = VolatilitySqueezeStrategy(period=10, lookback=40, window=5)
    entry = s.on_bar(view(BTC, closes_to_ohlc(wide + quiet + [D(103)], wiggle=D("0.1")))).entries[0]
    assert entry.side is Side.LONG and "enger Phase" in entry.rationale
    # dieselbe Bewegung ohne vorherige Ruhe: kein Squeeze-Signal
    assert s.on_bar(view(BTC, closes_to_ohlc(wide + wide[:30] + [D(110)], wiggle=D("0.1")))).entries == ()


def test_scalping_buys_dips_in_an_uptrend_and_leaves_after_max_hold():
    up = [D(100) + D(i) / 2 for i in range(60)]
    rows = closes_to_ohlc(up + [up[-1] - 2, up[-1] - 4], wiggle=D("0.1"))
    entry = ScalpingStrategy().on_bar(view(BTC, rows)).entries[0]
    assert entry.side is Side.LONG and entry.horizon is Horizon.SCALP
    held = pos(BTC, Side.LONG, opened=T0 + 57 * HOUR)  # die letzten 5 Kerzen beginnen ab hier
    assert "Haltedauer" in ScalpingStrategy(max_hold=5).on_bar(view(BTC, rows, [held])).exits[0].rationale
    fresh = pos(BTC, Side.LONG, opened=T0 + 61 * HOUR)
    assert ScalpingStrategy(max_hold=5).on_bar(view(BTC, rows, [fresh])).exits == ()  # RSI noch tief


@pytest.mark.parametrize("make", [
    lambda: TrendFollowingStrategy(fast=50, slow=20),
    lambda: MomentumStrategy(threshold=D(0)),
    lambda: MeanReversionStrategy(oversold=D(70), overbought=D(30)),
    lambda: BreakoutStrategy(entry=10, exit=20),
    lambda: VolatilitySqueezeStrategy(lookback=5, window=10),
    lambda: ScalpingStrategy(entry_level=D(80), exit_level=D(70)),
    lambda: StopRules(reward_risk=D("0.5")),
    lambda: StopRules(min_stop_pct=D("0.6")),
    lambda: StopRules(stop_atr=D("NaN")),
    lambda: build_strategy("gibt-es-nicht"),
])
def test_invalid_settings_are_rejected(make):
    with pytest.raises(ValueError):
        make()


# ------------------------------------------------------------------ alle Strategien gemeinsam


def test_registry_builds_every_strategy_with_a_unique_name():
    strategies = default_strategies()
    assert len(strategies) == len(STRATEGY_TYPES) and len({s.name for s in strategies}) == len(strategies)


@pytest.mark.parametrize("kind", sorted(STRATEGY_TYPES))
def test_every_proposal_is_consistent_over_a_long_synthetic_run(kind):
    """Bei jedem Zeitpunkt: Stop und Ziel auf der richtigen Seite des letzten Kurses, Short nur bei Futures,
    mit offener Position kein zweiter Einstieg in dasselbe Instrument."""
    ds = synthetic(days=25, seed=3)
    s = build_strategy(kind)
    entries = 0
    for i in range(1, 25 * 24):
        now = T0 + i * HOUR
        bars_now = {x.instrument: tuple(c for c in x.candles if c.close_time <= now)[-200:] for x in ds.series}
        for e in s.on_bar(MarketView(now, bars_now, ())).entries:
            entries += 1
            last = bars_now[e.instrument][-1].close
            if e.side is Side.LONG:
                assert e.stop_loss < last < e.take_profit and e.stop_loss > 0
            else:
                assert e.instrument == ETHF and e.take_profit < last < e.stop_loss and e.take_profit > 0
            assert isinstance(e.horizon, Horizon) and e.rationale
        held = MarketView(now, bars_now, (pos(BTC, Side.LONG), pos(ETHF, Side.SHORT)))
        assert s.on_bar(held).entries == ()
    assert kind == "no_trade" or entries > 0


@pytest.mark.parametrize("kind", sorted(STRATEGY_TYPES))
def test_no_strategy_can_see_the_future_and_runs_are_reproducible(kind):
    """Volle Pipeline (RIN, SORA, Risikomanager, Executor): Alles bis zum Schnitt ist identisch, ob danach noch Daten
    kommen oder nicht. Zwei Laeufe mit gleichen Daten sind bitgleich."""
    ds = synthetic(days=20, seed=9)
    cut = T0 + timedelta(days=12)
    full = BacktestEngine(ds, build_strategy(kind), config()).run()
    again = BacktestEngine(ds, build_strategy(kind), config()).run()
    part = BacktestEngine(ds.truncated(cut), build_strategy(kind), config()).run()
    assert full.result_hash == again.result_hash
    before = [p for p in full.equity_curve if p.time < cut]
    assert before == [p for p in part.equity_curve if p.time < cut] and before
    assert [t for t in full.trades if t.closed_at < cut] == [t for t in part.trades if t.closed_at < cut]
    assert kind == "no_trade" or full.setups_proposed > 0


# ------------------------------------------------------------------ Vergleichsmassstaebe


def test_no_trade_keeps_the_capital_untouched():
    r = BacktestEngine(synthetic(days=5), NoTradeStrategy(), config()).run()
    assert r.report.final_equity == D(10000) and r.trades == () and r.setups_proposed == 0


def test_buy_and_hold_without_costs_follows_the_price():
    ds = MarketDataset.build([series(BTC, [[100, 100, 100, 100], [100, 100, 90, 90], [90, 110, 90, 110]])])
    r = buy_and_hold(ds, D(10000), FREE)
    assert r.final_equity == D(11000) and r.return_pct == D(10) and r.fees == 0
    assert [p.equity for p in r.equity_curve] == [D(10000), D(9000), D(11000)]
    assert r.stats.max_drawdown_pct == D(10)


def test_buy_and_hold_pays_spread_slippage_fees_and_funding():
    costs = CostModel(taker_fee=D("0.001"), slippage_bps=D("10"))
    ds = MarketDataset.build([series(BTC, [[100, 100, 100, 100], [100, 110, 100, 110]], spread_bps=D(20))])
    r = buy_and_hold(ds, D(10000), costs)
    qty = D(10000) / D("1.001") / (D(100) * D("1.001") * D("1.001"))
    proceeds = qty * D(110) * D("0.999") * D("0.999")
    assert abs(r.final_equity - proceeds * D("0.999")) < D("0.000000001") and r.fees > 0
    fund = [FundingEvent(ETHF, T0 + HOUR, D("0.001"), D(100))]
    ds2 = MarketDataset.build([series(ETHF, [[100, 100, 100, 100], [100, 100, 100, 100]], funding=fund,
                                      initial_rate=D("0.001"))])
    r2 = buy_and_hold(ds2, D(10000), FREE)
    assert r2.funding == D(10) and r2.final_equity == D(9990)  # Long zahlt 0,1 % auf 10 000


def test_buy_and_hold_splits_the_capital_equally():
    ds = MarketDataset.build([series(BTC, [[100, 100, 100, 100], [100, 120, 100, 120]]),
                              series(ETHF, [[50, 50, 50, 50], [50, 50, 40, 40]], initial_rate=D(0),
                                     funding=[FundingEvent(ETHF, T0 + HOUR, D(0), D(50))])])
    r = buy_and_hold(ds, D(10000), FREE)
    assert r.final_equity == D(5000) * D("1.2") + D(5000) * D("0.8")
    with pytest.raises(ValueError):
        buy_and_hold(ds, D(0), FREE)
