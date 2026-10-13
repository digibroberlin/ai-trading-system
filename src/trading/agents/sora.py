from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from trading.agents.base import AgentName, BaseAgent
from trading.agents.contracts import MarketContext, SizingProposal
from trading.domain.models import PortfolioState, TradeSetup


class SoraAgent(BaseAgent):
    """SORA - bewertet Risiko, Positionsgroesse und moegliche Verluste.

    Vertrag: schlaegt Menge und Hebel vor (dynamisch, je nach Marktlage). SORA darf nur
    INNERHALB der Sicherheitslimits vorschlagen und kennt diese nicht als aenderbare
    Werte. Der unabhaengige Risikomanager prueft jeden Vorschlag noch einmal und hat
    immer das letzte Wort.
    """

    name: ClassVar[AgentName] = AgentName.SORA
    role: ClassVar[str] = "Bewertet Risiko, Positionsgroesse und moegliche Verluste"

    @abstractmethod
    def size(
        self, setup: TradeSetup, context: MarketContext, portfolio: PortfolioState
    ) -> SizingProposal:
        ...
