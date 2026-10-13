from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from trading.agents.base import AgentName, BaseAgent
from trading.agents.contracts import AgentOpinion, MarketContext
from trading.domain.models import TradeSetup


class RinAgent(BaseAgent):
    """RIN - validiert Kursdaten, Entry, Stop-Loss und Take-Profit.

    Vertrag: hat Vetorecht. Prueft u. a. ob der Entry zum aktuellen Kurs passt, Stop und
    Take-Profit auf der richtigen Seite liegen und das Chance-Risiko-Verhaeltnis nach
    Kosten sinnvoll ist. Ist `approve` False, wird das Setup blockiert.
    """

    name: ClassVar[AgentName] = AgentName.RIN
    role: ClassVar[str] = "Validiert Kursdaten, Entry, Stop-Loss und Take-Profit"

    @abstractmethod
    def validate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        ...
