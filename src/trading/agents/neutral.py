"""Neutrale Platzhalter fuer KIBO und YUZU.

KIBO und YUZU sind Bewerter von Handelssignalen - KEINE Sicherheitsinstanzen. Solange es fuer sie
keine eigene Logik gibt, geben diese Platzhalter kein Urteil ab und halten den Ablauf nicht auf.
Die Sicherheit haengt nicht an ihnen: RIN, SORA und der unabhaengige Risikomanager pruefen unveraendert.
In Ergebnissen (z. B. Backtests) wird ausgewiesen, wenn neutrale Platzhalter im Einsatz waren.
"""

from __future__ import annotations

from decimal import Decimal

from trading.agents.base import AgentName
from trading.agents.contracts import AgentOpinion, MarketContext
from trading.agents.kibo import KiboAgent
from trading.agents.yuzu import YuzuAgent
from trading.domain.models import TradeSetup

_NOTE = ("kein eigenes Urteil (neutraler Platzhalter)",)


class NeutralKibo(KiboAgent):
    def evaluate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        return AgentOpinion(AgentName.KIBO, setup.setup_id, True, Decimal("0.5"), _NOTE)


class NeutralYuzu(YuzuAgent):
    def evaluate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        return AgentOpinion(AgentName.YUZU, setup.setup_id, True, Decimal("0.5"), _NOTE)
