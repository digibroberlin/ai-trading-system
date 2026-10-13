from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar, List, Sequence

from trading.agents.base import AgentName, BaseAgent
from trading.agents.contracts import ExitSignal, MarketContext
from trading.domain.models import Position, TradeSetup


class MomoAgent(BaseAgent):
    """MOMO - erkennt Trading-Chancen und Markttrends.

    Vertrag: liefert Vorschlaege (TradeSetup) mit Entry, Stop-Loss und Take-Profit sowie
    Ausstiegssignale fuer offene Positionen. Ein Vorschlag ist noch KEINE Erlaubnis zu handeln.
    """

    name: ClassVar[AgentName] = AgentName.MOMO
    role: ClassVar[str] = "Erkennt Trading-Chancen und Markttrends"

    @abstractmethod
    def scan(self, contexts: Sequence[MarketContext]) -> List[TradeSetup]:
        """Untersucht die Maerkte und schlaegt Setups vor. `setup_id` muss eindeutig sein."""

    def exit_signals(
        self, contexts: Sequence[MarketContext], positions: Sequence[Position]
    ) -> List[ExitSignal]:
        """Ausstiegssignale fuer offene Positionen. Standard: keine (Stop-Loss/Take-Profit genuegen)."""
        return []
