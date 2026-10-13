"""Unabhaengiger, regelbasierter Risikomanager.

* Keine KI, kein Lernen, keine Konfiguration durch Strategien: nur feste Regeln.
* Er ist die letzte Instanz vor der Ausfuehrung. Der Executor ruft ihn selbst auf und
  vertraut NICHT darauf, dass SORA oder eine Strategie das Risiko schon geprueft hat.
* Alle verletzten Regeln werden gesammelt (nicht nur die erste), damit das Protokoll
  vollstaendig erklaert, warum ein Setup blockiert wurde.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from trading.domain.numeric import is_finite_decimal
from trading.domain.models import MarketType, PortfolioState, RiskDecision, Side, TradeSetup
from trading.domain.reasons import RejectReason
from trading.execution.costs import CostModel
from trading.safety.limits import DEFAULT_LIMITS, SafetyLimits


def liquidation_price(
    side: Side, entry: Decimal, leverage: Decimal, maintenance_margin_rate: Decimal
) -> Decimal:
    """Vereinfachte Liquidationsformel fuer isolierte Margin."""
    if side is Side.LONG:
        return entry * (1 - 1 / leverage + maintenance_margin_rate)
    return entry * (1 + 1 / leverage - maintenance_margin_rate)


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
        entry_price: Decimal,
        funding_rate: Optional[Decimal],
        portfolio: PortfolioState,
    ) -> RiskDecision:
        """`entry_price` ist der erwartete tatsaechliche Einstiegskurs (inkl. Slippage),
        nicht der von MOMO gewuenschte Kurs."""
        lim = self._limits
        reasons = []  # type: List[RejectReason]

        # --- 1. Ungueltige Eingaben: sofort abbrechen, weitere Rechnungen waeren sinnlos ---
        numbers = (
            quantity, leverage, entry_price, setup.stop_loss, setup.take_profit,
            portfolio.equity, portfolio.free_margin, portfolio.open_risk,
            portfolio.day_start_equity, portfolio.peak_equity,
        )
        if not all(is_finite_decimal(x) for x in numbers):
            return RiskDecision(False, (RejectReason.NON_FINITE_VALUE,))
        if funding_rate is not None and not is_finite_decimal(funding_rate):
            return RiskDecision(False, (RejectReason.NON_FINITE_VALUE,))
        if portfolio.equity <= 0:
            return RiskDecision(False, (RejectReason.NO_EQUITY,))
        if quantity <= 0 or entry_price <= 0:
            return RiskDecision(False, (RejectReason.INVALID_QUANTITY,))

        side = setup.side
        stop, tp = setup.stop_loss, setup.take_profit
        if side is Side.LONG:
            if not stop < entry_price:
                reasons.append(RejectReason.STOP_WRONG_SIDE)
            if not tp > entry_price:
                reasons.append(RejectReason.TAKE_PROFIT_WRONG_SIDE)
        else:
            if not stop > entry_price:
                reasons.append(RejectReason.STOP_WRONG_SIDE)
            if not tp < entry_price:
                reasons.append(RejectReason.TAKE_PROFIT_WRONG_SIDE)
        if stop <= 0 or tp <= 0:
            reasons.append(RejectReason.STOP_WRONG_SIDE)
        if reasons:
            return RiskDecision(False, tuple(reasons))

        # --- 2. Markt- und Hebelregeln ---
        if setup.market_type is MarketType.SPOT:
            if side is Side.SHORT:
                reasons.append(RejectReason.SPOT_SHORT_NOT_ALLOWED)
            if leverage != 1:
                reasons.append(RejectReason.SPOT_LEVERAGE_NOT_ALLOWED)
        if leverage < 1:
            reasons.append(RejectReason.LEVERAGE_TOO_LOW)
        if leverage > lim.max_leverage:
            reasons.append(RejectReason.LEVERAGE_TOO_HIGH)
        if leverage < 1:
            return RiskDecision(False, tuple(reasons))  # Weiterrechnen waere sinnlos

        # --- 3. Risiko ---
        worst = self._costs.worst_case_loss(side, quantity, entry_price, stop, funding_rate)
        risk_pct = worst / portfolio.equity
        total_after = (portfolio.open_risk + worst) / portfolio.equity
        if risk_pct > lim.max_risk_per_trade:
            reasons.append(RejectReason.TRADE_RISK_TOO_HIGH)
        if total_after > lim.max_portfolio_risk:
            reasons.append(RejectReason.PORTFOLIO_RISK_TOO_HIGH)

        # --- 4. Bestand ---
        if portfolio.open_count >= lim.max_open_positions:
            reasons.append(RejectReason.TOO_MANY_POSITIONS)
        if (setup.symbol, setup.market_type) in portfolio.open_keys:
            reasons.append(RejectReason.DUPLICATE_POSITION)

        # --- 5. Margin und Liquidation ---
        notional = quantity * entry_price
        margin = notional / leverage
        if margin + self._costs.fee(notional) > portfolio.free_margin:
            reasons.append(RejectReason.INSUFFICIENT_MARGIN)

        liq = None  # type: Optional[Decimal]
        if setup.market_type is MarketType.FUTURES:
            liq = liquidation_price(side, entry_price, leverage, lim.maintenance_margin_rate)
            if abs(entry_price - liq) < lim.liquidation_buffer * abs(entry_price - stop):
                reasons.append(RejectReason.LIQUIDATION_TOO_CLOSE)

        # --- 6. Notbremsen (Tagesverlust, Drawdown) ---
        if portfolio.day_start_equity > 0:
            day_loss = (portfolio.day_start_equity - portfolio.equity) / portfolio.day_start_equity
            if day_loss >= lim.max_daily_loss:
                reasons.append(RejectReason.DAILY_LOSS_LIMIT)
        if portfolio.peak_equity > 0:
            drawdown = (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity
            if drawdown >= lim.max_drawdown:
                reasons.append(RejectReason.DRAWDOWN_LIMIT)

        return RiskDecision(
            approved=not reasons,
            reasons=tuple(reasons),
            worst_case_loss=worst,
            risk_pct=risk_pct,
            total_risk_pct_after=total_after,
            margin_required=margin,
            liquidation_price=liq,
        )
