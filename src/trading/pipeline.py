"""Verbindet die Agenten zu einem Entscheidungsablauf:

    MOMO (Idee) -> RIN (Validierung) -> KIBO (Technik) -> YUZU (Umfeld)
                -> SORA (Risiko/Groesse) -> Executor (mit unabhaengigem Risikomanager)

Jede Stufe darf blockieren. AKI dokumentiert jeden Schritt. Faellt ein Agent aus (Exception),
gilt das als Blockade ("fail closed"), nie als Freigabe.

Die Freigaberegel ist absichtlich streng und einfach: RIN, KIBO und YUZU muessen alle
zustimmen. Gewichtung/Scoring kommt spaeter, wenn Backtests Belege liefern.

Gleichzeitigkeit: Dimensionierung (SORA) und Ausfuehrung eines Setups sind ein unteilbarer
Schritt (Sperre). So bekommen zwei gleichzeitige Anfragen nie dasselbe Restrisiko-Budget.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple, TypeVar

from trading.agents.aki import AkiAgent
from trading.agents.contracts import AgentOpinion, MarketContext
from trading.agents.kibo import KiboAgent
from trading.agents.momo import MomoAgent
from trading.agents.rin import RinAgent
from trading.agents.sora import SoraAgent
from trading.agents.yuzu import YuzuAgent
from trading.domain.models import (
    ExecutionResult,
    MarketType,
    OrderIntent,
    OrderStatus,
    TradeSetup,
)
from trading.domain.reasons import RejectReason
from trading.execution.protocol import Executor

T = TypeVar("T")


@dataclass(frozen=True)
class CycleOutcome:
    setup: TradeSetup
    executed: bool
    stage: str  # Stufe, an der es endete (z. B. "RIN", "EXECUTOR", "FILLED")
    reasons: Tuple[RejectReason, ...] = ()
    result: Optional[ExecutionResult] = None


class TradingPipeline:
    def __init__(
        self,
        momo: MomoAgent,
        rin: RinAgent,
        kibo: KiboAgent,
        yuzu: YuzuAgent,
        sora: SoraAgent,
        aki: AkiAgent,
        executor: Executor,
    ) -> None:
        self._momo, self._rin, self._kibo = momo, rin, kibo
        self._yuzu, self._sora, self._aki = yuzu, sora, aki
        self._executor = executor
        self._lock = threading.RLock()

    def run_cycle(self, contexts: Sequence[MarketContext]) -> List[CycleOutcome]:
        outcomes = []  # type: List[CycleOutcome]
        try:
            setups = self._momo.scan(contexts)
        except Exception as exc:  # noqa: BLE001 - jeder Agentenfehler ist eine Blockade
            self._aki.record_system_error("MOMO", repr(exc))
            return outcomes

        by_market = {
            (c.snapshot.symbol, c.snapshot.market_type): c for c in contexts
        }  # type: Dict[Tuple[str, MarketType], MarketContext]

        for setup in setups:
            self._aki.record_setup(setup)
            ctx = by_market.get((setup.symbol, setup.market_type))
            if ctx is None:
                outcomes.append(self._block(setup, "PIPELINE", (RejectReason.NO_MARKET_DATA,)))
                continue

            blocked = self._opinions(setup, ctx)
            if blocked is not None:
                outcomes.append(blocked)
                continue

            with self._lock:
                outcomes.append(self._size_and_execute(setup, ctx))
        return outcomes

    # ------------------------------------------------------------------ intern

    def _opinions(self, setup: TradeSetup, ctx: MarketContext) -> Optional[CycleOutcome]:
        """RIN, KIBO, YUZU nacheinander. Gibt eine Blockade zurueck oder None (alle stimmen zu)."""
        checks = (
            ("RIN", lambda: self._rin.validate(setup, ctx)),
            ("KIBO", lambda: self._kibo.evaluate(setup, ctx)),
            ("YUZU", lambda: self._yuzu.evaluate(setup, ctx)),
        )  # type: Tuple[Tuple[str, Callable[[], AgentOpinion]], ...]
        for stage, check in checks:
            opinion = self._guarded(check)
            if opinion is None:
                return self._block(setup, stage, (RejectReason.AGENT_ERROR,))
            self._aki.record_opinion(opinion)
            if not opinion.approve:
                return self._block(
                    setup,
                    stage,
                    opinion.reject_reasons or (RejectReason.AGENT_REJECTED,),
                    "; ".join(opinion.reasons),
                )
        return None

    def _size_and_execute(self, setup: TradeSetup, ctx: MarketContext) -> CycleOutcome:
        sizing = self._guarded(
            lambda: self._sora.size(setup, ctx, self._executor.portfolio_state())
        )
        if sizing is None:
            return self._block(setup, "SORA", (RejectReason.AGENT_ERROR,))
        self._aki.record_sizing(sizing)
        if not sizing.approve:
            return self._block(
                setup,
                "SORA",
                sizing.reject_reasons or (RejectReason.AGENT_REJECTED,),
                "; ".join(sizing.reasons),
            )
        intent = OrderIntent(
            client_order_id=setup.setup_id,
            setup=setup,
            quantity=sizing.quantity,
            leverage=sizing.leverage,
        )
        result = self._guarded(lambda: self._executor.submit(intent, ctx.snapshot))
        if result is None:  # z. B. Datenbankfehler: die Transaktion wurde zurueckgerollt, nichts wurde gehandelt
            return self._block(setup, "EXECUTOR", (RejectReason.EXECUTION_ERROR,))
        if result.status is OrderStatus.FILLED:
            return CycleOutcome(setup, True, "FILLED", result=result)
        return CycleOutcome(setup, False, "EXECUTOR", result.reasons, result=result)

    def _guarded(self, fn: Callable[[], T]) -> Optional[T]:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            self._aki.record_system_error("AGENT", repr(exc))
            return None

    def _block(
        self,
        setup: TradeSetup,
        stage: str,
        reasons: Tuple[RejectReason, ...],
        details: str = "",
    ) -> CycleOutcome:
        self._aki.record_blocked(setup, stage, reasons, details)
        return CycleOutcome(setup, False, stage, reasons)
