"""Paper Executor - ausschliesslich synthetische Daten, von Hand nachgerechnete Erwartungswerte.

Grundfall (Kostenmodell ohne Slippage): Spot Long, 40 Stueck zu 100, Stop 99, Ziel 102, Kapital 10 000.
  Einstiegsgebuehr 0,06 % x 4 000 = 2,4 -> Guthaben 9 997,6
  Hoechstverlust   40 + 2,4 + 2,376 = 44,776 (Restrisiko nach bezahlter Einstiegsgebuehr: 42,376)
"""

import dataclasses
import random
from datetime import timedelta
from decimal import Decimal

import pytest
from helpers import D, FUT, Env, T0, flat_candle, inst, make_candle, make_setup, minutes, snapshot

from trading.audit.event_log import InMemoryEventLog
from trading.audit.events import EventType
from trading.domain.models import (
    ExitReason,
    FundingEvent,
    MarketType,
    OrderIntent,
    OrderStatus,
    Side,
)
from trading.domain.reasons import RejectReason as R
from trading.execution.costs import MONEY_QUANTUM, CostModel
from trading.safety.kill_switch import RELEASE_CONFIRMATION, HaltReason
from trading.safety.limits import DEFAULT_LIMITS

FUTM = {"market_type": MarketType.FUTURES}


def futures_order(env, oid="f1", qty=D(40), lev=D(2), **setup_kw):
    symbol = setup_kw.get("symbol", "BTC-USD")
    return env.order(oid, qty, lev, snap_kw=dict(FUT, symbol=symbol), market_type=MarketType.FUTURES, **setup_kw)


def short_order(env, oid="s1", qty=D(40), lev=D(2), **kw):
    kw.setdefault("stop", D(101))
    kw.setdefault("tp", D(98))
    return futures_order(env, oid, qty, lev, side=Side.SHORT, **kw)


def small_order(env, oid, symbol, **kw):
    """Kleine Position mit weitem Stop: 9 Stueck zu 100, Stop 95.
    Hoechstverlust 45 + 0,54 + 0,513 = 46,053 (0,46 %); Exposure 9 %; Stress 90,486 (0,9 %).
    Damit greift bei mehreren Positionen zuerst das 2-%-Gesamtrisiko - nicht Exposure oder Stress."""
    return env.order(oid, qty=D(9), symbol=symbol, stop=D(95), tp=D(110), snap_kw={"symbol": symbol}, **kw)


def events(env, kind):
    return [e.payload for e in env.log.of_type(kind)]


# ------------------------------------------------------------------ Einstieg


def test_long_fill_charges_entry_fee_and_reports_the_portfolio():
    env = Env()
    r = env.order()
    assert r.status is OrderStatus.FILLED
    assert r.position.entry_price == D(100) and r.position.entry_fee == D("2.4")
    assert r.position.risk_at_open == D("44.776") and r.position.remaining_risk == D("42.376")
    assert r.position.instrument == inst("BTC-USD", MarketType.SPOT)
    assert env.executor.balance == D("9997.6")
    s = env.executor.portfolio_state()
    assert (s.balance, s.unrealized_pnl, s.equity) == (D("9997.6"), D(0), D("9997.6"))
    assert s.open_count == 1 and s.open_keys == frozenset({inst("BTC-USD", MarketType.SPOT)})
    assert s.free_margin == D("9997.6") - D(4000)  # Spot: voller Kaufpreis gebunden
    assert s.open_risk == D("42.376")  # die bezahlte Einstiegsgebuehr zaehlt nicht doppelt
    assert s.gross_exposure == D(4000)
    assert s.stress_loss == D("402.16")  # 40 x 10 + 0,06 % x 3 600
    assert s.valuation_complete


def test_entry_slippage_and_spread_are_applied_against_the_trader():
    env = Env(costs=CostModel())
    long_fill = env.order("a", snap_kw={"half_spread": D("0.01")}).position.entry_price
    assert long_fill == D("100.01") * (1 + D(2) / 10000)  # Ask plus Slippage
    env2 = Env(costs=CostModel())
    short = env2.order("b", lev=D(1), market_type=MarketType.FUTURES, side=Side.SHORT, stop=D(101), tp=D(98),
                       snap_kw=dict(FUTM, funding=D(0), half_spread=D("0.01")))
    assert short.position.entry_price == D("99.99") * (1 - D(2) / 10000)  # Bid minus Slippage
    # Kauf zum Brief, Bewertung zur Mitte: der Spread ist sofort ein Buchverlust.
    assert env.executor.portfolio_state().unrealized_pnl == (D(100) - long_fill) * 40 < 0


def test_position_is_not_filled_on_rejection_and_balance_is_untouched():
    env = Env()
    r = env.order(qty=D(100))  # viel zu gross
    assert r.status is OrderStatus.REJECTED and R.TRADE_RISK_TOO_HIGH in r.reasons
    assert env.executor.balance == D(10000) and env.executor.open_positions == ()


# ------------------------------------------------------------------ Stop-Loss und Take-Profit


def test_long_take_profit_exact_numbers():
    env = Env()
    env.order()
    closed = env.feed(make_candle(T0, D(100), D("102.5"), D("99.5"), D(102)))
    assert len(closed) == 1
    t = closed[0]
    assert t.exit_reason is ExitReason.TAKE_PROFIT and t.exit_price == D(102)
    assert t.gross_pnl == D(80)
    assert t.fees == D("2.4") + D("0.816")  # Einstieg taker, Ausstieg maker (0,02 % x 4 080)
    assert t.net_pnl == D("76.784")
    assert env.executor.balance == D("10076.784") and env.executor.open_positions == ()
    assert t.instrument == inst("BTC-USD", MarketType.SPOT)


def test_long_stop_loss_loses_exactly_the_planned_risk():
    env = Env()
    r = env.order()
    t = env.feed(make_candle(T0, D(100), D("100.5"), D("98.5"), D(99)))[0]
    assert t.exit_reason is ExitReason.STOP_LOSS and t.exit_price == D(99)
    assert t.net_pnl == D("-44.776") == -r.position.risk_at_open  # ohne Luecke: geplant == tatsaechlich
    assert t.r_multiple == D(-1)


def test_stop_loss_with_slippage_still_equals_planned_risk():
    env = Env(costs=CostModel())
    r = env.order()
    t = env.feed(make_candle(T0, D(100), D(101), D(98), D(98)))[0]
    assert t.exit_price == D(99) * (1 - D(5) / 10000)
    assert t.net_pnl == -r.position.risk_at_open


def test_stop_wins_when_stop_and_take_profit_hit_in_same_candle():
    env = Env()
    env.order()
    t = env.feed(make_candle(T0, D(100), D(103), D(98), D(102)))[0]
    assert t.exit_reason is ExitReason.STOP_LOSS  # Reihenfolge unbekannt -> schlechterer Fall


def test_gap_through_stop_fills_at_worse_open_price():
    env = Env()
    r = env.order()
    t = env.feed(make_candle(T0, D(95), D(96), D(94), D(95)))[0]
    assert t.exit_price == D(95)  # nicht 99!
    assert t.gross_pnl == D(-200) and t.net_pnl < -r.position.risk_at_open  # Luecken-Risiko ist real


def test_gap_above_take_profit_gives_no_price_improvement():
    env = Env()
    env.order()
    t = env.feed(make_candle(T0, D(105), D(106), D(104), D(105)))[0]
    assert t.exit_reason is ExitReason.TAKE_PROFIT and t.exit_price == D(102)  # nicht 105


def test_short_take_profit_and_stop():
    env = Env()
    short_order(env, "tp")
    t = env.feed(make_candle(T0, D(100), D("100.5"), D("97.9"), D(98), **FUTM))[0]
    assert t.exit_reason is ExitReason.TAKE_PROFIT and t.gross_pnl == D(80)
    assert t.net_pnl == D(80) - D("2.4") - D("0.784")

    short_order(env, "sl")
    t2 = env.feed(make_candle(env.clock.now(), D(100), D("101.5"), D("99.5"), D(101), **FUTM))[0]
    assert t2.exit_reason is ExitReason.STOP_LOSS and t2.exit_price == D(101) and t2.gross_pnl == D(-40)


def test_short_gap_through_stop_fills_at_worse_open_price():
    env = Env()
    short_order(env)
    t = env.feed(make_candle(T0, D(104), D(105), D(103), D(104), **FUTM))[0]
    assert t.exit_reason is ExitReason.STOP_LOSS and t.exit_price == D(104)  # nicht 101!
    assert t.gross_pnl == D(-160)


def test_unrealized_gain_is_equity_but_not_free_margin_and_loss_reduces_free_margin():
    env = Env()
    env.order()
    env.feed(make_candle(T0, D(100), D("101.5"), D("99.5"), D(101)))
    s = env.executor.portfolio_state()
    assert s.unrealized_pnl == D(40) and s.equity == D("10037.6")
    assert s.free_margin == D("9997.6") - D(4000)  # Buchgewinn zaehlt NICHT als freie Margin
    env.feed(make_candle(env.clock.now(), D(101), D(101), D("99.4"), D("99.5")))
    s = env.executor.portfolio_state()
    assert s.unrealized_pnl == D(-20)
    assert s.free_margin == D("9997.6") - D(4000) - D(20)  # Buchverlust schon


def test_new_market_price_from_a_snapshot_updates_the_valuation():
    env = Env()
    small_order(env, "a", "BTC-USD")
    env.clock.advance(60)
    r = small_order(env, "again", "BTC-USD", entry=D(101))  # abgelehnt (schon eine Position) ...
    r = env.order("again2", qty=D(9), stop=D(95), tp=D(110), entry=D(101), snap_kw={"mid": D(101)})
    assert R.DUPLICATE_POSITION in r.reasons
    assert env.executor.portfolio_state().unrealized_pnl == D(9)  # ... aber der neue Kurs 101 gilt


# ------------------------------------------------------------------ Liquidation (isolierte Margin)


def test_long_liquidation_loses_the_whole_margin():
    env = Env()
    r = futures_order(env, qty=D(4), lev=D(5), stop=D(90), tp=D(110))
    assert r.status is OrderStatus.FILLED
    assert r.position.liquidation_price == D("80.5") and r.position.margin == D(80)
    t = env.feed(make_candle(T0, D(70), D(71), D(69), D(70), **FUTM))[0]
    assert t.exit_reason is ExitReason.LIQUIDATION and t.exit_price == D("80.5")
    assert t.gross_pnl == D(-80)  # die GANZE Margin, nicht nur der Kursverlust bis 80,5 (78)
    assert t.fees == D("0.24") and t.net_pnl == D("-80.24")
    assert env.executor.balance == D(10000) - D("80.24")


def test_short_liquidation_loses_the_whole_margin():
    env = Env()
    r = short_order(env, qty=D(4), lev=D(5), stop=D(110), tp=D(90))
    assert r.position.liquidation_price == D("119.5")
    t = env.feed(make_candle(T0, D(130), D(131), D(129), D(130), **FUTM))[0]
    assert t.exit_reason is ExitReason.LIQUIDATION and t.exit_price == D("119.5") and t.gross_pnl == D(-80)


def test_gap_beyond_stop_but_before_liquidation_is_a_normal_stop():
    env = Env()
    futures_order(env, qty=D(4), lev=D(5), stop=D(90), tp=D(110))
    t = env.feed(make_candle(T0, D(85), D(86), D(84), D(85), **FUTM))[0]
    assert t.exit_reason is ExitReason.STOP_LOSS and t.exit_price == D(85) and t.gross_pnl == D(-60)


# ------------------------------------------------------------------ H1: Spot und Futures werden nie verwechselt


def test_candle_only_affects_the_position_of_exactly_that_instrument():
    env = Env()
    assert env.order("spot", qty=D(10)).status is OrderStatus.FILLED  # BTC-USD Spot Long, Stop 99
    assert futures_order(env, "fut", qty=D(10), lev=D(2)).status is OrderStatus.FILLED  # BTC-USD Futures Long
    assert len(env.executor.portfolio_state().open_keys) == 2

    closed = env.feed(make_candle(T0, D(100), D(100), D(98), D(99)))  # SPOT-Kerze reisst den Stop
    assert [t.instrument.market_type for t in closed] == [MarketType.SPOT]
    assert [p.instrument.market_type for p in env.executor.open_positions] == [MarketType.FUTURES]

    closed = env.feed(make_candle(T0, D(100), D(103), D(100), D(102), **FUTM))  # FUTURES-Kerze: Ziel
    assert [(t.instrument.market_type, t.exit_reason) for t in closed] == \
        [(MarketType.FUTURES, ExitReason.TAKE_PROFIT)]


def test_same_pair_on_another_exchange_is_independent():
    from trading.domain.instrument import Exchange

    env = Env()
    env.order("syn", qty=D(10))
    r = env.order("cb", qty=D(10), exchange=Exchange.COINBASE, snap_kw={"exchange": Exchange.COINBASE})
    assert r.status is OrderStatus.FILLED
    env.feed(make_candle(T0, D(100), D(100), D(98), D(99), exchange=Exchange.COINBASE))
    assert [p.instrument.exchange for p in env.executor.open_positions] == [Exchange.SYNTHETIC]


def test_valuation_uses_the_price_of_the_own_instrument():
    env = Env()
    env.order("spot", qty=D(10))
    futures_order(env, "fut", qty=D(10), lev=D(2))
    env.feed(make_candle(T0, D(100), D(101), D(100), D(101)))  # nur Spot steigt
    assert env.executor.portfolio_state().unrealized_pnl == D(10)  # Futures bleibt bei 100 bewertet


def test_snapshot_for_other_instrument_is_refused():
    env = Env()
    assert env.order(snap_kw={"symbol": "ETH-USD"}).reasons == (R.SYMBOL_MISMATCH,)
    assert env.order("o2", snap_kw=dict(FUT)).reasons == (R.SYMBOL_MISMATCH,)  # Spot-Setup, Futures-Daten


def test_malformed_instrument_is_refused():
    env = Env()
    assert env.order("x", symbol="S0", snap_kw={"symbol": "S0"}).reasons == (R.INVALID_SYMBOL,)


# ------------------------------------------------------------------ H3: Kerzen werden geprueft, kein Blick in die Zukunft


def rejected_reasons(env):
    return [tuple(p["reasons"]) for p in events(env, EventType.DATA_REJECTED)]


def test_candle_that_has_not_closed_yet_is_refused():
    env = Env()
    env.order()
    candle = make_candle(T0, D(100), D(100), D(90), D(95))  # schliesst erst 12:01 - es ist 12:00
    assert env.executor.on_candle(candle) == []
    assert len(env.executor.open_positions) == 1  # der Stop aus der "Zukunft" wirkt nicht
    assert rejected_reasons(env) == [("FUTURE_TIMESTAMP",)]
    env.clock.set(candle.close_time)
    assert len(env.executor.on_candle(candle)) == 1  # nach Abschluss der Kerze sehr wohl


def test_candles_that_ended_before_the_entry_are_ignored():
    env = Env()
    env.order()
    old = make_candle(T0 - minutes(5), D(100), D(100), D(90), D(95))  # endete 4 Minuten vor dem Einstieg
    assert env.feed(old) == [] and len(env.executor.open_positions) == 1
    assert len(env.feed(make_candle(T0, D(100), D(100), D(90), D(95)))) == 1


def test_candle_overlapping_the_entry_counts_only_its_stop():
    """H2: Position seit 12:00:30. Die Kerze 12:00-12:01 enthaelt auch Kurse VOR dem Einstieg."""
    env = Env()
    env.clock.set(T0 + timedelta(seconds=30))
    env.order(snap_kw={"ts": env.clock.now()})
    # Ziel (102) erreicht, Stop nicht: ob das NACH dem Einstieg war, ist unbekannt -> kein Gewinn gebucht
    assert env.feed(make_candle(T0, D(100), D(103), D("99.5"), D(101))) == []
    assert len(env.executor.open_positions) == 1

    env = Env()
    env.clock.set(T0 + timedelta(seconds=30))
    env.order(snap_kw={"ts": env.clock.now()})
    # Stop (99) erreicht: konservativ gilt er als nach dem Einstieg ausgeloest - zum Stop-Kurs, ohne Luecke
    t = env.feed(make_candle(T0, D("98.5"), D("100.5"), D("98.4"), D(100)))[0]
    assert t.exit_reason is ExitReason.STOP_LOSS and t.exit_price == D(99)


@pytest.mark.parametrize("change, reason", [
    ({"close": Decimal("NaN")}, "NON_FINITE_VALUE"),
    ({"low": Decimal("Infinity")}, "NON_FINITE_VALUE"),
    ({"high": None}, "MISSING_DATA"),
    ({"open": 100.0}, "NON_FINITE_VALUE"),
    ({"low": D(0)}, "NON_POSITIVE_PRICE"),
    ({"low": D(-5)}, "NON_POSITIVE_PRICE"),
    ({"high": D("99.9")}, "BAD_CANDLE"),  # Hoch unter Schluss
    ({"low": D("100.5")}, "BAD_CANDLE"),  # Tief ueber Eroeffnung
    ({"volume": D(-1)}, "BAD_CANDLE"),
    ({"volume": None}, "MISSING_DATA"),
    ({"interval_s": 0}, "BAD_CANDLE"),
    ({"open_time": T0.replace(tzinfo=None)}, "BAD_CANDLE"),
    ({"open_time": T0 + timedelta(seconds=30)}, "CANDLE_MISALIGNED"),
    ({"instrument": None}, "INVALID_SYMBOL"),
])
def test_invalid_candles_are_refused_logged_and_change_nothing(change, reason):
    env = Env()
    env.order()
    before = env.executor.export_state()
    env.clock.advance(120)
    bad = dataclasses.replace(make_candle(T0, D(100), D("100.2"), D(98), D(100)), **change)
    assert env.executor.on_candle(bad) == []  # keine Exception, kein Stop-Out durch Datenmuell
    assert env.executor.export_state() == before
    assert reason in rejected_reasons(env)[0]
    assert env.executor.portfolio_state().equity.is_finite()


def test_non_candle_objects_are_refused():
    env = Env()
    assert env.executor.on_candle(None) == [] and env.executor.on_candle("kerze") == []
    assert rejected_reasons(env) == [("MISSING_DATA",), ("MISSING_DATA",)]


def test_duplicate_older_and_overlapping_candles_are_refused():
    env = Env()
    env.order()
    c1 = make_candle(T0, D(100), D(100.5), D(99.5), D(100))
    c2 = make_candle(T0 + minutes(1), D(100), D(100.5), D(99.5), D("100.2"))
    assert env.feed(c1) == [] and env.feed(c2) == []
    killer = make_candle(T0, D(100), D(100), D(90), D(95))  # haette den Stop gerissen
    assert env.feed(c2) == [] and env.feed(c1) == [] and env.feed(killer) == []
    long_one = make_candle(T0, D(100), D(100), D(90), D(95), interval_s=300)  # ueberlappt bereits Verarbeitetes
    env.clock.set(long_one.close_time)
    assert env.feed(long_one) == []
    assert rejected_reasons(env) == [("DUPLICATE_CANDLE",), ("OUT_OF_ORDER",), ("OUT_OF_ORDER",), ("OUT_OF_ORDER",)]
    assert len(env.executor.open_positions) == 1
    assert env.executor.portfolio_state().unrealized_pnl == D(8)  # Bewertung blieb beim letzten gueltigen Kurs 100,2


def test_missing_candles_are_detected_and_logged_but_processing_continues():
    env = Env()
    env.order()
    env.feed(make_candle(T0, D(100), D(100.5), D(99.5), D(100)))
    after_gap = make_candle(T0 + minutes(4), D(97), D(97.5), D(96), D(97))  # 3 Kerzen fehlen
    t = env.feed(after_gap)[0]
    gap = events(env, EventType.DATA_GAP)[0]
    assert gap["missing_from"] == (T0 + minutes(1)).isoformat() and gap["missing_to"] == (T0 + minutes(4)).isoformat()
    assert t.exit_price == D(97)  # nach der Luecke: schlechterer Eroeffnungskurs


def test_extreme_but_wellformed_candle_is_processed_conservatively_and_flagged():
    """Ein echter Crash muss Stops ausloesen koennen - also wird nicht verworfen, sondern markiert."""
    env = Env()
    env.order()
    env.feed(make_candle(T0, D(100), D(100.5), D(99.5), D(100)))
    t = env.feed(make_candle(T0 + minutes(1), D(100), D(100), D("0.01"), D(100)))[0]
    assert t.exit_reason is ExitReason.STOP_LOSS and t.exit_price == D(99)
    assert len(events(env, EventType.DATA_ANOMALY)) == 1


def test_replayed_candles_after_a_restart_have_no_effect():
    env = Env()
    env.order()
    c1 = make_candle(T0, D(100), D(100.5), D(99.5), D(100))
    env.feed(c1)
    state = env.executor.export_state()
    fresh = Env()  # "Neustart": neuer Executor, Zustand wiederhergestellt
    fresh.clock.set(env.clock.now())
    fresh.executor.restore_state(state)
    assert fresh.executor.export_state() == state
    assert fresh.feed(make_candle(T0, D(100), D(100), D(90), D(95))) == []  # Wiederholung: abgelehnt
    assert len(fresh.executor.open_positions) == 1


# ------------------------------------------------------------------ M10: Funding nur ueber ausdrueckliche Ereignisse


def test_long_pays_positive_funding_once_per_event():
    env = Env()
    futures_order(env)
    env.clock.advance(4 * 3600)  # 16:00
    assert env.fund() == D("0.4")  # 40 x 100 x 0,01 %
    pos = env.executor.open_positions[0]
    assert pos.funding_paid == D("0.4") and pos.last_funding_ts == env.clock.now()
    assert env.executor.balance == D(10000) - D("2.4") - D("0.4")
    assert env.fund() == D(0)  # dasselbe Ereignis noch einmal (Replay): wirkungslos
    assert env.fund(time=T0 + timedelta(hours=2)) == D(0)  # aelteres Ereignis: wirkungslos
    assert env.executor.open_positions[0].funding_paid == D("0.4")
    assert len(env.log.of_type(EventType.FUNDING_APPLIED)) == 1


def test_funding_uses_rate_and_price_of_the_event():
    env = Env()
    futures_order(env)
    env.clock.advance(4 * 3600)
    assert env.fund(rate=D("0.0003"), mark=D(102)) == D("1.224")  # 40 x 102 x 0,03 %
    env.clock.advance(8 * 3600)
    assert env.fund(rate=D("0.0001"), mark=D(100)) == D("0.4")  # naechste Abrechnung
    assert env.executor.open_positions[0].funding_paid == D("1.624")


def test_short_receives_positive_funding_and_pays_negative_funding():
    env = Env()
    short_order(env)
    env.clock.advance(4 * 3600)
    assert env.fund() == D("-0.4")
    assert env.executor.balance == D(10000) - D("2.4") + D("0.4")
    env.clock.advance(8 * 3600)
    assert env.fund(rate=D("-0.0002")) == D("0.8")  # negativer Satz: Short zahlt


def test_funding_before_or_at_the_entry_does_not_apply():
    env = Env()
    futures_order(env)
    assert env.fund(time=T0) == D(0) and env.fund(time=T0 - timedelta(hours=4)) == D(0)
    assert env.executor.open_positions[0].funding_paid == D(0)


def test_funding_never_touches_the_spot_position_of_the_same_pair():
    env = Env()
    env.order("spot", qty=D(10))
    env.clock.advance(4 * 3600)
    assert env.fund() == D(0)  # Futures-Ereignis, aber nur eine Spot-Position
    assert env.executor.open_positions[0].funding_paid == D(0) and env.executor.balance == D("9999.4")


def test_funding_is_part_of_the_trade_result():
    env = Env()
    futures_order(env)
    env.clock.advance(4 * 3600)
    env.fund()
    t = env.feed(make_candle(env.clock.now(), D(100), D(103), D(100), D(102), **FUTM))[0]
    assert t.funding == D("0.4") and t.net_pnl == D(80) - D("2.4") - D("0.816") - D("0.4")
    assert env.executor.balance == D(10000) + t.net_pnl


@pytest.mark.parametrize("change, reason", [
    ({"rate": Decimal("NaN")}, "BAD_FUNDING"),
    ({"rate": None}, "BAD_FUNDING"),
    ({"mark_price": D(0)}, "BAD_FUNDING"),
    ({"mark_price": 100.0}, "BAD_FUNDING"),
    ({"time": (T0 + timedelta(hours=4)).replace(tzinfo=None)}, "NAIVE_TIMESTAMP"),
    ({"time": T0 + timedelta(hours=9)}, "FUTURE_TIMESTAMP"),
    ({"instrument": inst("BTC-USD", MarketType.SPOT)}, "BAD_FUNDING"),
    ({"instrument": None}, "INVALID_SYMBOL"),
])
def test_invalid_funding_events_are_refused_and_logged(change, reason):
    env = Env()
    futures_order(env)
    env.clock.advance(4 * 3600)
    good = FundingEvent(inst("BTC-USD", MarketType.FUTURES), env.clock.now(), D("0.0001"), D(100))
    before = env.executor.export_state()
    assert env.executor.apply_funding(dataclasses.replace(good, **change)) == D(0)
    assert env.executor.export_state() == before
    assert reason in rejected_reasons(env)[0]


def test_funding_is_rounded_against_the_trader():
    env, env2 = Env(), Env()
    futures_order(env, qty=D("0.3333"), lev=D(1))
    short_order(env2, qty=D("0.3333"), lev=D(1))
    for e in (env, env2):
        e.clock.advance(4 * 3600)
    paid = env.fund(rate=D("0.000123456789"), mark=D("100.01"))
    received = -env2.fund(rate=D("0.000123456789"), mark=D("100.01"))
    exact = D("0.3333") * D("100.01") * D("0.000123456789")
    assert paid >= exact >= received and paid - received == MONEY_QUANTUM  # Zahler auf-, Empfaenger abgerundet
    assert paid == paid.quantize(MONEY_QUANTUM) and received == received.quantize(MONEY_QUANTUM)


# ------------------------------------------------------------------ M11: Schliessung auf Signal


def close(env, symbol="BTC-USD", reason=ExitReason.SIGNAL, rid="x1", market_type=MarketType.SPOT, **snap_kw):
    snap_kw.setdefault("symbol", symbol)
    snap_kw.setdefault("market_type", market_type)
    return env.executor.close_position(inst(symbol, market_type), env.snap(**snap_kw), reason, rid)


def test_long_is_closed_on_signal_at_the_bid_with_taker_fee():
    env = Env()
    env.order()
    env.clock.advance(60)
    res = close(env, mid=D(101), half_spread=D("0.05"))  # Bid 100,95
    assert res.closed and res.reasons == ()
    t = res.trade
    assert t.exit_reason is ExitReason.SIGNAL and t.exit_price == D("100.95")
    assert t.gross_pnl == D(38)  # 0,95 x 40
    assert t.fees == D("2.4") + D("2.4228")  # Ausstieg taker: 0,06 % x 4 038
    assert t.net_pnl == D("33.1772")
    assert env.executor.balance == D(10000) + D("33.1772") and env.executor.open_positions == ()
    payload = events(env, EventType.POSITION_CLOSED)[0]
    assert payload["request_id"] == "x1" and payload["trade"]["exit_reason"] == "signal"


def test_short_is_closed_on_signal_at_the_ask_with_slippage():
    env = Env(costs=CostModel())
    short_order(env, qty=D(10))
    env.clock.advance(60)
    res = close(env, market_type=MarketType.FUTURES, mid=D(99), half_spread=D("0.05"), funding=D(0))
    assert res.closed and res.trade.exit_price == D("99.05") * (1 + D(2) / 10000)  # Ask plus Slippage


def test_close_without_valid_market_data_keeps_the_position_and_its_stop():
    env = Env()
    env.order()
    env.clock.advance(60)
    res = close(env, ts=env.clock.now() - timedelta(seconds=20))  # aelter als die erlaubten 15 s
    assert not res.closed and res.reasons == (R.STALE_DATA,) and res.trade is None
    assert len(env.executor.open_positions) == 1
    assert events(env, EventType.CLOSE_REJECTED)[0]["reasons"] == ["STALE_DATA"]
    # der Stop schuetzt weiterhin:
    assert env.feed(make_candle(env.clock.now(), D(100), D(100), D(98), D(99)))[0].exit_reason is ExitReason.STOP_LOSS


def test_close_requests_that_cannot_work_are_refused():
    env = Env()
    assert close(env).reasons == (R.NO_POSITION,)
    env.order()
    assert close(env, market_type=MarketType.FUTURES, funding=D(0)).reasons == (R.NO_POSITION,)  # anderes Instrument
    wrong_data = env.executor.close_position(inst(), env.snap(symbol="ETH-USD"), ExitReason.SIGNAL, "r")
    assert wrong_data.reasons == (R.SYMBOL_MISMATCH,)
    assert close(env, reason=ExitReason.STOP_LOSS).reasons == (R.INVALID_EXIT_REASON,)
    assert close(env, reason=ExitReason.LIQUIDATION).reasons == (R.INVALID_EXIT_REASON,)
    assert len(env.executor.open_positions) == 1
    assert close(env, reason=ExitReason.MANUAL).closed
    assert close(env).reasons == (R.NO_POSITION,)  # zweimal schliessen geht nicht


def test_closing_frees_risk_exposure_and_the_instrument():
    env = Env()
    env.order()
    assert env.order("b", snap_kw={"symbol": "ETH-USD"}, symbol="ETH-USD").status is OrderStatus.REJECTED
    close(env)
    s = env.executor.portfolio_state()
    assert (s.open_risk, s.gross_exposure, s.stress_loss, s.open_count) == (D(0), D(0), D(0), 0)
    assert env.order("c", snap_kw={"symbol": "ETH-USD"}, symbol="ETH-USD").status is OrderStatus.FILLED


# ------------------------------------------------------------------ Doppel-Orders


def test_same_order_id_is_executed_only_once():
    env = Env()
    assert env.order("dup").status is OrderStatus.FILLED
    again = env.order("dup")
    assert again.status is OrderStatus.REJECTED and again.reasons == (R.DUPLICATE_ORDER_ID,)
    assert len(env.executor.open_positions) == 1 and len(env.log.of_type(EventType.ORDER_FILLED)) == 1


def test_rejected_order_id_cannot_be_retried_with_same_id():
    env = Env()
    assert env.order("x", snap_kw={"ts": T0 - timedelta(seconds=99)}).status is OrderStatus.REJECTED
    assert env.order("x").reasons == (R.DUPLICATE_ORDER_ID,)


def test_second_position_in_same_instrument_is_blocked():
    env = Env()
    small_order(env, "a", "BTC-USD")
    r = small_order(env, "b", "BTC-USD")
    assert r.reasons == (R.DUPLICATE_POSITION,) and len(env.executor.open_positions) == 1


# ------------------------------------------------------------------ Blockaden werden protokolliert


def test_stale_data_blocks_trade_and_is_logged_with_reasons_and_setup():
    env = Env()
    r = env.order("stale1", snap_kw={"ts": T0 - timedelta(seconds=60)})
    assert r.status is OrderStatus.REJECTED and R.STALE_DATA in r.reasons
    assert env.executor.open_positions == ()
    ev = events(env, EventType.ORDER_REJECTED)[0]
    assert ev["reasons"] == ["STALE_DATA"] and ev["stage"] == "EXECUTOR"
    assert ev["setup"]["setup_id"] == "stale1"
    assert ev["setup"]["instrument"] == {"exchange": "synthetic", "market_type": "spot", "symbol": "BTC-USD"}


@pytest.mark.parametrize(
    "snap_kw, reason",
    [
        ({"mid": D(0)}, R.NON_POSITIVE_PRICE),
        ({"half_spread": D(10)}, R.SPREAD_TOO_WIDE),
        ({"ts": T0 + timedelta(seconds=60)}, R.FUTURE_TIMESTAMP),
    ],
)
def test_bad_data_blocks_trade(snap_kw, reason):
    env = Env()
    r = env.order(snap_kw=snap_kw)
    assert reason in r.reasons and env.executor.open_positions == ()


# ------------------------------------------------------------------ M4/H5: alle Grenzen gleichzeitig, bezogen auf die aktuelle Netto-Equity


def test_two_percent_total_risk_is_enforced_inside_the_executor():
    env = Env()
    results = [small_order(env, "o%d" % i, "S%d-USD" % i) for i in range(5)]
    assert [r.status for r in results] == [OrderStatus.FILLED] * 4 + [OrderStatus.REJECTED]
    assert results[4].reasons == (R.PORTFOLIO_RISK_TOO_HIGH,)  # nur diese Grenze - Exposure/Stress haetten Platz
    s = env.executor.portfolio_state()
    assert s.open_risk == 4 * D("45.513") and s.equity == D(10000) - 4 * D("0.54")
    assert s.open_risk / s.equity <= D("0.02")


def test_limit_ratios_never_rise_through_the_fill_itself():
    """Regression (Audit M4): Geprueft wird der Zustand NACH der Order. Direkt nach dem Fill muss
    das Portfolio daher exakt die geprueften Kennzahlen haben - keine Ueberschreitung durch die Gebuehr."""
    env = Env(costs=CostModel())
    for i in range(4):
        sym = "R%d-USD" % i
        r = env.order("r%d" % i, qty=D(9), symbol=sym, stop=D(95), tp=D(110),
                      snap_kw={"symbol": sym, "half_spread": D("0.02")})
        assert r.status is OrderStatus.FILLED
        s = env.executor.portfolio_state()
        assert s.open_risk / s.equity == r.risk.total_risk_pct_after <= D("0.02")
        assert s.gross_exposure / s.equity == r.risk.gross_exposure_pct_after <= D(1)
        assert s.stress_loss / s.equity == r.risk.stress_loss_pct_after <= D("0.05")


def test_stress_limit_blocks_a_second_position_although_planned_risk_is_tiny():
    env = Env()
    assert env.order("a").status is OrderStatus.FILLED  # Stress 402,16 (4,02 %)
    # 10 Stueck: Stress 100,54 -> zusammen 502,70 / 9 997,0 = 5,03 % > 5 %
    r = env.order("b", qty=D(10), symbol="ETH-USD", snap_kw={"symbol": "ETH-USD"})
    assert r.reasons == (R.STRESS_LOSS_TOO_HIGH,)
    # 9 Stueck: Stress 90,486 -> zusammen 492,646 / 9 997,06 = 4,93 % -> ok
    assert env.order("c", qty=D(9), symbol="ETH-USD", snap_kw={"symbol": "ETH-USD"}).status is OrderStatus.FILLED
    s = env.executor.portfolio_state()
    assert s.stress_loss == D("402.16") + D("90.486") and s.stress_loss / s.equity <= D("0.05")


def test_gross_exposure_limit_is_enforced():
    env = Env(limits=DEFAULT_LIMITS.tightened(max_gross_exposure=D("0.3")))
    assert env.order("a", qty=D(20)).status is OrderStatus.FILLED  # 20 %
    r = env.order("b", qty=D(11), symbol="ETH-USD", snap_kw={"symbol": "ETH-USD"})  # zusammen 31 %
    assert r.reasons == (R.GROSS_EXPOSURE_TOO_HIGH,)
    assert env.order("c", qty=D(9), symbol="ETH-USD", snap_kw={"symbol": "ETH-USD"}).status is OrderStatus.FILLED
    s = env.executor.portfolio_state()
    assert s.gross_exposure == D(2900) and s.gross_exposure / s.equity <= D("0.3")


def test_exposure_and_stress_follow_the_current_market_price():
    env = Env()
    env.order("a", qty=D(20))
    env.feed(make_candle(T0, D(100), D(101.5), D(99.5), D(101)))
    s = env.executor.portfolio_state()
    assert s.gross_exposure == D(2020)  # 20 x 101
    assert s.stress_loss == D(20) * D("10.1") + D(20) * D("90.9") * D("0.0006")


def test_stale_valuation_of_an_open_position_blocks_new_trades_until_refreshed():
    env = Env()
    small_order(env, "a", "BTC-USD")
    env.clock.advance(DEFAULT_LIMITS.max_mark_age_s)
    assert small_order(env, "b", "ETH-USD").status is OrderStatus.FILLED  # genau an der Grenze: noch ok
    env.clock.advance(DEFAULT_LIMITS.max_mark_age_s + 60)  # beide Bewertungskurse jetzt aelter als 15 Minuten
    assert not env.executor.portfolio_state().valuation_complete
    r = small_order(env, "c", "SOL-USD")
    assert r.reasons == (R.EQUITY_UNCERTAIN,)  # Equity nicht bestimmbar -> keine neue Position
    for sym in ("BTC-USD", "ETH-USD"):  # frische Kerzen -> Bewertung wieder aktuell
        env.feed(flat_candle(env.clock.now() - minutes(1), symbol=sym))
    assert small_order(env, "d", "SOL-USD").status is OrderStatus.FILLED


def test_daily_loss_brake_halts_trading_until_deliberate_release():
    """Tagesverlust >= 3 % loest den Not-Aus aus. Er bleibt auch am naechsten Tag bestehen (AP-07, strenger als frueher:
    vorher loeste sich die Bremse um Mitternacht von selbst) - bis ein Mensch ihn bewusst freigibt."""
    env = Env()
    blocked_at = None
    for i in range(10):
        r = env.order("t%d" % i)
        if r.status is OrderStatus.REJECTED:
            blocked_at = i
            assert r.reasons == (R.TRADING_HALTED, R.DAILY_LOSS_LIMIT)
            break
        env.feed(make_candle(env.clock.now(), D(100), D(100), D(98), D(98.5)))  # Stop wird ausgeloest
        env.clock.advance(120)
    assert blocked_at == 7  # 7 Verluste a 0,448 % = 3,13 % >= 3 %
    assert env.executor.halt_status().reason is HaltReason.DAILY_LOSS
    assert [e.payload["reason"] for e in env.log.of_type(EventType.HALT_ACTIVATED)] == ["DAILY_LOSS"]
    env.clock.set(T0 + timedelta(days=1))
    assert env.order("next-day").reasons == (R.TRADING_HALTED, R.DAILY_LOSS_LIMIT)
    env.executor.release_halt("Eigner", RELEASE_CONFIRMATION)
    assert env.order("after-release").status is OrderStatus.FILLED
    assert env.log.of_type(EventType.HALT_RELEASED)[0].payload["operator"] == "Eigner"


def test_clock_going_backwards_does_not_reset_the_daily_loss_limit():
    """Audit H4: Eine falsch gehende Uhr (auch die eines anderen Prozesses) darf die Notbremse nicht loesen."""
    env = Env()
    for i in range(7):
        env.order("t%d" % i)
        env.feed(make_candle(env.clock.now(), D(100), D(100), D(98), D(98.5)))
        env.clock.advance(120)
    env.clock.set(env.clock.now() - timedelta(days=1))
    r = env.order("after", symbol="ETH-USD", snap_kw={"symbol": "ETH-USD"})
    assert r.reasons == (R.TRADING_HALTED, R.DAILY_LOSS_LIMIT)
    assert env.executor.portfolio_state().day_start_equity == D(10000)


def test_peak_equity_never_falls_and_drawdown_limit_blocks():
    env = Env()
    env.order()
    env.feed(make_candle(T0, D(100), D(101.5), D(99.5), D(101)))
    assert env.executor.portfolio_state().peak_equity == D("10037.6")
    env.feed(make_candle(env.clock.now(), D(101), D(101), D(99.5), D("99.5")))
    s = env.executor.portfolio_state()
    assert s.equity == D("9977.6") and s.peak_equity == D("10037.6")  # Hoechststand bleibt

    # Drawdown >= 10 % vom Hoechststand: keine neuen Positionen
    state = dataclasses.replace(Env().executor.export_state(), peak_equity=D("11112"))
    dd = Env()
    dd.executor.restore_state(state)
    assert dd.order("x").reasons == (R.TRADING_HALTED, R.DRAWDOWN_LIMIT)
    assert dd.executor.halt_status().reason is HaltReason.DRAWDOWN
    ok = Env()
    ok.executor.restore_state(dataclasses.replace(state, peak_equity=D("11111")))  # 9,99 %
    assert ok.order("x").status is OrderStatus.FILLED


# ------------------------------------------------------------------ M5: keine halben Zustaende, kein Handel ohne Protokoll


class FailingLog(InMemoryEventLog):
    fail = False

    def _persist(self, event):
        if self.fail:
            raise OSError("No space left on device")


def test_failed_logging_rolls_the_order_back_completely():
    env = Env()
    log = FailingLog(env.clock)
    env.executor._log = log
    log.fail = True
    with pytest.raises(OSError):
        env.order("x")
    assert env.executor.open_positions == () and env.executor.balance == D(10000) and log.events() == ()
    # Protokollfehler -> Not-Aus (AP-07): ohne bewusste Freigabe keine neue Position.
    assert env.executor.halt_status().reason is HaltReason.PROTOCOL_ERROR
    log.fail = False
    env.executor.release_halt("Eigner", RELEASE_CONFIRMATION)
    assert env.order("x").status is OrderStatus.FILLED  # die Order-ID wurde nicht verbraucht
    assert [e.event_type for e in log.events()] == ["HALT_RELEASED", "ORDER_FILLED"]


def test_failed_logging_rolls_back_exits_funding_and_closes():
    env = Env()
    log = FailingLog(env.clock)
    env.executor._log = log
    futures_order(env)
    env.clock.advance(4 * 3600)
    before = env.executor.export_state()
    log.fail = True
    stop_candle = make_candle(env.clock.now() - minutes(1), D(100), D(100), D(98), D(99), **FUTM)
    with pytest.raises(OSError):
        env.executor.on_candle(stop_candle)
    with pytest.raises(OSError):
        env.fund()
    with pytest.raises(OSError):
        close(env, market_type=MarketType.FUTURES, funding=D(0))
    assert env.executor.export_state() == before and env.executor.closed_trades == ()
    log.fail = False
    assert len(env.executor.on_candle(stop_candle)) == 1  # danach normal verarbeitbar


# ------------------------------------------------------------------ Buchhaltung und Rundung


def test_balance_always_equals_start_plus_net_pnl_after_all_trades_closed():
    env = Env()
    env.order("a")  # Take-Profit
    env.feed(make_candle(T0, D(100), D(103), D(100), D(102)))
    futures_order(env, "b")  # Funding, dann Stop
    env.clock.advance(4 * 3600)
    env.fund()
    env.feed(make_candle(env.clock.now(), D(100), D(100), D(98), D(98), **FUTM))
    short_order(env, "c")  # Schliessung auf Signal
    env.clock.advance(60)
    close(env, market_type=MarketType.FUTURES, mid=D(99), funding=D(0))
    futures_order(env, "d", qty=D(4), lev=D(5), stop=D(90), tp=D(110))  # Liquidation
    env.feed(make_candle(env.clock.now(), D(70), D(71), D(69), D(70), **FUTM))
    trades = env.executor.closed_trades
    assert [t.exit_reason for t in trades] == [ExitReason.TAKE_PROFIT, ExitReason.STOP_LOSS,
                                               ExitReason.SIGNAL, ExitReason.LIQUIDATION]
    assert env.executor.open_positions == ()
    assert env.executor.balance == env.equity0 + sum(t.net_pnl for t in trades)
    assert len(env.log.of_type(EventType.POSITION_CLOSED)) == 4 and env.log.verify().ok


def test_accounting_is_exact_with_awkward_prices_and_all_amounts_are_rounded_to_the_quantum():
    rng = random.Random(3)
    env = Env(equity=D(250000), costs=CostModel())
    for i in range(150):
        price = D(str(round(rng.uniform(0.5, 60000), 7)))
        qty = D(str(round(rng.uniform(0.0001, 0.02) * 60000 / float(price), 7)))
        futures = rng.random() < 0.5
        side = rng.choice([Side.LONG, Side.SHORT]) if futures else Side.LONG
        sg = side.sign
        setup_kw = dict(symbol="BTC-USD", side=side, entry=price, stop=price * (1 - sg * D("0.0137")),
                        tp=price * (1 + sg * D("0.0291")), market_type=MarketType.FUTURES if futures else MarketType.SPOT)
        env.validator.reset(inst("BTC-USD", setup_kw["market_type"]))
        r = env.order("q%d" % i, qty=qty, lev=D(3) if futures else D(1),
                      snap_kw=dict(symbol="BTC-USD", mid=price, half_spread=price * D("0.0001"),
                                   market_type=setup_kw["market_type"], funding=D("0.000137") if futures else None),
                      **setup_kw)
        if r.status is not OrderStatus.FILLED:
            continue
        mt = setup_kw["market_type"]
        if futures:
            env.clock.advance(3600)
            env.fund(rate=D("0.000137"), mark=price * D("1.0003"))
        move = D(str(round(rng.uniform(-0.04, 0.04), 6)))
        c = price * (1 + move)
        candle = make_candle(env.clock.now().replace(second=0, microsecond=0), price, max(price, c) * D("1.001"),
                             min(price, c) * D("0.999"), c, market_type=mt)
        env.clock.advance(120)
        if not env.executor.on_candle(candle):
            assert close(env, market_type=mt, mid=c, half_spread=c * D("0.0001"),
                         funding=D(0) if futures else None, rid="c%d" % i).closed
        env.clock.advance(60)
    trades = env.executor.closed_trades
    assert len(trades) > 100 and env.executor.open_positions == ()
    assert env.executor.balance == env.equity0 + sum(t.net_pnl for t in trades)  # exakt, ohne Rundungsrest
    for t in trades:
        for amount in (t.gross_pnl, t.fees, t.funding, t.net_pnl, t.risk_at_open):
            assert amount == amount.quantize(MONEY_QUANTUM)
    assert {t.exit_reason for t in trades} >= {ExitReason.STOP_LOSS, ExitReason.TAKE_PROFIT, ExitReason.SIGNAL}


def test_executor_requires_positive_decimal_equity():
    with pytest.raises(ValueError):
        Env(equity=D(0))
    with pytest.raises(ValueError):
        Env(equity=10000.0)


# ------------------------------------------------------------------ Luecken aus der Gegenprobe (Stufe 1, Abschnitt E)


def test_error_after_the_fill_leaves_neither_a_position_nor_a_log_entry():
    """Scheitert eine Operation NACH dem Erzeugen des Ereignisses, darf auch im Protokoll nichts stehen bleiben."""
    env = Env()
    real, calls = env.executor.portfolio_state, []

    def flaky():
        calls.append(1)
        if len(calls) == 2:  # 1. Aufruf: Risikopruefung; 2. Aufruf: nach der Ausfuehrung
            raise RuntimeError("unerwarteter Fehler nach der Ausfuehrung")
        return real()

    env.executor.portfolio_state = flaky
    with pytest.raises(RuntimeError):
        env.order("x")
    del env.executor.portfolio_state
    assert env.executor.open_positions == () and env.executor.balance == D(10000)
    assert env.log.events() == ()  # kein "ORDER_FILLED" fuer einen Trade, den es nicht gibt
    assert env.order("x").status is OrderStatus.FILLED
    assert [e.event_type for e in env.log.events()] == ["ORDER_FILLED"]


def test_short_stop_is_filled_with_slippage_against_the_trader():
    """Short, Stop 101, 5 bp Stop-Slippage: zurueckgekauft wird zu 101,0505 - nicht guenstiger."""
    costs = CostModel()
    assert costs.stop_exit_price(Side.SHORT, D(101)) == D("101.0505")
    assert costs.stop_exit_price(Side.LONG, D(99)) == D("98.9505")
    # geplanter Hoechstverlust je Stueck (Einstieg 100, Stop 101): 1,0505 + 0,06 + 0,0606303
    assert costs.worst_case_loss(Side.SHORT, D(1), D(100), D(101)) == D("1.0505") + D("0.06") + D("0.0606303")

    env = Env(costs=costs)
    assert short_order(env).status is OrderStatus.FILLED  # verkauft zu 100 - 2 bp = 99,98
    (t,) = env.feed(make_candle(T0, D(100), D("101.2"), D(100), D(101), **FUTM))
    assert t.exit_reason is ExitReason.STOP_LOSS and t.entry_price == D("99.98") and t.exit_price == D("101.0505")
    assert t.gross_pnl == D("-42.82")  # (99,98 - 101,0505) x 40
    assert t.fees == D("2.39952") + D("2.425212")
    assert t.net_pnl == D("-47.644732")
    assert -t.net_pnl == t.risk_at_open - D("0.39992")  # geplant war zusaetzlich ein Funding-Puffer


def test_closing_on_signal_updates_the_last_known_price():
    env = Env()
    env.order()
    env.clock.advance(60)
    assert close(env, mid=D(101), half_spread=D("0.05")).closed
    mark = env.executor.export_state().marks[inst("BTC-USD", MarketType.SPOT)]
    assert (mark.price, mark.time) == (D(101), env.clock.now())
