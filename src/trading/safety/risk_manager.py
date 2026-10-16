"""Unabhaengiger, regelbasierter Risikomanager.

* Keine KI, kein Lernen, keine Konfiguration durch Strategien: nur feste Regeln.
* Er ist die letzte Instanz vor der Ausfuehrung. Der Executor ruft ihn selbst auf und
  vertraut NICHT darauf, dass SORA oder eine Strategie das Risiko schon geprueft hat.
* Die Regeln selbst stehen in `trading.safety.order_risk` (eine einzige Implementierung).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from trading.domain.exchange_rules import ExchangeRules
from trading.domain.models import PortfolioState, RiskDecision, TradeSetup
from trading.domain.reasons import RejectReason
from trading.execution.costs import CostModel, FillQuote
from trading.safety.limits import DEFAULT_LIMITS, SafetyLimits
from trading.safety.order_risk import evaluate_order, liquidation_price

__all__ = ["RiskManager", "liquidation_price"]


class RiskManager:
    def __init__(
        self, limits: SafetyLimits = DEFAULT_LIMITS, costs: Optional[CostModel] = None,
        rules: Optional[ExchangeRules] = None,
    ) -> None:
        self._limits = limits
        self._costs = costs or CostModel()
        self._rules = rules  # Boersenregeln (AP-13); ohne Regelwerk wird nur gegen die eigenen Limits geprueft

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
        rules = None
        if self._rules is not None:
            rules = self._rules.for_instrument(setup.instrument)
            if rules is None:  # Regelwerk vorhanden, aber nicht fuer dieses Instrument: nicht raten
                return RiskDecision(False, (RejectReason.EXCHANGE_RULES_MISSING,))
        return evaluate_order(self._limits, self._costs, setup, quantity, leverage, quote, portfolio, rules)

    @property
    def rules(self) -> Optional[ExchangeRules]:
        return self._rules
