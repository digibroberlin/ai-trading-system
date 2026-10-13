from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from trading.agents.base import AgentName, BaseAgent
from trading.agents.contracts import AgentOpinion, MarketContext
from trading.domain.models import TradeSetup


class KiboAgent(BaseAgent):
    """KIBO - bewertet technische Handelssignale (Trend, Momentum, Volumen, Struktur)."""

    name: ClassVar[AgentName] = AgentName.KIBO
    role: ClassVar[str] = "Bewertet technische Handelssignale"

    @abstractmethod
    def evaluate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        ...
