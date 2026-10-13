"""Schnittstellen der sechs Agenten und der Entscheidungsablauf (mit Test-Agenten)."""

import inspect
from datetime import timedelta
from decimal import Decimal

import pytest
from helpers import D, Env, T0, make_candle, make_setup
from stubs import BrokenMomo, StubKibo, StubMomo, StubRin, StubSora, StubYuzu

from trading.agents import (
    AgentName,
    AgentOpinion,
    AkiAgent,
    BaseAgent,
    KiboAgent,
    MarketContext,
    MomoAgent,
    RinAgent,
    SoraAgent,
    YuzuAgent,
)
from trading.ai.provider import AIRequest, NullProvider, ProviderRegistry
from trading.audit.events import EventType
from trading.domain.reasons import RejectReason as R
from trading.pipeline import TradingPipeline

# ------------------------------------------------------------------ Schnittstellen


@pytest.mark.parametrize("cls", [MomoAgent, RinAgent, SoraAgent, KiboAgent, YuzuAgent])
def test_agent_base_classes_are_abstract(cls):
    with pytest.raises(TypeError):
        cls()  # type: ignore[abstract]


def test_six_agents_with_unique_names_and_roles():
    classes = [MomoAgent, RinAgent, SoraAgent, KiboAgent, YuzuAgent, AkiAgent]
    assert {c.name for c in classes} == set(AgentName)
    assert len({c.name for c in classes}) == 6
    assert all(c.role for c in classes)
    assert all(issubclass(c, BaseAgent) for c in classes)


def test_agents_get_no_access_to_limits_risk_manager_or_executor():
    for cls in (MomoAgent, RinAgent, SoraAgent, KiboAgent, YuzuAgent, AkiAgent):
        params = set(inspect.signature(cls.__init__).parameters)
        assert not params & {"limits", "risk_manager", "executor", "safety_limits"}


def test_opinion_score_must_be_between_zero_and_one():
    with pytest.raises(ValueError):
        AgentOpinion(AgentName.RIN, "x", True, D("1.5"))


def test_null_ai_provider_and_registry():
    reg = ProviderRegistry()
    reg.register(NullProvider())
    assert reg.get("null").complete(AIRequest("t", "p")).text == ""
    with pytest.raises(ValueError):
        reg.register(NullProvider())
    with pytest.raises(KeyError):
        reg.get("gibt-es-nicht")
    assert reg.names() == ["null"]


# ------------------------------------------------------------------ Pipeline


def build(env, setups=None, rin=None, kibo=None, yuzu=None, sora=None, momo=None):
    aki = AkiAgent(env.log)
    pipe = TradingPipeline(
        momo or StubMomo(setups if setups is not None else [make_setup("s1")]),
        rin or StubRin(),
        kibo or StubKibo(),
        yuzu or StubYuzu(),
        sora or StubSora(env.risk.costs),
        aki,
        env.executor,
    )
    return pipe, aki


def ctx(env, **kw):
    return [MarketContext(env.snap(**kw))]


def types(env):
    return [e.event_type for e in env.log.events()]


def test_happy_path_executes_and_documents_every_step():
    env = Env()
    pipe, _ = build(env)
    out = pipe.run_cycle(ctx(env))
    assert len(out) == 1 and out[0].executed and out[0].stage == "FILLED"
    assert types(env) == [
        "SETUP_PROPOSED", "AGENT_OPINION", "AGENT_OPINION", "AGENT_OPINION",
        "SIZING_PROPOSED", "ORDER_FILLED",
    ]
    assert len(env.executor.open_positions) == 1
    assert env.log.verify().ok


@pytest.mark.parametrize(
    "kwargs, stage",
    [
        ({"rin": StubRin(approve=False)}, "RIN"),
        ({"kibo": StubKibo(approve=False)}, "KIBO"),
        ({"yuzu": StubYuzu(approve=False)}, "YUZU"),
    ],
)
def test_any_agent_veto_blocks_and_is_documented(kwargs, stage):
    env = Env()
    pipe, _ = build(env, **kwargs)
    out = pipe.run_cycle(ctx(env))
    assert not out[0].executed and out[0].stage == stage
    assert out[0].reasons == (R.AGENT_REJECTED,)
    assert env.executor.open_positions == ()
    blocked = env.log.of_type(EventType.SETUP_BLOCKED)[0].payload
    assert blocked["stage"] == stage and blocked["setup"]["setup_id"] == "s1"
    assert "ORDER_FILLED" not in types(env)


def test_sora_declining_blocks():
    env = Env()
    pipe, _ = build(env, sora=StubSora(env.risk.costs, approve=False))
    out = pipe.run_cycle(ctx(env))
    assert out[0].stage == "SORA" and not out[0].executed


def test_crashing_agent_blocks_instead_of_approving():
    env = Env()
    pipe, _ = build(env, kibo=StubKibo(explode=True))
    out = pipe.run_cycle(ctx(env))
    assert not out[0].executed and out[0].reasons == (R.AGENT_ERROR,)
    assert len(env.log.of_type(EventType.SYSTEM_ERROR)) == 1
    assert env.executor.open_positions == ()


def test_crashing_momo_produces_no_trades_and_is_logged():
    env = Env()
    pipe, _ = build(env, momo=BrokenMomo())
    assert pipe.run_cycle(ctx(env)) == []
    assert len(env.log.of_type(EventType.SYSTEM_ERROR)) == 1


def test_oversized_sora_proposal_is_stopped_by_independent_risk_manager():
    env = Env()
    pipe, _ = build(env, sora=StubSora(env.risk.costs, override_qty=D(100)))
    out = pipe.run_cycle(ctx(env))
    assert out[0].stage == "EXECUTOR" and not out[0].executed
    assert R.TRADE_RISK_TOO_HIGH in out[0].reasons
    assert env.executor.open_positions == ()
    assert env.log.of_type(EventType.ORDER_REJECTED)


def test_stale_market_data_blocks_at_executor():
    env = Env()
    pipe, _ = build(env)
    out = pipe.run_cycle(ctx(env, ts=T0 - timedelta(seconds=120)))
    assert out[0].stage == "EXECUTOR" and R.STALE_DATA in out[0].reasons


def test_setup_without_market_data_is_blocked():
    env = Env()
    pipe, _ = build(env, setups=[make_setup("s1", symbol="ETH-USD")])
    out = pipe.run_cycle(ctx(env))  # nur BTC-USD verfuegbar
    assert out[0].reasons == (R.NO_MARKET_DATA,)


def test_same_setup_id_proposed_twice_is_only_executed_once():
    env = Env()
    pipe, _ = build(env, setups=[make_setup("same"), make_setup("same")])
    out = pipe.run_cycle(ctx(env))
    assert [o.executed for o in out] == [True, False]
    assert R.DUPLICATE_ORDER_ID in out[1].reasons
    assert len(env.executor.open_positions) == 1


def test_every_blocked_and_every_executed_setup_leaves_a_trace():
    env = Env()
    setups = [make_setup("ok", symbol="BTC-USD"), make_setup("nodata", symbol="XRP-USD")]
    pipe, _ = build(env, setups=setups)
    pipe.run_cycle(ctx(env))
    proposed = {e.payload["setup"]["setup_id"] for e in env.log.of_type(EventType.SETUP_PROPOSED)}
    assert proposed == {"ok", "nodata"}
    assert len(env.log.of_type(EventType.SETUP_BLOCKED)) == 1
    assert len(env.log.of_type(EventType.ORDER_FILLED)) == 1


def test_aki_statistics_after_a_full_round_trip():
    env = Env()
    pipe, aki = build(env)
    pipe.run_cycle(ctx(env))
    trades = env.executor.on_candle(make_candle(T0, D(100), D(103), D(100), D(102)))
    aki.register_closed_trades(trades)
    stats = aki.performance(env.equity0)
    assert stats.trade_count == 1 and stats.wins == 1 and stats.net_pnl > 0
