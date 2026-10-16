from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Iterable, List, Optional, Sequence, Tuple

from trading.agents.base import AgentName, BaseAgent
from trading.agents.contracts import AgentOpinion, ExitSignal, SizingProposal
from trading.ai.orchestrator import AICallRecord
from trading.ai.provider import AIProvider
from trading.audit.event_log import EventLog
from trading.audit.costs import (
    AICostSummary,
    CostLedger,
    NetProfitReport,
    OperatingCost,
    build_net_profit_report,
    summarize_ai_costs,
)
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
        self._ai_records = []  # type: List[AICallRecord]
        self._costs = CostLedger()

    # --- Dokumentation ---

    def record_setup(self, setup: TradeSetup) -> None:
        self._log.append(EventType.SETUP_PROPOSED, {"agent": AgentName.MOMO, "setup": setup})

    def record_opinion(self, opinion: AgentOpinion) -> None:
        self._log.append(EventType.AGENT_OPINION, opinion)

    def record_sizing_precheck(self, sizing: SizingProposal) -> None:
        """SORA-Vorpruefung (vor KIBO/YUZU). Nicht verbindlich: die Groesse fuer die Order entsteht erst unter der Sperre."""
        self._log.append(EventType.SIZING_PRECHECK, {"agent": AgentName.SORA, "sizing": sizing})

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

    # --- Kosten (D-016: Nettogewinn nach ALLEN Kosten) ---

    def record_ai_call(self, record: AICallRecord) -> None:
        """Messpunkt des KI-Orchestrators (als `recorder` uebergeben): jede Analyse-Stufe mit Tokens und Kosten.
        Zuerst gemerkt, dann protokolliert: Scheitert das Protokoll, fehlen die Kosten trotzdem nicht im Nettobericht
        (der Fehler geht weiter an den Orchestrator, der daraufhin das Budget sperrt)."""
        self._ai_records.append(record)
        self._log.append(EventType.AI_CALL, record)

    def record_regime_change(self, instrument: str, before: str, after: str) -> None:
        self._log.append(EventType.REGIME_CHANGE, {"instrument": instrument, "before": before, "after": after})

    def record_operating_cost(self, cost: OperatingCost) -> None:
        """Betriebskosten fuer einen Zeitraum (Server, Marktdaten, Sonstiges; nie gemessene KI-Aufrufe doppelt)."""
        self._log.append(EventType.OPERATING_COST, cost)
        self._costs.add(cost)

    @property
    def ai_records(self) -> Tuple[AICallRecord, ...]:
        return tuple(self._ai_records)

    def ai_cost_summary(self, start: Optional[datetime] = None, end: Optional[datetime] = None) -> AICostSummary:
        """KI-Aufrufe, Tokens und Kosten je Agent/Modell/Tag/Monat, je Analyse und je Signal."""
        return summarize_ai_costs(self._ai_records, start, end)

    def net_profit_report(self, start: datetime, end: datetime) -> NetProfitReport:
        """Handelsergebnis minus KI-, Server-, Marktdaten- und sonstige Kosten - das Ergebnis, an dem das System misst."""
        return build_net_profit_report(self._trades, self._ai_records, self._costs, start, end)

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

    def update_equity(self, time: datetime, state: PortfolioState) -> EquityPoint:
        """Wie `record_equity`, aber ein Punkt zum SELBEN Zeitpunkt wird ersetzt (Laufzeit: mehrere Kerzen schliessen
        gleichzeitig - der Punkt zeigt dann den Stand nach der letzten). Aeltere Zeitpunkte bleiben ein Fehler."""
        point = EquityPoint.from_state(time, state)
        if self._curve and point.time == self._curve[-1].time:
            self._curve[-1] = point
            return point
        return self.record_equity(time, state)

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
