"""Die EINE Risikorechnung des Systems (reine Funktionen, kein Zustand, keine KI).

Grundsatz: Geprueft wird der Zustand NACH der Order gegen die AKTUELLE Netto-Equity nach allen
bereits angefallenen Kosten. Eine Order ist nur zulaessig, wenn danach ALLE Grenzen zugleich gelten:

  1. Risiko dieses Trades        Hoechstverlust inkl. aller Kosten      <= 0,5 % der Equity
  2. Geplantes Gesamtrisiko      Summe des noch moeglichen geplanten
                                 Verlusts aller offenen Positionen      <= 2 %   der Equity danach
  3. Brutto-Exposure             Summe Menge x Bewertungskurs           <= 100 % der Equity danach
  4. Stress-Verlust              Verlust, wenn JEDER Kurs schlagartig
                                 10 % gegen die Position springt
                                 (Stops greifen dann nicht)             <= 5 %   der Equity danach
  5. Margin, Hebel, Liquidationsabstand, Positionszahl, Notbremsen

"Equity danach" = Equity jetzt - Einstiegsgebuehr - sofortige Spread-/Slippage-Kosten der neuen Position
(sie wird zum Briefkurs gekauft, aber zur Mitte bewertet). Genau diesen Zustand sieht auch die
Datenbank beim COMMIT - beide Pruefungen koennen daher nicht voneinander abweichen.

Nicht bestimmbares Risiko (fehlende/ungueltige Zahlen, veraltete Bewertungskurse) fuehrt immer zur Ablehnung.

Diese Funktionen nutzen der Risikomanager (verbindlich) und SORA (fuer die eigene Endkontrolle).
Es gibt absichtlich keine zweite Implementierung.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Protocol

from trading.domain.instrument import MarketType, is_instrument
from trading.domain.models import PortfolioState, RiskDecision, Side, TradeSetup
from trading.domain.numeric import is_finite_decimal
from trading.domain.reasons import RejectReason
from trading.execution.costs import CostModel, FillQuote, cost_up

ZERO = Decimal(0)


class RiskLimits(Protocol):
    """Die Zahlen, die die Risikorechnung braucht (SafetyLimits oder deren schreibgeschuetzte Kopie)."""

    @property
    def max_portfolio_risk(self) -> Decimal: ...
    @property
    def max_risk_per_trade(self) -> Decimal: ...
    @property
    def max_gross_exposure(self) -> Decimal: ...
    @property
    def stress_shock(self) -> Decimal: ...
    @property
    def max_stress_loss(self) -> Decimal: ...
    @property
    def max_leverage(self) -> Decimal: ...
    @property
    def max_open_positions(self) -> int: ...
    @property
    def max_daily_loss(self) -> Decimal: ...
    @property
    def max_drawdown(self) -> Decimal: ...
    @property
    def liquidation_buffer(self) -> Decimal: ...
    @property
    def maintenance_margin_rate(self) -> Decimal: ...


def liquidation_price(
    side: Side, entry: Decimal, leverage: Decimal, maintenance_margin_rate: Decimal
) -> Decimal:
    """Vereinfachte Liquidationsformel fuer isolierte Margin."""
    if side is Side.LONG:
        return entry * (1 - 1 / leverage + maintenance_margin_rate)
    return entry * (1 + 1 / leverage - maintenance_margin_rate)


def is_beyond(side: Side, price: Decimal, level: Decimal) -> bool:
    """True, wenn `price` aus Sicht der Position auf oder jenseits von `level` liegt (Long: darunter)."""
    return price <= level if side is Side.LONG else price >= level


def position_stress_loss(
    side: Side,
    quantity: Decimal,
    mark: Decimal,
    entry_price: Decimal,
    margin: Decimal,
    liquidation: Optional[Decimal],
    shock: Decimal,
    costs: CostModel,
) -> Decimal:
    """Equity-Verlust AB JETZT, wenn der Kurs schlagartig um `shock` gegen die Position springt.

    Bei einer Kursluecke schuetzt kein Stop: Ausgefuehrt wird zum geschockten Kurs (mit Stop-Slippage
    und Taker-Gebuehr). Erreicht der Schock bei Futures den Liquidationskurs, ist die gesamte isolierte
    Margin verloren (samt der bis dahin aufgelaufenen Buchgewinne/-verluste)."""
    shocked = mark * (1 - side.sign * shock)
    if liquidation is not None and is_beyond(side, shocked, liquidation):
        unrealized = side.sign * (mark - entry_price) * quantity
        return max(cost_up(margin + unrealized), ZERO)
    exit_price = costs.stop_exit_price(side, shocked)
    return cost_up(side.sign * (mark - exit_price) * quantity) + costs.fee(quantity * exit_price)


def evaluate_order(
    limits: RiskLimits,
    costs: CostModel,
    setup: TradeSetup,
    quantity: Decimal,
    leverage: Decimal,
    quote: FillQuote,
    portfolio: PortfolioState,
) -> RiskDecision:
    """Prueft eine Order. Alle verletzten Regeln werden gesammelt (nicht nur die erste), damit das
    Protokoll vollstaendig erklaert, warum ein Setup blockiert wurde."""
    reasons: List[RejectReason] = []
    fill, mark, funding = quote.fill_price, quote.mark_price, quote.funding_rate

    # --- 1. Nicht bestimmbar: sofort abbrechen, weitere Rechnungen waeren sinnlos ---
    numbers = (
        quantity, leverage, fill, mark, setup.stop_loss, setup.take_profit,
        portfolio.equity, portfolio.free_margin, portfolio.open_risk, portfolio.day_start_equity,
        portfolio.peak_equity, portfolio.gross_exposure, portfolio.stress_loss,
    )
    if not all(is_finite_decimal(x) for x in numbers):
        return RiskDecision(False, (RejectReason.NON_FINITE_VALUE,))
    if funding is not None and not is_finite_decimal(funding):
        return RiskDecision(False, (RejectReason.NON_FINITE_VALUE,))
    if not is_instrument(setup.instrument) or not isinstance(setup.side, Side):
        return RiskDecision(False, (RejectReason.INVALID_SYMBOL,))
    if portfolio.equity <= 0:
        return RiskDecision(False, (RejectReason.NO_EQUITY,))
    if quantity <= 0 or fill <= 0 or mark <= 0:
        return RiskDecision(False, (RejectReason.INVALID_QUANTITY,))

    side, market = setup.side, setup.instrument.market_type
    stop, tp = setup.stop_loss, setup.take_profit
    if not side.sign * (fill - stop) > 0 or stop <= 0:
        reasons.append(RejectReason.STOP_WRONG_SIDE)
    if not side.sign * (tp - fill) > 0 or tp <= 0:
        reasons.append(RejectReason.TAKE_PROFIT_WRONG_SIDE)
    if market is MarketType.FUTURES and funding is None:
        reasons.append(RejectReason.FUNDING_MISSING)  # Kosten nicht bestimmbar
    if reasons:
        return RiskDecision(False, tuple(reasons))

    # --- 2. Bewertung der bestehenden Positionen muss aktuell sein ---
    if not portfolio.valuation_complete:
        reasons.append(RejectReason.EQUITY_UNCERTAIN)

    # --- 3. Markt- und Hebelregeln ---
    if market is MarketType.SPOT:
        if side is Side.SHORT:
            reasons.append(RejectReason.SPOT_SHORT_NOT_ALLOWED)
        if leverage != 1:
            reasons.append(RejectReason.SPOT_LEVERAGE_NOT_ALLOWED)
    if leverage > limits.max_leverage:
        reasons.append(RejectReason.LEVERAGE_TOO_HIGH)
    if leverage < 1:
        reasons.append(RejectReason.LEVERAGE_TOO_LOW)
        return RiskDecision(False, tuple(reasons))  # Weiterrechnen waere sinnlos

    # --- 4. Zustand NACH der Order ---
    notional = quantity * fill
    entry_fee = costs.fee(notional)
    worst = costs.worst_case_loss(side, quantity, fill, stop, funding)
    margin = cost_up(notional / leverage)
    liq = (
        liquidation_price(side, fill, leverage, limits.maintenance_margin_rate)
        if market is MarketType.FUTURES
        else None
    )
    spread_cost = side.sign * (fill - mark) * quantity  # sofortiger Buchverlust: Kauf zum Brief, Bewertung zur Mitte
    equity_after = portfolio.equity - entry_fee - spread_cost
    risk_pct = worst / portfolio.equity
    if equity_after <= 0:
        reasons.append(RejectReason.NO_EQUITY)
        return RiskDecision(False, tuple(reasons), worst, entry_fee, risk_pct)

    total_risk_after = (portfolio.open_risk + worst - entry_fee) / equity_after
    exposure_after = (portfolio.gross_exposure + quantity * mark) / equity_after
    stress_new = position_stress_loss(side, quantity, mark, fill, margin, liq, limits.stress_shock, costs)
    stress_after = (portfolio.stress_loss + stress_new) / equity_after

    if risk_pct > limits.max_risk_per_trade:
        reasons.append(RejectReason.TRADE_RISK_TOO_HIGH)
    if total_risk_after > limits.max_portfolio_risk:
        reasons.append(RejectReason.PORTFOLIO_RISK_TOO_HIGH)
    if exposure_after > limits.max_gross_exposure:
        reasons.append(RejectReason.GROSS_EXPOSURE_TOO_HIGH)
    if stress_after > limits.max_stress_loss:
        reasons.append(RejectReason.STRESS_LOSS_TOO_HIGH)

    # --- 5. Bestand ---
    if portfolio.open_count >= limits.max_open_positions:
        reasons.append(RejectReason.TOO_MANY_POSITIONS)
    if setup.instrument in portfolio.open_keys:
        reasons.append(RejectReason.DUPLICATE_POSITION)

    # --- 6. Margin und Liquidation ---
    if margin + entry_fee > portfolio.free_margin:
        reasons.append(RejectReason.INSUFFICIENT_MARGIN)
    if liq is not None and abs(fill - liq) < limits.liquidation_buffer * abs(fill - stop):
        reasons.append(RejectReason.LIQUIDATION_TOO_CLOSE)

    # --- 7. Notbremsen (Tagesverlust, Drawdown) ---
    if portfolio.day_start_equity > 0:
        day_loss = (portfolio.day_start_equity - portfolio.equity) / portfolio.day_start_equity
        if day_loss >= limits.max_daily_loss:
            reasons.append(RejectReason.DAILY_LOSS_LIMIT)
    if portfolio.peak_equity > 0:
        drawdown = (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity
        if drawdown >= limits.max_drawdown:
            reasons.append(RejectReason.DRAWDOWN_LIMIT)

    return RiskDecision(
        approved=not reasons,
        reasons=tuple(reasons),
        worst_case_loss=worst,
        entry_fee=entry_fee,
        risk_pct=risk_pct,
        total_risk_pct_after=total_risk_after,
        gross_exposure_pct_after=exposure_after,
        stress_loss_pct_after=stress_after,
        margin_required=margin,
        liquidation_price=liq,
    )
