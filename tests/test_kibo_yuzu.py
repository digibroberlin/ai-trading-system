"""Marktphasen (AP-17) und die regelbasierten Agenten KIBO (Technik) und YUZU (Marktumfeld), inklusive der optionalen
KI-Zweitmeinung ueber den Kosten-Orchestrator."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from bt_helpers import BTC, ETHF, bars, config, synthetic
from helpers import T0

from trading.agents.ai_review import AIReview, rule_confidence
from trading.agents.base import AgentName
from trading.agents.contracts import MarketContext
from trading.agents.kibo_rules import KiboConfig, RuleBasedKibo
from trading.agents.yuzu_rules import RuleBasedYuzu, YuzuConfig
from trading.analysis.regime import RegimeConfig, Trend, Volatility, detect_regime
from trading.audit.events import EventType
from trading.backtest import BacktestEngine
from trading.domain.models import Candle, Horizon, Side, TradeSetup
from trading.domain.reasons import RejectReason as R
from trading.marketdata.synthetic import snapshot_from_price
from trading.strategies import build_strategy

D = Decimal
HOUR = timedelta(hours=1)


def candles_from(closes, wiggle=D("0.2"), instrument=BTC, volumes=None):
    rows, prev = [], closes[0]
    for c in closes:
        rows.append([prev, max(prev, c) + wiggle, min(prev, c) - wiggle, c])
        prev = c
    out = bars(instrument, rows)
    if volumes is not None:
        out = [Candle(c.instrument, c.open_time, c.interval_s, c.open, c.high, c.low, c.close, D(v))
               for c, v in zip(out, volumes)]
    return tuple(out)


def rising(n=150, step="0.6"):
    """Aufwaertstrend mit Ruecksetzern: abwechselnd +2,2 und -1,0 (netto +0,6 je Kerze, RSI um 69)."""
    return [D(100) + D(step) * i + (D("0.8") if i % 2 else D("-0.8")) for i in range(n)]


def falling(n=150, step="0.6"):
    return [D(200) - D(step) * i - (D("0.8") if i % 2 else D("-0.8")) for i in range(n)]


def zigzag(n=150, amp="1"):
    return [D(100) + (D(amp) if i % 2 else -D(amp)) for i in range(n)]


def context(candles, spread_bps=D(2), funding=None, extras=None, instrument=None):
    inst = instrument or candles[-1].instrument
    snap = snapshot_from_price(inst, candles[-1].close_time, candles[-1].close, spread_bps, funding)
    return MarketContext(snap, tuple(candles), extras or {})


def setup(side=Side.LONG, entry=None, stop_pct="0.02", horizon=Horizon.INTRADAY, instrument=BTC, candles=None,
          sid="s1"):
    price = entry if entry is not None else candles[-1].close
    sign = 1 if side is Side.LONG else -1
    stop = price * (1 - sign * D(stop_pct))
    target = price * (1 + sign * D(stop_pct) * D("2.5"))
    return TradeSetup(sid, instrument, side, price, stop, target, horizon, "test", T0)


# ------------------------------------------------------------------ Marktphasen


def test_trend_and_sideways_are_recognised():
    assert detect_regime(candles_from(rising())).trend is Trend.UP
    assert detect_regime(candles_from(falling())).trend is Trend.DOWN
    flat = detect_regime(candles_from(zigzag()))
    assert flat.trend is Trend.SIDEWAYS and flat.efficiency == 0
    assert detect_regime(candles_from(rising(60))) is None  # zu wenig Kerzen: keine geratene Phase


def test_volatility_is_ranked_against_its_own_recent_history():
    calm_then_wild = candles_from(zigzag(140, "0.2") + zigzag(10, "4"), wiggle=D("0.1"))
    wild_then_calm = candles_from(zigzag(140, "4") + zigzag(10, "0.2"), wiggle=D("0.1"))
    assert detect_regime(calm_then_wild).volatility is Volatility.HIGH
    assert detect_regime(wild_then_calm).volatility is Volatility.LOW
    assert detect_regime(candles_from([D(100)] * 150)).volatility is Volatility.NORMAL  # gleichbleibend: Mitte
    payload = detect_regime(calm_then_wild).as_payload()
    assert payload["volatility"] == "HIGH" and all(isinstance(v, str) for v in payload.values())


@pytest.mark.parametrize("bad", [dict(trend_period=0), dict(low_rank=D("0.9")), dict(trend_efficiency=D("1.5"))])
def test_invalid_regime_settings_are_rejected(bad):
    with pytest.raises(ValueError):
        RegimeConfig(**bad)


# ------------------------------------------------------------------ KIBO


def test_kibo_without_enough_candles_gives_no_verdict_and_does_not_block():
    c = candles_from(rising(30))
    op = RuleBasedKibo().evaluate(setup(candles=c), context(c))
    assert op.agent is AgentName.KIBO and op.approve and op.score == D("0.5") and "zu wenig Kerzen" in op.reasons[0]


def test_kibo_approves_a_long_with_the_trend_and_rejects_against_it():
    up, down = candles_from(rising()), candles_from(falling())
    op = RuleBasedKibo().evaluate(setup(candles=up), context(up))
    assert op.approve and op.score >= D("0.7") and any("Trend in Setup-Richtung" in r for r in op.reasons)
    op = RuleBasedKibo().evaluate(setup(candles=down), context(down))
    assert not op.approve and op.reject_reasons == (R.AGAINST_TREND,)
    relaxed = RuleBasedKibo(KiboConfig(block_counter_trend=False)).evaluate(setup(candles=down), context(down))
    assert R.AGAINST_TREND not in relaxed.reject_reasons
    short = RuleBasedKibo().evaluate(setup(Side.SHORT, candles=down, instrument=ETHF), context(down))
    assert short.approve and short.score >= D("0.7")


def test_kibo_rejects_a_stop_inside_the_normal_noise():
    c = candles_from(zigzag(150, "2"))  # ATR ~ 4
    op = RuleBasedKibo().evaluate(setup(candles=c, stop_pct="0.005"), context(c))  # Stop 0,5 = weit unter 0,5 x ATR
    assert not op.approve and R.STOP_INSIDE_NOISE in op.reject_reasons and op.score == 0


def test_kibo_score_drops_with_exhausted_rsi_and_weak_volume():
    steep = candles_from([D(100) + 2 * i for i in range(150)])  # nur Anstiege: RSI 100
    normal = candles_from(rising())
    steep_score = RuleBasedKibo().evaluate(setup(candles=steep), context(steep)).score
    assert steep_score < RuleBasedKibo().evaluate(setup(candles=normal), context(normal)).score
    quiet = candles_from(rising(), volumes=[10] * 149 + [2])
    op = RuleBasedKibo().evaluate(setup(candles=quiet), context(quiet))
    assert any("schwaches Volumen" in r for r in op.reasons)


def test_kibo_weak_score_is_a_soft_rejection():
    c = candles_from(zigzag())
    op = RuleBasedKibo(KiboConfig(min_score=D("0.9"))).evaluate(setup(candles=c), context(c))
    assert not op.approve and op.reject_reasons == (R.TECHNICALS_WEAK,)


# ------------------------------------------------------------------ YUZU


def test_yuzu_rejects_extreme_volatility_bad_funding_and_illiquid_markets():
    wild = candles_from(zigzag(150, "6"))  # Spanne ~12 % je Kerze
    op = RuleBasedYuzu().evaluate(setup(candles=wild, stop_pct="0.08"), context(wild))
    assert not op.approve and R.VOLATILITY_TOO_HIGH in op.reject_reasons
    f = candles_from(zigzag(), instrument=ETHF)
    long_pays = RuleBasedYuzu().evaluate(setup(candles=f, instrument=ETHF), context(f, funding=D("0.003")))
    assert not long_pays.approve and long_pays.reject_reasons == (R.FUNDING_UNFAVORABLE,)
    short_gets = RuleBasedYuzu().evaluate(setup(Side.SHORT, candles=f, instrument=ETHF), context(f, funding=D("0.003")))
    assert short_gets.approve and short_gets.score > D("0.5")  # Short erhaelt Funding
    crowded = RuleBasedYuzu().evaluate(setup(candles=f, instrument=ETHF), context(f, funding=D("0.001")))
    assert crowded.approve and crowded.score == D("0.35") and any("ueberfuellt" in r for r in crowded.reasons)
    c = candles_from(zigzag())
    wide = RuleBasedYuzu().evaluate(setup(candles=c, stop_pct="0.005"), context(c, spread_bps=D(30)))
    assert not wide.approve and R.LIQUIDITY_TOO_LOW in wide.reject_reasons
    dried = candles_from(zigzag(), volumes=[10] * 147 + [1, 1, 1])
    assert R.LIQUIDITY_TOO_LOW in RuleBasedYuzu().evaluate(setup(candles=dried), context(dried)).reject_reasons


def test_yuzu_weighs_scalping_volatility_and_sentiment():
    wild = candles_from(zigzag(140, "0.2") + zigzag(10, "1.5"), wiggle=D("0.1"))
    scalp = RuleBasedYuzu().evaluate(setup(candles=wild, horizon=Horizon.SCALP, stop_pct="0.03"), context(wild))
    swing = RuleBasedYuzu().evaluate(setup(candles=wild, horizon=Horizon.SWING, stop_pct="0.03"), context(wild))
    assert scalp.score < swing.score and any("Scalping" in r for r in scalp.reasons)
    c = candles_from(zigzag())
    against = RuleBasedYuzu().evaluate(setup(candles=c), context(c, extras={"sentiment": "-0.8"}))
    assert against.score == D("0.35") and any("Stimmung" in r for r in against.reasons)
    garbage = RuleBasedYuzu().evaluate(setup(candles=c), context(c, extras={"sentiment": "kaputt"}))
    assert garbage.score == D("0.5")  # unbrauchbare Zusatzdaten werden ignoriert, nicht geraten
    assert not RuleBasedYuzu(YuzuConfig(min_score=D("0.4"))).evaluate(setup(candles=c),
                                                                     context(c, extras={"sentiment": -1})).approve


@pytest.mark.parametrize("make", [lambda: KiboConfig(oversold=D(80)), lambda: KiboConfig(rsi_period=0),
                                  lambda: YuzuConfig(crowded_funding=D("0.01")), lambda: YuzuConfig(min_score=D(-1)),
                                  lambda: AIReview(None, D(-1))])  # type: ignore[arg-type]
def test_invalid_agent_settings_are_rejected(make):
    with pytest.raises(ValueError):
        make()


# ------------------------------------------------------------------ KI-Zweitmeinung ueber den Orchestrator


def review_env(text='{"approve": false, "confidence": 0.9}', budget=None):
    from test_ai_orchestration import BUDGET, Env, FakeProvider

    return Env(FakeProvider(text=text), budget=budget or BUDGET)


def test_uncertain_rules_ask_the_orchestrator_and_a_more_confident_ai_answer_decides():
    env = review_env()
    c = candles_from(zigzag())
    op = RuleBasedKibo(review=AIReview(env.orch, D(50))).evaluate(setup(candles=c), context(c))
    assert len(env.provider.calls) == 1 and not op.approve and op.reject_reasons == (R.TECHNICALS_WEAK,)
    assert any("KI-Zweitmeinung" in r for r in op.reasons)
    sent = env.provider.calls[0].prompt
    assert '"trend":"SIDEWAYS"' in sent and '"side":"long"' in sent  # kompaktes, strukturiertes JSON statt Fliesstext


def test_without_budget_or_value_the_rules_decide_and_nothing_is_paid():
    from trading.ai.budget import NO_BUDGET

    c = candles_from(zigzag())
    for env, value in ((review_env(budget=NO_BUDGET), D(50)), (review_env(), D(0))):
        op = RuleBasedYuzu(review=AIReview(env.orch, value)).evaluate(setup(candles=c), context(c))
        assert op.approve and env.provider.calls == []


def test_hard_rejections_never_reach_the_ai():
    env = review_env('{"approve": true, "confidence": 0.99}')
    down = candles_from(falling())
    op = RuleBasedKibo(review=AIReview(env.orch, D(50))).evaluate(setup(candles=down), context(down))
    assert not op.approve and op.reject_reasons == (R.AGAINST_TREND,) and env.provider.calls == []


def test_ai_answer_without_a_clear_decision_is_ignored():
    env = review_env('{"confidence": 0.95}')  # kein approve
    c = candles_from(zigzag())
    op = RuleBasedKibo(review=AIReview(env.orch, D(50))).evaluate(setup(candles=c), context(c))
    assert op.approve and len(env.provider.calls) == 1 and not any("KI-Zweitmeinung" in r for r in op.reasons)


def test_rule_confidence_grows_with_the_distance_from_the_threshold():
    assert rule_confidence(D("0.35"), D("0.35")) == D("0.5")
    assert rule_confidence(D("0.6"), D("0.35")) == D("0.75") and rule_confidence(D("1"), D("0")) == D("0.95")


# ------------------------------------------------------------------ alle sechs Agenten im Backtest


def test_backtests_run_with_all_six_agents_and_record_kibo_and_yuzu():
    from trading.audit.event_log import InMemoryEventLog

    logs = []

    def factory(clock):
        logs.append(InMemoryEventLog(clock))
        return logs[-1]

    r = BacktestEngine(synthetic(days=20, seed=4), build_strategy("breakout"), config(), log_factory=factory).run()
    assert r.placeholders == () and r.setups_proposed > 0
    opinions = [e.payload["agent"] for e in logs[0].of_type(EventType.AGENT_OPINION)]
    assert {"RIN", "KIBO", "YUZU"} <= set(opinions)
    again = BacktestEngine(synthetic(days=20, seed=4), build_strategy("breakout"), config()).run()
    assert again.result_hash == r.result_hash  # auch mit KIBO/YUZU bitgleich reproduzierbar
