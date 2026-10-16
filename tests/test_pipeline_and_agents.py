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
from trading.domain.models import OrderStatus
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
    # Reihenfolge nach Vorgabe des Eigners (AP-12): MOMO -> RIN -> SORA (Vorpruefung) -> KIBO -> YUZU
    # -> SORA (endgueltig, unter der Sperre) -> Executor; AKI protokolliert jeden Schritt.
    assert types(env) == [
        "SETUP_PROPOSED", "AGENT_OPINION", "SIZING_PRECHECK", "AGENT_OPINION", "AGENT_OPINION",
        "SIZING_PROPOSED", "ORDER_FILLED",
    ]
    opinions = [e.payload["agent"] for e in env.log.of_type(EventType.AGENT_OPINION)]
    assert opinions == ["RIN", "KIBO", "YUZU"]
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


def test_crashing_sora_blocks_with_agent_error_and_nothing_is_traded():
    """SORA rechnet unter der Sperre des Executors (Audit M8): Ein Absturz dort blockiert, haelt keine Sperre fest und
    hinterlaesst keine Order - die Order-ID bleibt frei."""
    env = Env()

    class ExplodingSora(StubSora):
        def size(self, setup, context, portfolio):
            raise RuntimeError("SORA abgestuerzt")

    pipe, _ = build(env, sora=ExplodingSora(env.risk.costs))
    out = pipe.run_cycle(ctx(env))
    assert not out[0].executed and out[0].stage == "SORA" and out[0].reasons == (R.AGENT_ERROR,)
    assert len(env.log.of_type(EventType.SYSTEM_ERROR)) == 1 and not env.log.of_type(EventType.ORDER_REJECTED)
    assert env.executor.open_positions == ()
    assert env.order(out[0].setup.setup_id).status is OrderStatus.FILLED  # Sperre frei, ID unverbraucht


class CallLog:
    """Haelt fest, welcher Agent in welcher Reihenfolge gefragt wurde."""

    def __init__(self):
        self.calls = []


def recording(env, log, sora_answers=None, rin_approve=True):
    """Test-Agenten, die ihren Aufruf in `log.calls` eintragen. `sora_answers`: Liste je SORA-Aufruf
    (True = zustimmen, False = ablehnen, "boom" = abstuerzen); danach immer zustimmen."""
    answers = list(sora_answers or [])

    class Rin(StubRin):
        def validate(self, setup, context):
            log.calls.append("RIN")
            return super().validate(setup, context)

    class Kibo(StubKibo):
        def evaluate(self, setup, context):
            log.calls.append("KIBO")
            return super().evaluate(setup, context)

    class Yuzu(StubYuzu):
        def evaluate(self, setup, context):
            log.calls.append("YUZU")
            return super().evaluate(setup, context)

    class Sora(StubSora):
        def size(self, setup, context, portfolio):
            log.calls.append("SORA")
            answer = answers.pop(0) if answers else True
            if answer == "boom":
                raise RuntimeError("SORA abgestuerzt")
            self.approve = answer
            return super().size(setup, context, portfolio)

    return dict(rin=Rin(approve=rin_approve), kibo=Kibo(), yuzu=Yuzu(), sora=Sora(env.risk.costs))


def test_agents_are_asked_in_the_owners_order():
    env, log = Env(), CallLog()
    pipe, _ = build(env, **recording(env, log))
    assert pipe.run_cycle(ctx(env))[0].executed
    assert log.calls == ["RIN", "SORA", "KIBO", "YUZU", "SORA"]


def test_rin_veto_stops_before_sora_kibo_and_yuzu():
    env, log = Env(), CallLog()
    pipe, _ = build(env, **recording(env, log, rin_approve=False))
    out = pipe.run_cycle(ctx(env))
    assert out[0].stage == "RIN" and log.calls == ["RIN"]
    assert not env.log.of_type(EventType.SIZING_PRECHECK)


def test_sora_precheck_rejection_stops_before_kibo_and_yuzu_and_is_documented():
    """Ein Setup, das SORA schon verwirft, verbraucht keine (spaeter teure) Bewertung durch KIBO/YUZU."""
    env, log = Env(), CallLog()
    pipe, _ = build(env, **recording(env, log, sora_answers=[False]))
    out = pipe.run_cycle(ctx(env))
    assert not out[0].executed and out[0].stage == "SORA" and out[0].reasons == (R.AGENT_REJECTED,)
    assert log.calls == ["RIN", "SORA"]
    assert len(env.log.of_type(EventType.SIZING_PRECHECK)) == 1 and not env.log.of_type(EventType.SIZING_PROPOSED)
    blocked = env.log.of_type(EventType.SETUP_BLOCKED)[0].payload
    assert blocked["stage"] == "SORA" and blocked["details"] == "sora-stub: kein Trade"
    assert env.executor.open_positions == ()


def test_sora_precheck_crash_blocks_before_kibo_and_yuzu():
    env, log = Env(), CallLog()
    pipe, _ = build(env, **recording(env, log, sora_answers=["boom"]))
    out = pipe.run_cycle(ctx(env))
    assert out[0].stage == "SORA" and out[0].reasons == (R.AGENT_ERROR,) and log.calls == ["RIN", "SORA"]
    assert len(env.log.of_type(EventType.SYSTEM_ERROR)) == 1 and env.executor.open_positions == ()


def test_unreadable_portfolio_before_the_precheck_blocks_fail_closed():
    env, log = Env(), CallLog()
    pipe, _ = build(env, **recording(env, log))

    def broken():
        raise RuntimeError("Zustand nicht lesbar")

    env.executor.portfolio_state = broken
    out = pipe.run_cycle(ctx(env))
    assert not out[0].executed and out[0].stage == "EXECUTOR" and out[0].reasons == (R.EXECUTION_ERROR,)
    assert log.calls == ["RIN"] and env.executor.open_positions == ()
    assert env.log.of_type(EventType.SYSTEM_ERROR)[0].payload["source"] == "EXECUTOR"


def test_precheck_approval_is_no_permission_final_sizing_under_the_lock_decides():
    """Stimmt die Vorpruefung zu, lehnt SORA aber unter der Sperre ab (z. B. Budget inzwischen verbraucht),
    wird nicht gehandelt - die Vorpruefung ist nur ein Filter."""
    env, log = Env(), CallLog()
    pipe, _ = build(env, **recording(env, log, sora_answers=[True, False]))
    out = pipe.run_cycle(ctx(env))
    assert not out[0].executed and out[0].stage == "SORA" and out[0].reasons == (R.AGENT_REJECTED,)
    assert log.calls == ["RIN", "SORA", "KIBO", "YUZU", "SORA"]
    assert env.executor.open_positions == () and not env.log.of_type(EventType.ORDER_FILLED)


def test_final_sizing_crash_under_the_lock_blocks_and_frees_the_order_id():
    env, log = Env(), CallLog()
    pipe, _ = build(env, **recording(env, log, sora_answers=[True, "boom"]))
    out = pipe.run_cycle(ctx(env))
    assert out[0].stage == "SORA" and out[0].reasons == (R.AGENT_ERROR,)
    assert env.executor.open_positions == () and not env.log.of_type(EventType.ORDER_REJECTED)
    assert env.order(out[0].setup.setup_id).status is OrderStatus.FILLED  # Sperre frei, ID unverbraucht


def test_order_size_comes_from_the_final_sizing_not_the_precheck():
    """Die Vorpruefung rechnet mit einem vorab gelesenen Zustand; die Order bekommt die Groesse von unter der Sperre."""
    env = Env()
    sizes = [D("0.5"), D("0.25")]

    class TwoSizes(StubSora):
        def size(self, setup, context, portfolio):
            self.override_qty = sizes.pop(0)
            return super().size(setup, context, portfolio)

    pipe, _ = build(env, sora=TwoSizes(env.risk.costs))
    out = pipe.run_cycle(ctx(env))
    assert out[0].executed and env.executor.open_positions[0].quantity == D("0.25")
    assert env.log.of_type(EventType.SIZING_PRECHECK)[0].payload["sizing"]["quantity"] == "0.5"


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


def test_identical_repeat_through_the_pipeline_is_reported_as_not_executed():
    env = Env()
    sora = StubSora(env.risk.costs, override_qty=D(10))  # feste Groesse -> zweimal exakt derselbe Auftrag
    pipe, _ = build(env, setups=[make_setup("same"), make_setup("same")], sora=sora)
    out = pipe.run_cycle(ctx(env))
    assert [o.executed for o in out] == [True, False]
    assert out[1].stage == "EXECUTOR" and out[1].reasons == (R.DUPLICATE_ORDER_ID,)
    assert out[1].result is not None and out[1].result.replayed
    assert out[1].result.position == out[0].result.position and len(env.executor.open_positions) == 1


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
    trades = env.feed(make_candle(T0, D(100), D(103), D(100), D(102)))
    aki.register_closed_trades(trades)
    stats = aki.performance(env.equity0)
    assert stats.trade_count == 1 and stats.wins == 1 and stats.net_pnl > 0


# ------------------------------------------------------------------ Stufe 1: getrennte Schritte, Ausstiegssignale, neutrale Agenten


def exit_signal(symbol="BTC-USD", sid="x1"):
    from helpers import inst
    from trading.agents import ExitSignal

    return ExitSignal(sid, inst(symbol), "test", T0, "Trend gedreht")


def test_proposing_and_executing_are_separate_steps_with_separate_market_data():
    """Ein Setup entsteht aus den Daten des Entscheidungszeitpunkts und wird mit den Daten des
    Ausfuehrungszeitpunkts geprueft und dimensioniert."""
    from trading.agents import RuleBasedSora, SizingPolicy
    from trading.safety.limits import DEFAULT_LIMITS

    env = Env()
    pipe, _ = build(env, sora=RuleBasedSora(SizingPolicy.from_limits(DEFAULT_LIMITS), env.risk.costs))
    setups = pipe.propose(ctx(env))
    assert [s.setup_id for s in setups] == ["s1"] and types(env) == ["SETUP_PROPOSED"]
    assert env.executor.open_positions == ()  # ein Vorschlag ist noch kein Handel
    out = pipe.process_setup(setups[0], MarketContext(env.snap(mid=D("100.5"))))  # Kurs bei Ausfuehrung: 100,5
    assert out.executed and out.result.position.entry_price == D("100.5")
    # dimensioniert wurde mit dem Ausfuehrungskurs (Stop-Abstand 1,5 statt 1): Risiko bleibt bei 0,4 %
    assert D("39.9") < out.result.position.risk_at_open <= D(40)


def test_size_computed_from_the_proposal_price_is_stopped_when_the_market_has_moved():
    env = Env()
    pipe, _ = build(env)  # Test-SORA rechnet (falsch) mit dem Wunsch-Entry 100
    (setup,) = pipe.propose(ctx(env))
    out = pipe.process_setup(setup, MarketContext(env.snap(mid=D("100.5"))))
    assert out.stage == "EXECUTOR" and R.TRADE_RISK_TOO_HIGH in out.reasons  # der Risikomanager faengt es ab


def test_setup_without_execution_data_is_blocked_and_documented():
    env = Env()
    pipe, _ = build(env)
    (setup,) = pipe.propose(ctx(env))
    out = pipe.process_setup(setup, None)
    assert not out.executed and out.reasons == (R.NO_MARKET_DATA,)
    assert env.log.of_type(EventType.SETUP_BLOCKED)[0].payload["stage"] == "PIPELINE"


def test_exit_signal_closes_the_position_and_is_documented():
    env = Env()
    pipe, _ = build(env, momo=StubMomo([make_setup("s1")], exits=[exit_signal()]))
    pipe.run_cycle(ctx(env))
    env.clock.advance(60)
    signals = pipe.propose_exits(ctx(env), env.executor.open_positions)
    assert [s.signal_id for s in signals] == ["x1"]
    out = pipe.process_exit(signals[0], MarketContext(env.snap(mid=D(101))))
    assert out.closed and out.result.trade.exit_reason.value == "signal"
    assert env.executor.open_positions == ()
    assert types(env)[-2:] == ["EXIT_SIGNAL", "POSITION_CLOSED"]
    assert env.log.of_type(EventType.EXIT_SIGNAL)[0].payload["signal"]["rationale"] == "Trend gedreht"


def test_exit_signal_that_cannot_be_executed_keeps_the_position_and_leaves_a_trace():
    env = Env()
    pipe, _ = build(env, momo=StubMomo([make_setup("s1")], exits=[exit_signal(), exit_signal("ETH-USD", "x2")]))
    pipe.run_cycle(ctx(env))
    env.clock.advance(60)
    s1, s2 = pipe.propose_exits(ctx(env), env.executor.open_positions)
    no_data = pipe.process_exit(s1, None)
    assert not no_data.closed and no_data.reasons == (R.NO_MARKET_DATA,)
    stale = pipe.process_exit(s1, MarketContext(env.snap(ts=env.clock.now() - timedelta(seconds=20))))
    assert not stale.closed and stale.reasons == (R.STALE_DATA,)
    nothing = pipe.process_exit(s2, MarketContext(env.snap(symbol="ETH-USD")))
    assert nothing.reasons == (R.NO_POSITION,)
    assert len(env.executor.open_positions) == 1
    rejected = [e.payload for e in env.log.of_type(EventType.CLOSE_REJECTED)]
    assert [tuple(p["reasons"]) for p in rejected] == [("NO_MARKET_DATA",), ("STALE_DATA",), ("NO_POSITION",)]
    assert rejected[0]["stage"] == "PIPELINE"


def test_crashing_momo_produces_no_exit_signals_and_is_logged():
    env = Env()
    pipe, _ = build(env, momo=BrokenMomo())
    assert pipe.propose_exits(ctx(env), ()) == []
    assert len(env.log.of_type(EventType.SYSTEM_ERROR)) == 1


def test_default_momo_has_no_exit_signals():
    assert MomoAgent.exit_signals(StubMomo([]), [], []) == []


def test_neutral_placeholder_agents_approve_without_a_verdict_but_never_bypass_rin_or_the_risk_manager():
    from trading.agents import NeutralKibo, NeutralYuzu

    env = Env()
    setup = make_setup("s1")
    for agent, name in ((NeutralKibo(), AgentName.KIBO), (NeutralYuzu(), AgentName.YUZU)):
        op = agent.evaluate(setup, MarketContext(env.snap()))
        assert op.approve and op.agent is name and op.score == D("0.5") and "Platzhalter" in op.reasons[0]
    # RIN blockiert trotzdem, und eine zu grosse Position scheitert weiter am Risikomanager:
    pipe, _ = build(env, rin=StubRin(approve=False), kibo=NeutralKibo(), yuzu=NeutralYuzu())
    assert pipe.run_cycle(ctx(env))[0].stage == "RIN"
    pipe, _ = build(env, setups=[make_setup("s2")], kibo=NeutralKibo(), yuzu=NeutralYuzu(),
                    sora=StubSora(env.risk.costs, override_qty=D(100)))
    assert pipe.run_cycle(ctx(env))[0].stage == "EXECUTOR"


def test_crashing_executor_blocks_the_trade_and_the_next_setup_is_still_processed():
    """Faellt die Ausfuehrung aus (z. B. Datenbank nicht erreichbar), wird das Setup blockiert und protokolliert;
    der Zyklus laeuft weiter - ein Fehler reisst nicht das ganze System ab."""
    env = Env()
    pipe, _ = build(env, setups=[make_setup("s1"), make_setup("s2")])
    real, calls = env.executor.submit_sized, []

    def flaky(client_order_id, snapshot, sizer):  # die Pipeline fuehrt ueber submit_sized aus (Audit M8)
        calls.append(client_order_id)
        if len(calls) == 1:
            raise ConnectionError("Verbindung zur Datenbank verloren")
        return real(client_order_id, snapshot, sizer)

    env.executor.submit_sized = flaky
    first, second = pipe.run_cycle(ctx(env))
    assert not first.executed and first.stage == "EXECUTOR" and first.reasons == (R.EXECUTION_ERROR,)
    assert second.executed  # das naechste Setup wird ganz normal ausgefuehrt
    assert [p.setup_id for p in env.executor.open_positions] == ["s2"]
    assert calls == ["s1", "s2"]
    assert env.log.of_type(EventType.SYSTEM_ERROR)
    blocked = env.log.of_type(EventType.SETUP_BLOCKED)[0].payload
    assert blocked["setup"]["setup_id"] == "s1" and blocked["reasons"] == ["EXECUTION_ERROR"]
