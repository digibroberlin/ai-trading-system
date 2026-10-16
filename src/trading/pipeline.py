"""Verbindet die Agenten zu einem Entscheidungsablauf:

    MOMO (Idee) -> RIN (Validierung) -> KIBO (Technik) -> YUZU (Umfeld)
                -> SORA (Risiko/Groesse) -> Executor (mit unabhaengigem Risikomanager)

Jede Stufe darf blockieren. AKI dokumentiert jeden Schritt. Faellt ein Agent aus (Exception),
gilt das als Blockade ("fail closed"), nie als Freigabe.

Vorschlagen und Ausfuehren sind getrennte Schritte (`propose` / `process_setup`): Ein Setup entsteht
zu einem Entscheidungszeitpunkt aus den bis dahin bekannten Daten und wird mit den Marktdaten des
AUSFUEHRUNGSZEITPUNKTS geprueft und dimensioniert. `run_cycle` erledigt beides in einem Zug.

Ausstiegssignale (`propose_exits` / `process_exit`) durchlaufen keine Chance-Risiko-Pruefung: Eine
Schliessung senkt das Risiko. Die Marktdaten prueft der Executor trotzdem; ohne gueltigen Kurs wird
nicht geschlossen, und die Position bleibt mit ihrem Stop-Loss bestehen.

Die Freigaberegel ist absichtlich streng und einfach: RIN, KIBO und YUZU muessen alle
zustimmen. Gewichtung/Scoring kommt spaeter, wenn Backtests Belege liefern.

Gleichzeitigkeit: Dimensionierung (SORA) und Ausfuehrung eines Setups sind ein unteilbarer
Schritt: SORA rechnet unter der Sperre des Executors (`Executor.submit_sized`) - bei PostgreSQL also
innerhalb der Transaktion mit gesperrter Konto-Zeile, auch gegenueber anderen Prozessen. So bekommen
zwei gleichzeitige Anfragen nie dasselbe Restrisiko-Budget, und ein geschrumpftes Budget fuehrt zu
einer kleineren Position statt zu einer Ablehnung (Audit M8).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple, TypeVar

from trading.agents.aki import AkiAgent
from trading.agents.contracts import AgentOpinion, ExitSignal, MarketContext, SizingProposal
from trading.agents.kibo import KiboAgent
from trading.agents.momo import MomoAgent
from trading.agents.rin import RinAgent
from trading.agents.sora import SoraAgent
from trading.agents.yuzu import YuzuAgent
from trading.domain.models import (
    CloseResult,
    ExecutionResult,
    ExitReason,
    OrderIntent,
    OrderStatus,
    PortfolioState,
    Position,
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


@dataclass(frozen=True)
class ExitOutcome:
    signal: ExitSignal
    closed: bool
    reasons: Tuple[RejectReason, ...] = ()
    result: Optional[CloseResult] = None


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

    # ------------------------------------------------------------------ Einstiege

    def propose(self, contexts: Sequence[MarketContext]) -> List[TradeSetup]:
        """MOMO schlaegt Setups vor; jeder Vorschlag wird dokumentiert. Ein Fehler ergibt keine Vorschlaege."""
        try:
            setups = list(self._momo.scan(contexts))
        except Exception as exc:  # noqa: BLE001 - jeder Agentenfehler ist eine Blockade
            self._aki.record_system_error("MOMO", repr(exc))
            return []
        for setup in setups:
            self._aki.record_setup(setup)
        return setups

    def process_setup(self, setup: TradeSetup, ctx: Optional[MarketContext]) -> CycleOutcome:
        """Prueft, dimensioniert und fuehrt EIN Setup mit den Marktdaten `ctx` aus (oder blockiert es)."""
        if ctx is None:
            return self.reject_setup(setup, "PIPELINE", (RejectReason.NO_MARKET_DATA,))
        blocked = self._opinions(setup, ctx)
        if blocked is not None:
            return blocked
        with self._lock:
            return self._size_and_execute(setup, ctx)

    def run_cycle(self, contexts: Sequence[MarketContext]) -> List[CycleOutcome]:
        by_instrument = {c.snapshot.instrument: c for c in contexts}
        return [
            self.process_setup(setup, by_instrument.get(setup.instrument))
            for setup in self.propose(contexts)
        ]

    def reject_setup(
        self, setup: TradeSetup, stage: str, reasons: Tuple[RejectReason, ...], details: str = ""
    ) -> CycleOutcome:
        self._aki.record_blocked(setup, stage, reasons, details)
        return CycleOutcome(setup, False, stage, reasons)

    # ------------------------------------------------------------------ Ausstiege

    def propose_exits(
        self, contexts: Sequence[MarketContext], positions: Sequence[Position]
    ) -> List[ExitSignal]:
        try:
            signals = list(self._momo.exit_signals(contexts, positions))
        except Exception as exc:  # noqa: BLE001
            self._aki.record_system_error("MOMO", repr(exc))
            return []
        for signal in signals:
            self._aki.record_exit_signal(signal)
        return signals

    def process_exit(self, signal: ExitSignal, ctx: Optional[MarketContext]) -> ExitOutcome:
        if ctx is None:
            return self._exit_blocked(signal, (RejectReason.NO_MARKET_DATA,))
        snapshot = ctx.snapshot
        with self._lock:
            result = self._guarded(
                lambda: self._executor.close_position(
                    signal.instrument, snapshot, ExitReason.SIGNAL, signal.signal_id
                )
            )
        if result is None:
            return self._exit_blocked(signal, (RejectReason.EXECUTION_ERROR,))
        return ExitOutcome(signal, result.closed, result.reasons, result)  # der Executor protokolliert selbst

    def _exit_blocked(self, signal: ExitSignal, reasons: Tuple[RejectReason, ...]) -> ExitOutcome:
        self._aki.record_exit_blocked(signal, "PIPELINE", reasons)
        return ExitOutcome(signal, False, reasons)

    # ------------------------------------------------------------------ intern

    def _opinions(self, setup: TradeSetup, ctx: MarketContext) -> Optional[CycleOutcome]:
        """RIN, KIBO, YUZU nacheinander. Gibt eine Blockade zurueck oder None (alle stimmen zu)."""
        checks: Tuple[Tuple[str, Callable[[], AgentOpinion]], ...] = (
            ("RIN", lambda: self._rin.validate(setup, ctx)),
            ("KIBO", lambda: self._kibo.evaluate(setup, ctx)),
            ("YUZU", lambda: self._yuzu.evaluate(setup, ctx)),
        )
        for stage, check in checks:
            opinion = self._guarded(check)
            if opinion is None:
                return self.reject_setup(setup, stage, (RejectReason.AGENT_ERROR,))
            self._aki.record_opinion(opinion)
            if not opinion.approve:
                return self.reject_setup(
                    setup,
                    stage,
                    opinion.reject_reasons or (RejectReason.AGENT_REJECTED,),
                    "; ".join(opinion.reasons),
                )
        return None

    def _size_and_execute(self, setup: TradeSetup, ctx: MarketContext) -> CycleOutcome:
        """SORA rechnet INNERHALB der Sperre des Executors (bei PostgreSQL: der Konto-Transaktion) mit genau dem
        Zustand, gegen den der Risikomanager danach prueft (Audit M8). Ein von anderen Prozessen inzwischen
        verbrauchtes Restbudget fuehrt so zu einer kleineren Position statt zu einer Ablehnung."""
        sizing_box: List[SizingProposal] = []

        def sizer(portfolio: PortfolioState) -> Optional[OrderIntent]:
            try:
                sizing = self._sora.size(setup, ctx, portfolio)
            except Exception as exc:  # noqa: BLE001 - ein SORA-Fehler blockiert, er bricht nicht die Sperre auf
                self._aki.record_system_error("AGENT", repr(exc))
                return None
            sizing_box.append(sizing)
            self._aki.record_sizing(sizing)  # vor dem Handel protokolliert, wie bisher
            if not sizing.approve:
                return None
            return OrderIntent(
                client_order_id=setup.setup_id,
                setup=setup,
                quantity=sizing.quantity,
                leverage=sizing.leverage,
            )

        try:
            result = self._executor.submit_sized(setup.setup_id, ctx.snapshot, sizer)
        except Exception as exc:  # noqa: BLE001 - z. B. Datenbankfehler: zurueckgerollt, nichts wurde gehandelt
            self._aki.record_system_error("AGENT", repr(exc))
            return self.reject_setup(setup, "EXECUTOR", (RejectReason.EXECUTION_ERROR,))
        if result is None:
            if not sizing_box:  # SORA ist abgestuerzt
                return self.reject_setup(setup, "SORA", (RejectReason.AGENT_ERROR,))
            sizing = sizing_box[-1]
            return self.reject_setup(
                setup,
                "SORA",
                sizing.reject_reasons or (RejectReason.AGENT_REJECTED,),
                "; ".join(sizing.reasons),
            )
        if result.replayed:  # schon frueher bearbeitet: in DIESEM Durchlauf wurde nichts gehandelt
            return CycleOutcome(setup, False, "EXECUTOR", (RejectReason.DUPLICATE_ORDER_ID,), result=result)
        if result.status is OrderStatus.FILLED:
            return CycleOutcome(setup, True, "FILLED", result=result)
        return CycleOutcome(setup, False, "EXECUTOR", result.reasons, result=result)

    def _guarded(self, fn: Callable[[], T]) -> Optional[T]:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            self._aki.record_system_error("AGENT", repr(exc))
            return None
