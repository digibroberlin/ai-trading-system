from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Iterable, List, Optional, Sequence, Tuple

from trading.agents.base import AgentName, BaseAgent
from trading.agents.contracts import AgentOpinion, ExitSignal, SizingProposal
from trading.ai.provider import AIProvider
from trading.audit.event_log import EventLog
from trading.audit.events import EventType
from trading.audit.performance import EquityPoint, PerformanceReport, build_report
from trading.audit.stats import PerformanceStats, compute_performance
from trading.domain.models import ClosedTrade, PortfolioState, Position, TradeSetup
from trading.domain.reasons import RejectReason


class AkiAgent(BaseAgent):
    """AKI - dokumentiert saemtliche Entscheidungen, Trades und Ergebnisse und berechnet
    Performance-Statistiken.

    AKI ist der einzige Agent, der schon in dieser Phase voll implementiert ist, weil das
    unveraenderliche Protokoll eine Sicherheitsfunktion ist. AKI schreibt nur (append-only)
    und trifft selbst keine Handelsentscheidungen.
    """

    name: ClassVar[AgentName] = AgentName.AKI
    role: ClassVar[str] = "Dokumentiert Entscheidungen, Trades und Ergebnisse; berechnet Statistiken"

    def __init__(self, event_log: EventLog, ai: Optional[AIProvider] = None) -> None:
        super().__init__(ai)
        self._log = event_log
        self._trades = []  # type: List[ClosedTrade]
        self._curve = []  # type: List[EquityPoint]

    # --- Dokumentation ---

    def record_setup(self, setup: TradeSetup) -> None:
        self._log.append(EventType.SETUP_PROPOSED, {"agent": AgentName.MOMO, "setup": setup})

    def record_opinion(self, opinion: AgentOpinion) -> None:
        self._log.append(EventType.AGENT_OPINION, opinion)

    def record_sizing(self, sizing: SizingProposal) -> None:
        self._log.append(EventType.SIZING_PROPOSED, {"agent": AgentName.SORA, "sizing": sizing})

    def record_blocked(
        self, setup: TradeSetup, stage: str, reasons: Iterable[RejectReason], details: str = ""
    ) -> None:
        """Jedes blockierte Setup wird festgehalten - mit Stufe und Gruenden."""
        self._log.append(
            EventType.SETUP_BLOCKED,
            {"setup": setup, "stage": stage, "reasons": tuple(reasons), "details": details},
        )

    def record_exit_signal(self, signal: ExitSignal) -> None:
        self._log.append(EventType.EXIT_SIGNAL, {"agent": AgentName.MOMO, "signal": signal})

    def record_exit_blocked(
        self, signal: ExitSignal, stage: str, reasons: Iterable[RejectReason]
    ) -> None:
        """Ein Ausstiegssignal, das nicht ausgefuehrt werden konnte (die Position bleibt mit ihrem Stop)."""
        self._log.append(
            EventType.CLOSE_REJECTED,
            {"request_id": signal.signal_id, "instrument": signal.instrument.key, "stage": stage,
             "reasons": tuple(reasons)},
        )

    def record_system_error(self, source: str, message: str) -> None:
        """Ausgefallene Agenten werden dokumentiert; das Setup gilt dann als blockiert."""
        self._log.append(EventType.SYSTEM_ERROR, {"source": source, "message": message})

    # --- Auswertung ---

    def register_closed_trades(self, trades: Sequence[ClosedTrade]) -> None:
        """Merkt Ergebnisse fuer die Statistik vor. (Das Schliessen selbst protokolliert
        bereits der Executor.)"""
        self._trades.extend(trades)

    def record_equity(self, time: datetime, state: PortfolioState) -> EquityPoint:
        """Haengt einen Punkt an die Kapitalkurve an (alle offenen Positionen zum aktuellen Kurs bewertet)."""
        point = EquityPoint.from_state(time, state)
        if self._curve and point.time <= self._curve[-1].time:
            raise ValueError("Kapitalkurve: Zeitpunkte muessen streng aufsteigend sein.")
        self._curve.append(point)
        return point

    @property
    def trades(self) -> Tuple[ClosedTrade, ...]:
        return tuple(self._trades)

    @property
    def equity_curve(self) -> Tuple[EquityPoint, ...]:
        return tuple(self._curve)

    def performance(self, initial_equity: Decimal) -> PerformanceStats:
        """Kennzahlen nur aus abgeschlossenen Trades."""
        return compute_performance(self._trades, initial_equity)

    def report(self, initial_equity: Decimal, open_positions: Sequence[Position]) -> PerformanceReport:
        """Gesamtbericht aus Trades UND Kapitalkurve (realisiert/unrealisiert getrennt, alles nach Kosten)."""
        return build_report(initial_equity, self._trades, self._curve, open_positions)
