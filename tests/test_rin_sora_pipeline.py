"""RIN und SORA im Zusammenspiel mit Pipeline, Executor und Protokoll (synthetische Daten)."""

import dataclasses
import inspect
from datetime import timedelta

import pytest
from helpers import D, Env, T0, make_candle, make_setup
from stubs import StubKibo, StubMomo, StubYuzu

from trading.agents import (
    AkiAgent,
    MarketContext,
    RinConfig,
    RuleBasedRin,
    RuleBasedSora,
    SizingPolicy,
)
from trading.audit.events import EventType
from trading.domain.models import MarketType, Side
from trading.domain.reasons import RejectReason as R
from trading.domain.universe import TradingUniverse
from trading.marketdata.synthetic import SyntheticMarket
from trading.pipeline import TradingPipeline
from trading.safety.limits import DEFAULT_LIMITS

UNIVERSE = TradingUniverse.of(spot=["BTC-USD"], futures=["BTC-USD", "ETH-USD"])
FUT = {"market_type": MarketType.FUTURES, "funding": D("0.0001")}


def build(env, setups, rin_config=None):
    rin = RuleBasedRin(UNIVERSE, env.clock, rin_config, env.risk.costs)
    sora = RuleBasedSora(SizingPolicy.from_limits(DEFAULT_LIMITS), env.risk.costs)
    aki = AkiAgent(env.log)
    return TradingPipeline(StubMomo(setups), rin, StubKibo(), StubYuzu(), sora, aki, env.executor), aki


def ctx(env, **kw):
    return [MarketContext(env.snap(**kw))]


def test_concrete_agents_do_not_receive_limits_riskmanager_or_executor():
    for cls in (RuleBasedRin, RuleBasedSora):
        params = set(inspect.signature(cls.__init__).parameters)
        assert not params & {"limits", "risk_manager", "executor", "safety_limits"}


def test_full_flow_with_real_rin_and_sora_fills_and_logs():
    env = Env()
    pipe, aki = build(env, [make_setup("a", tp=D("102.5"))])
    out = pipe.run_cycle(ctx(env))
    assert out[0].executed
    pos = env.executor.open_positions[0]
    assert pos.risk_at_open <= D(40)  # SORA-Ziel 0,4 % von 10 000
    sizing = env.log.of_type(EventType.SIZING_PROPOSED)[0].payload["sizing"]
    assert sizing["approve"] is True and D(sizing["planned_loss"]) == pos.risk_at_open
    # Das Ergebnis des Trades in AKI:
    trades = env.executor.on_candle(make_candle(T0, D(100), D(103), D(100), D(102.6)))
    aki.register_closed_trades(trades)
    assert aki.performance(env.equity0).wins == 1


@pytest.mark.parametrize(
    "setup_kw, snap_kw, stage, reason",
    [
        ({"side": Side.SHORT, "stop": D(101), "tp": D(97)}, {}, "RIN", R.SPOT_SHORT_NOT_ALLOWED),
        ({"symbol": "DOGE-USD"}, {"symbol": "DOGE-USD"}, "RIN", R.INVALID_SYMBOL),
        ({"stop": D(101)}, {}, "RIN", R.STOP_WRONG_SIDE),
        ({}, {"ts": T0 - timedelta(seconds=120)}, "RIN", R.STALE_DATA),
        ({"tp": D("100.5")}, {}, "RIN", R.REWARD_RISK_TOO_LOW),
    ],
)
def test_rin_blocks_arrive_with_specific_machine_readable_reasons(setup_kw, snap_kw, stage, reason):
    env = Env()
    pipe, _ = build(env, [make_setup("x", **setup_kw)])
    out = pipe.run_cycle(ctx(env, **snap_kw))
    assert not out[0].executed and out[0].stage == stage and reason in out[0].reasons
    assert R.AGENT_REJECTED not in out[0].reasons  # konkreter Grund statt Sammelgrund
    assert env.executor.open_positions == ()
    blocked = env.log.of_type(EventType.SETUP_BLOCKED)[0].payload
    assert reason.value in blocked["reasons"] and blocked["stage"] == stage and blocked["details"]


def test_sora_blocks_when_the_two_percent_budget_is_used_up():
    env = Env()
    # Hebel-Positionen bis das Budget aufgebraucht ist (verschiedene Maerkte)
    setups = [make_setup("f%d" % i, symbol="BTC-USD" if i == 0 else "ETH-USD",
                         market_type=MarketType.FUTURES) for i in range(2)]
    pipe, _ = build(env, setups)
    # Vorhandenes Risiko simulieren: eine fremde Position, die fast alles belegt
    env.order("pre", qty=D(40), lev=D(2), symbol="XRP-USD", market_type=MarketType.FUTURES,
              snap_kw=dict(FUT, symbol="XRP-USD"))
    env.order("pre2", qty=D(40), lev=D(2), symbol="SOL-USD", market_type=MarketType.FUTURES,
              snap_kw=dict(FUT, symbol="SOL-USD"))
    env.order("pre3", qty=D(40), lev=D(2), symbol="ADA-USD", market_type=MarketType.FUTURES,
              snap_kw=dict(FUT, symbol="ADA-USD"))
    env.order("pre4", qty=D(40), lev=D(2), symbol="LTC-USD", market_type=MarketType.FUTURES,
              snap_kw=dict(FUT, symbol="LTC-USD"))
    ctxs = [MarketContext(env.snap(symbol=s, **FUT)) for s in ("BTC-USD", "ETH-USD")]
    out = pipe.run_cycle(ctxs)
    # 4 Positionen = ca. 1,8 % belegt: der Rest reicht nur fuer eine kleine Position, danach ist Schluss
    assert out[0].executed
    assert not out[1].executed and out[1].stage == "SORA"
    assert R.PORTFOLIO_RISK_TOO_HIGH in out[1].reasons or R.TOO_MANY_POSITIONS in out[1].reasons
    st = env.executor.portfolio_state()
    assert st.open_risk / st.equity <= D("0.02")


def test_rin_does_not_replace_the_risk_manager_executor_still_checks_independently():
    env = Env()
    pipe, _ = build(env, [make_setup("a")])
    pipe._sora = _OversizedSora(pipe._sora)  # SORA-Fehler simulieren
    out = pipe.run_cycle(ctx(env))
    assert out[0].stage == "EXECUTOR" and R.TRADE_RISK_TOO_HIGH in out[0].reasons


class _OversizedSora(RuleBasedSora):
    def __init__(self, inner):
        self.__dict__.update(inner.__dict__)

    def size(self, setup, context, portfolio):
        import dataclasses
        good = super().size(setup, context, portfolio)
        return dataclasses.replace(good, quantity=good.quantity * 5)


def test_synthetic_market_run_through_rin_and_sora_never_breaks_limits():
    """Viele synthetische Kerzen, laufende Setups: 2-%-Grenze und Buchhaltung bleiben stabil."""
    env = Env(equity=D(50000))
    market = SyntheticMarket(start_time=T0, seed=11, volatility=0.004, start_price=D(100),
                             market_type=MarketType.FUTURES, funding_rate=D("0.0001"))
    rin = RuleBasedRin(UNIVERSE, env.clock, RinConfig(), env.risk.costs)
    sora = RuleBasedSora(SizingPolicy.from_limits(DEFAULT_LIMITS), env.risk.costs)
    aki = AkiAgent(env.log)
    max_ratio_after_fill = D(0)
    executed = 0
    blocked_reasons = set()
    for i in range(400):
        candle = market.next_candle()
        env.clock.set(candle.close_time)
        env.executor.on_candle(candle, D("0.0001"))
        snap = market.snapshot_for(candle)
        if i % 5 == 0:
            p = snap.ask
            setup = make_setup("syn-%d" % i, symbol="BTC-USD", market_type=MarketType.FUTURES,
                               entry=p, stop=p * D("0.985"), tp=p * D("1.03"))
            setup = dataclasses.replace(setup, created_at=env.clock.now())
            pipe = TradingPipeline(StubMomo([setup]), rin, StubKibo(), StubYuzu(), sora, aki, env.executor)
            out = pipe.run_cycle([MarketContext(snap)])[0]
            if out.executed:
                executed += 1
                st = env.executor.portfolio_state()
                max_ratio_after_fill = max(max_ratio_after_fill, st.open_risk / st.equity)
            else:
                blocked_reasons.update(out.reasons)
    assert executed >= 8, (executed, blocked_reasons)  # nicht trivial: es wurde wirklich gehandelt
    assert len(env.executor.closed_trades) >= 5  # und es gab abgeschlossene Trades
    assert max_ratio_after_fill <= D("0.02")  # Grenze gilt im Moment jeder Freigabe
    open_ = env.executor.open_positions
    assert env.executor.balance == (
        env.equity0 + sum(t.net_pnl for t in env.executor.closed_trades)
        - sum(p.entry_fee + p.funding_paid for p in open_)
    )
    assert env.log.verify().ok
