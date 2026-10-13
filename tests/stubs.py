"""Einfache Test-Agenten (keine echte Strategie!). Sie zeigen nur, dass die Schnittstellen tragen."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Sequence

from trading.agents import (
    AgentName,
    AgentOpinion,
    KiboAgent,
    MarketContext,
    MomoAgent,
    RinAgent,
    SizingProposal,
    SoraAgent,
    YuzuAgent,
)
from trading.domain.models import PortfolioState, TradeSetup
from trading.execution.costs import CostModel

D = Decimal


class StubMomo(MomoAgent):
    def __init__(self, setups: Sequence[TradeSetup]) -> None:
        super().__init__()
        self.setups = list(setups)

    def scan(self, contexts: Sequence[MarketContext]) -> List[TradeSetup]:
        return list(self.setups)


class BrokenMomo(MomoAgent):
    def scan(self, contexts: Sequence[MarketContext]) -> List[TradeSetup]:
        raise RuntimeError("MOMO abgestuerzt")


class StubRin(RinAgent):
    def __init__(self, approve: bool = True) -> None:
        super().__init__()
        self.approve = approve

    def validate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        return AgentOpinion(AgentName.RIN, setup.setup_id, self.approve, D("0.9"), ("rin-stub",))


class StubKibo(KiboAgent):
    def __init__(self, approve: bool = True, explode: bool = False) -> None:
        super().__init__()
        self.approve, self.explode = approve, explode

    def evaluate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        if self.explode:
            raise RuntimeError("KIBO abgestuerzt")
        return AgentOpinion(AgentName.KIBO, setup.setup_id, self.approve, D("0.7"), ("kibo-stub",))


class StubYuzu(YuzuAgent):
    def __init__(self, approve: bool = True) -> None:
        super().__init__()
        self.approve = approve

    def evaluate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        return AgentOpinion(AgentName.YUZU, setup.setup_id, self.approve, D("0.6"), ("yuzu-stub",))


class StubSora(SoraAgent):
    """Dimensioniert auf einen festen Anteil des Kapitals; `override_qty` simuliert einen Fehler."""

    def __init__(
        self,
        costs: CostModel,
        risk_fraction: Decimal = D("0.004"),
        leverage: Decimal = D(1),
        approve: bool = True,
        override_qty: Optional[Decimal] = None,
    ) -> None:
        super().__init__()
        self.costs, self.fraction, self.leverage = costs, risk_fraction, leverage
        self.approve, self.override_qty = approve, override_qty

    def size(
        self, setup: TradeSetup, context: MarketContext, portfolio: PortfolioState
    ) -> SizingProposal:
        if not self.approve:
            return SizingProposal(setup.setup_id, False, D(0), D(1), ("sora-stub: kein Trade",))
        per_unit = self.costs.worst_case_loss(setup.side, D(1), setup.entry, setup.stop_loss)
        qty = self.override_qty or (portfolio.equity * self.fraction / per_unit).quantize(D("0.0001"))
        return SizingProposal(setup.setup_id, True, qty, self.leverage)
