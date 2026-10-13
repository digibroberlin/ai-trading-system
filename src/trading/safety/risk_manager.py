"""Unabhaengiger, regelbasierter Risikomanager.

* Keine KI, kein Lernen, keine Konfiguration durch Strategien: nur feste Regeln.
* Er ist die letzte Instanz vor der Ausfuehrung. Der Executor ruft ihn selbst auf und
  vertraut NICHT darauf, dass SORA oder eine Strategie das Risiko schon geprueft hat.
* Die Regeln selbst stehen in `trading.safety.order_risk` (eine einzige Implementierung).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from trading.domain.models import PortfolioState, RiskDecision, TradeSetup
from trading.execution.costs import CostModel, FillQuote
from trading.safety.limits import DEFAULT_LIMITS, SafetyLimits
from trading.safety.order_risk import evaluate_order, liquidation_price

__all__ = ["RiskManager", "liquidation_price"]


class RiskManager:
    def __init__(
        self, limits: SafetyLimits = DEFAULT_LIMITS, costs: Optional[CostModel] = None
    ) -> None:
        self._limits = limits
        self._costs = costs or CostModel()

    @property
    def limits(self) -> SafetyLimits:
        return self._limits

    @property
    def costs(self) -> CostModel:
        return self._costs

    def evaluate(
        self,
        setup: TradeSetup,
        quantity: Decimal,
        leverage: Decimal,
        quote: FillQuote,
        portfolio: PortfolioState,
    ) -> RiskDecision:
        """`quote` enthaelt den erwarteten tatsaechlichen Einstiegskurs (inkl. Spread und Slippage),
        nicht den von MOMO gewuenschten Kurs."""
        return evaluate_order(self._limits, self._costs, setup, quantity, leverage, quote, portfolio)
