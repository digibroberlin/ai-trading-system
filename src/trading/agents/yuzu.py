from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from trading.agents.base import AgentName, BaseAgent
from trading.agents.contracts import AgentOpinion, MarketContext
from trading.domain.models import TradeSetup


class YuzuAgent(BaseAgent):
    """YUZU - bewertet alternative Signale und Marktbedingungen
    (Funding, Open Interest, Liquiditaet, Volatilitaetsregime, Nachrichten/Stimmung)."""

    name: ClassVar[AgentName] = AgentName.YUZU
    role: ClassVar[str] = "Bewertet alternative Signale und Marktbedingungen"

    @abstractmethod
    def evaluate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        ...
