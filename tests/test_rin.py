"""RIN: regelbasierte Validierung von Signalen und Kursdaten (synthetisch, mit Grenzfaellen)."""

import dataclasses
import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from helpers import D, NO_SLIP, T0, inst, make_candle, make_setup, snapshot

from trading.agents import AgentName, MarketContext, RinConfig, RuleBasedRin
from trading.clock import FixedClock
from trading.domain.models import MarketType, Side
from trading.domain.reasons import RejectReason as R
from trading.domain.universe import TradingUniverse, is_well_formed_symbol
from trading.execution.costs import CostModel
from trading.safety.limits import LimitViolationError

UNIVERSE = TradingUniverse.of(spot=["BTC-USD", "ETH-USD"], futures=["BTC-USD"])
FUT = {"market_type": MarketType.FUTURES, "funding": D("0.0001")}


def make_rin(config=None, costs=NO_SLIP, universe=UNIVERSE):
    clock = FixedClock(T0)
    return clock, RuleBasedRin(universe, clock, config, costs)


def check(setup=None, snap_kw=None, rin=None, candles=(), snap=None):
    if rin is None:
        _, rin = make_rin()
    setup = setup or make_setup()
    snap = snap or snapshot(**(snap_kw or {}))
    return rin.validate(setup, MarketContext(snap, tuple(candles)))


def short_futures(**kw):
    kw.setdefault("entry", D(100))
    kw.setdefault("stop", D(101))
    kw.setdefault("tp", D(98))
    return make_setup(market_type=MarketType.FUTURES, side=Side.SHORT, **kw)


# ------------------------------------------------------------------ Freigabe


def test_valid_spot_long_is_approved_with_score():
    op = check()
    assert op.approve and op.agent is AgentName.RIN and op.reject_reasons == ()
    assert D(0) < op.score <= D(1)


def test_valid_futures_short_and_long_are_approved():
    assert check(short_futures(), FUT).approve
    assert check(make_setup(market_type=MarketType.FUTURES), FUT).approve


# ------------------------------------------------------------------ Handelspaare


@pytest.mark.parametrize(
    "symbol",
    ["FOO-USD", "BTCUSD", "btc-usd", "BTC/USD", "BTC-USD ", " BTC-USD", "", "BTC-USD;DROP", "A-USD", None, 123],
)
def test_invalid_trading_pairs_are_blocked(symbol):
    op = check(make_setup(symbol=symbol), {"symbol": symbol})
    assert not op.approve and R.INVALID_SYMBOL in op.reject_reasons


def test_pair_must_be_allowed_for_that_market_type():
    only_spot = TradingUniverse.of(spot=["ETH-USD"], futures=["BTC-USD"])
    _, rin = make_rin(universe=only_spot)
    # ETH-USD gibt es nur als Spot, nicht als Futures
    op = check(make_setup(symbol="ETH-USD", market_type=MarketType.FUTURES, side=Side.SHORT,
                          stop=D(101), tp=D(98)),
               dict(FUT, symbol="ETH-USD"), rin=rin)
    assert R.INVALID_SYMBOL in op.reject_reasons


def test_empty_universe_allows_nothing():
    _, rin = make_rin(universe=TradingUniverse())
    assert R.INVALID_SYMBOL in check(rin=rin).reject_reasons


def test_universe_rejects_malformed_whitelist_entries_and_odd_inputs():
    with pytest.raises(ValueError):
        TradingUniverse.of(spot=["btc-usd"])
    assert UNIVERSE.allows(inst("BTC-USD", MarketType.SPOT))
    assert not UNIVERSE.allows(None) and not UNIVERSE.allows("BTC-USD")  # nur vollstaendige Instrument-Schluessel
    assert not UNIVERSE.allows(dataclasses.replace(inst(), market_type="spot"))  # Enum erforderlich
    assert is_well_formed_symbol("BTC-USD") and not is_well_formed_symbol(b"BTC-USD")


def test_instrument_must_match_in_exchange_market_type_and_pair():
    """H1: Dasselbe Paar an einer anderen Boerse oder in einer anderen Marktart ist nicht erlaubt,
    nur weil es anderswo auf der Whitelist steht."""
    from trading.domain.instrument import Exchange

    coinbase_spot = TradingUniverse.of(spot=["BTC-USD"], exchange=Exchange.COINBASE)
    _, rin = make_rin(universe=coinbase_spot)
    assert R.INVALID_SYMBOL in check(rin=rin).reject_reasons  # synthetische Boerse: nicht erlaubt
    cb = check(make_setup(exchange=Exchange.COINBASE), {"exchange": Exchange.COINBASE}, rin=rin)
    assert cb.approve
    # Marktdaten einer ANDEREN Boerse fuer dieses Setup: abgelehnt
    mixed = check(make_setup(exchange=Exchange.COINBASE), {"exchange": Exchange.BITGET}, rin=rin)
    assert R.SYMBOL_MISMATCH in mixed.reject_reasons
    merged = coinbase_spot.merged(TradingUniverse.of(futures=["BTC-USD"], exchange=Exchange.BITGET))
    assert merged.allows(inst("BTC-USD", MarketType.FUTURES, Exchange.BITGET))
    assert not merged.allows(inst("BTC-USD", MarketType.SPOT, Exchange.BITGET))


def test_history_candles_of_another_instrument_are_rejected():
    futures_candle = make_candle(T0 - timedelta(minutes=1), D(100), D("100.5"), D("99.5"), D(100),
                                 market_type=MarketType.FUTURES)
    assert R.BAD_CANDLE in check(candles=[futures_candle]).reject_reasons  # Spot-Setup, Futures-Kerze


def test_snapshot_for_another_pair_is_a_mismatch():
    op = check(snap_kw={"symbol": "ETH-USD"})
    assert R.SYMBOL_MISMATCH in op.reject_reasons


def test_snapshot_for_another_market_type_is_a_mismatch():
    op = check(snap_kw=FUT)  # Setup ist Spot, Daten sind Futures
    assert R.SYMBOL_MISMATCH in op.reject_reasons


# ------------------------------------------------------------------ Spot-Short


def test_spot_short_is_blocked():
    op = check(make_setup(side=Side.SHORT, stop=D(101), tp=D(98)))
    assert R.SPOT_SHORT_NOT_ALLOWED in op.reject_reasons and not op.approve


# ------------------------------------------------------------------ Marktdaten


@pytest.mark.parametrize(
    "snap_kw, reason",
    [
        ({"ts": T0 - timedelta(seconds=16)}, R.STALE_DATA),
        ({"ts": T0 + timedelta(seconds=30)}, R.FUTURE_TIMESTAMP),
        ({"mid": D(0)}, R.NON_POSITIVE_PRICE),
        ({"mid": D(-1)}, R.NON_POSITIVE_PRICE),
        ({"half_spread": D("0.5")}, R.SPREAD_TOO_WIDE),  # 100 bp > 20 bp
    ],
)
def test_bad_market_data_is_rejected(snap_kw, reason):
    op = check(snap_kw=snap_kw)
    assert not op.approve and reason in op.reject_reasons


def test_data_exactly_at_age_limit_is_accepted_and_one_second_more_is_not():
    assert check(snap_kw={"ts": T0 - timedelta(seconds=15)}).approve
    assert not check(snap_kw={"ts": T0 - timedelta(seconds=16)}).approve


def test_spread_boundary():
    # 20 bp Spread (half_spread 0.1 bei Kurs 100) ist noch erlaubt, 0.11 nicht.
    # (weiteres Ziel, damit das Chance-Risiko-Verhaeltnis den Spread-Kosten standhaelt)
    wide_tp = make_setup(tp=D(104))
    assert check(wide_tp, {"half_spread": D("0.1")}).approve
    assert R.SPREAD_TOO_WIDE in check(wide_tp, {"half_spread": D("0.11")}).reject_reasons


def test_spread_costs_are_part_of_the_net_reward_risk():
    # Gleiches Setup: ohne Spread ok (CRV 1.54), mit 20 bp Spread zu wenig (Long kauft zum Ask).
    assert check(make_setup(tp=D("101.80"))).approve
    assert R.REWARD_RISK_TOO_LOW in check(make_setup(tp=D("101.80")),
                                          {"half_spread": D("0.1")}).reject_reasons


def test_incomplete_snapshot_fields_are_rejected():
    base = snapshot()
    for field in ("bid", "ask", "last", "timestamp"):
        op = check(snap=dataclasses.replace(base, **{field: None}))
        assert not op.approve and R.MISSING_DATA in op.reject_reasons, field


def test_nan_infinity_and_float_prices_are_rejected_not_crashing():
    base = snapshot()
    for bad in (D("NaN"), D("Infinity"), 100.0):
        op = check(snap=dataclasses.replace(base, bid=bad))
        assert not op.approve and R.NON_FINITE_VALUE in op.reject_reasons


def test_crossed_book_is_rejected():
    op = check(snap=dataclasses.replace(snapshot(), bid=D(101), ask=D(100)))
    assert R.CROSSED_BOOK in op.reject_reasons


def test_naive_timestamp_is_rejected():
    op = check(snap=snapshot(ts=T0.replace(tzinfo=None)))
    assert R.NAIVE_TIMESTAMP in op.reject_reasons


def test_futures_without_funding_rate_are_rejected():
    op = check(make_setup(market_type=MarketType.FUTURES), {"market_type": MarketType.FUTURES})
    assert R.FUNDING_MISSING in op.reject_reasons


def test_price_jump_between_two_checks_is_rejected():
    _, rin = make_rin()
    assert check(rin=rin).approve
    op = check(rin=rin, snap_kw={"mid": D(130)}, setup=make_setup(entry=D(130), stop=D(128), tp=D(135)))
    assert R.PRICE_JUMP in op.reject_reasons


def test_inconsistent_candle_is_rejected():
    bad = make_candle(T0 - timedelta(minutes=1), D(100), D(99), D(98), D(101))
    assert R.BAD_CANDLE in check(candles=[bad]).reject_reasons


def test_overlapping_or_unsorted_candles_are_rejected():
    c1 = make_candle(T0 - timedelta(minutes=2), D(100), D(101), D(99), D(100))
    c2 = make_candle(T0 - timedelta(minutes=3), D(100), D(101), D(99), D(100))
    assert R.BAD_CANDLE in check(candles=[c1, c2]).reject_reasons


def test_last_candle_far_from_current_price_is_rejected():
    c = make_candle(T0 - timedelta(minutes=1), D(108), D(110), D(107), D(110))
    op = check(candles=[c])  # Tick 100 vs Kerze 110 = 9 % Abweichung
    assert R.CANDLE_PRICE_MISMATCH in op.reject_reasons


def test_consistent_candles_pass():
    c1 = make_candle(T0 - timedelta(minutes=2), D(100), D("100.5"), D("99.5"), D(100))
    c2 = make_candle(T0 - timedelta(minutes=1), D(100), D("100.5"), D("99.5"), D("100.2"))
    assert check(candles=[c1, c2]).approve


# ------------------------------------------------------------------ Setup-Alter


def test_setup_age_boundary():
    clock, rin = make_rin()
    old = dataclasses.replace(make_setup(), created_at=T0 - timedelta(seconds=60))
    assert check(old, rin=rin).approve
    older = dataclasses.replace(make_setup(), created_at=T0 - timedelta(seconds=61))
    assert R.STALE_SETUP in check(older, rin=rin).reject_reasons


def test_setup_from_the_future_or_without_timezone_is_rejected():
    future = dataclasses.replace(make_setup(), created_at=T0 + timedelta(minutes=5))
    assert R.STALE_SETUP in check(future).reject_reasons
    naive = dataclasses.replace(make_setup(), created_at=T0.replace(tzinfo=None))
    assert R.MISSING_DATA in check(naive).reject_reasons


# ------------------------------------------------------------------ Entry / Stop / Take-Profit


@pytest.mark.parametrize(
    "field, value, reason",
    [
        ("stop_loss", None, R.MISSING_DATA),
        ("take_profit", None, R.MISSING_DATA),
        ("entry", None, R.MISSING_DATA),
        ("stop_loss", D("NaN"), R.NON_FINITE_VALUE),
        ("take_profit", D("Infinity"), R.NON_FINITE_VALUE),
        ("entry", 100.0, R.NON_FINITE_VALUE),  # float statt Decimal
        ("stop_loss", D(0), R.NON_POSITIVE_PRICE),
        ("stop_loss", D(-5), R.NON_POSITIVE_PRICE),
        ("take_profit", D(0), R.NON_POSITIVE_PRICE),
    ],
)
def test_incomplete_or_invalid_setup_prices_are_rejected_without_crashing(field, value, reason):
    op = check(dataclasses.replace(make_setup(), **{field: value}))
    assert not op.approve and reason in op.reject_reasons


def test_stop_and_take_profit_on_wrong_side():
    assert R.STOP_WRONG_SIDE in check(make_setup(stop=D(101))).reject_reasons
    assert R.STOP_WRONG_SIDE in check(make_setup(stop=D(100))).reject_reasons  # Stop == Entry
    assert R.TAKE_PROFIT_WRONG_SIDE in check(make_setup(tp=D(99))).reject_reasons
    assert R.TAKE_PROFIT_WRONG_SIDE in check(make_setup(tp=D(100))).reject_reasons
    assert R.STOP_WRONG_SIDE in check(short_futures(stop=D(99)), FUT).reject_reasons
    assert R.TAKE_PROFIT_WRONG_SIDE in check(short_futures(tp=D(102)), FUT).reject_reasons


def test_market_already_beyond_stop_or_target_is_rejected():
    # Setup sieht aus Entry-Sicht korrekt aus, aber der Markt ist laengst am Stop vorbei.
    s = make_setup(entry=D("99.6"), stop=D("99.3"), tp=D(103))
    op = check(s, {"mid": D(99)})
    assert R.STOP_WRONG_SIDE in op.reject_reasons
    s2 = make_setup(entry=D("100.2"), stop=D(99), tp=D("100.4"))
    assert R.TAKE_PROFIT_WRONG_SIDE in check(s2, {"mid": D("100.5")}).reject_reasons


def test_entry_deviation_boundary():
    ok = make_setup(entry=D("100.5"), stop=D(99), tp=D(103))  # genau 0,5 %
    assert check(ok).approve
    far = make_setup(entry=D("100.51"), stop=D(99), tp=D(103))
    assert R.ENTRY_DEVIATES_FROM_MARKET in check(far).reject_reasons


def test_stop_too_tight_compared_to_costs():
    # 20 bp Abstand: groesser als das 10-bp-Minimum, aber unter 3x Round-Trip-Kosten (36 bp)
    op = check(make_setup(stop=D("99.8"), tp=D(104)))
    assert R.STOP_TOO_TIGHT in op.reject_reasons


def test_stop_distance_boundaries():
    assert check(make_setup(stop=D("99.6"), tp=D(104))).approve  # 40 bp >= 36 bp
    assert R.STOP_TOO_TIGHT in check(make_setup(stop=D("99.7"), tp=D(104))).reject_reasons  # 30 bp
    assert check(make_setup(stop=D(90), tp=D(125))).approve  # genau 10 %
    assert R.STOP_TOO_WIDE in check(make_setup(stop=D("89.9"), tp=D(125))).reject_reasons


def test_stop_cost_floor_grows_with_slippage_and_spread():
    _, rin = make_rin(costs=CostModel())  # mit Slippage: 12 + 7 + 2 = 21 bp -> Minimum 63 bp
    assert R.STOP_TOO_TIGHT in check(make_setup(stop=D("99.5"), tp=D(104)), rin=rin).reject_reasons
    assert check(make_setup(stop=D("99.3"), tp=D(104)), rin=rin).approve


def test_net_reward_risk_boundary_after_costs():
    # Stop 99 -> Risiko je Stueck 1,1194. Ziel 101.75 -> CRV 1.49 (zu wenig), 101.80 -> 1.54 (ok).
    below = check(make_setup(tp=D("101.75")))
    assert R.REWARD_RISK_TOO_LOW in below.reject_reasons
    assert check(make_setup(tp=D("101.80"))).approve


def test_gross_reward_risk_of_1_5_is_not_enough_after_costs():
    # brutto genau 1:1,5 (Stop 99, Ziel 101.5) -> netto deutlich darunter
    assert R.REWARD_RISK_TOO_LOW in check(make_setup(tp=D("101.5"))).reject_reasons


def test_funding_reduces_net_reward_risk_for_futures():
    s = make_setup(market_type=MarketType.FUTURES, tp=D("101.80"))
    assert check(s, dict(FUT, funding=D(0))).approve
    high = check(s, dict(FUT, funding=D("0.01")))  # 1 % Funding je Intervall
    assert R.REWARD_RISK_TOO_LOW in high.reject_reasons


# ------------------------------------------------------------------ Verhalten als Ganzes


def test_all_violations_are_reported_together_with_plain_text():
    op = check(make_setup(side=Side.SHORT, symbol="XXX-USD", stop=D(99), tp=D(101)), {"symbol": "XXX-USD"})
    assert {R.INVALID_SYMBOL, R.SPOT_SHORT_NOT_ALLOWED, R.STOP_WRONG_SIDE} <= set(op.reject_reasons)
    assert len(op.reasons) >= 3 and all(isinstance(t, str) and t for t in op.reasons)
    assert op.score == D(0)


def test_rejection_never_raises_for_garbage_setup():
    for garbage in (
        dataclasses.replace(make_setup(), side="long", entry="x"),
        dataclasses.replace(make_setup(), instrument="BTC-USD"),
        dataclasses.replace(make_setup(), instrument=None),
        dataclasses.replace(make_setup(), instrument=dataclasses.replace(inst(), market_type="spot")),
    ):
        op = check(garbage)
        assert not op.approve and op.reject_reasons


def test_config_cannot_loosen_data_limits_or_use_nonpositive_thresholds():
    clock = FixedClock(T0)
    with pytest.raises(LimitViolationError):
        RuleBasedRin(UNIVERSE, clock, RinConfig(max_data_age_s=600))
    with pytest.raises(LimitViolationError):
        RuleBasedRin(UNIVERSE, clock, RinConfig(max_spread_bps=D(500)))
    with pytest.raises(ValueError):
        RinConfig(min_net_reward_risk=D(0))


def test_stricter_config_is_respected():
    clock, rin = make_rin(RinConfig(max_data_age_s=5))
    assert R.STALE_DATA in check(snap_kw={"ts": T0 - timedelta(seconds=6)}, rin=rin).reject_reasons


def test_concurrent_validation_gives_identical_results():
    _, rin = make_rin()
    results, errors = [], []

    def work(i):
        try:
            setup = make_setup() if i % 2 == 0 else make_setup(side=Side.SHORT, stop=D(101), tp=D(98))
            results.append((i % 2, rin.validate(setup, MarketContext(snapshot())).approve))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(60)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert {r for r in results if r[0] == 0} == {(0, True)}  # Long: immer freigegeben
    assert {r for r in results if r[0] == 1} == {(1, False)}  # Spot-Short: immer blockiert
    assert len(results) == 60
