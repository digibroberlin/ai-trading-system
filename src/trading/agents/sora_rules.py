"""SORA - regelbasierte Risikobewertung und Positionsgroessen-Berechnung.

SORA beantwortet: "Wie gross darf diese Position hoechstens sein - oder darf sie gar nicht
eroeffnet werden?" Alle Rechnungen sind deterministisch (kein Zufall, keine KI), zustandslos
und damit thread-sicher.

Vorgehen (immer vom ERWARTETEN EINSTIEGSKURS inkl. Spread und Slippage aus, nie vom Wunsch-Entry):

  1. Groesste Menge, die ALLE Grenzen zugleich einhaelt - jede Grenze liefert eine Obergrenze:
       Risiko je Trade    (Ziel 0,4 %, hoechstens 0,5 % der Equity)
       Gesamtrisiko       (2 %  der Equity nach dem Trade, abzueglich offener Risiken)
       Brutto-Exposure    (100 % der Equity nach dem Trade, abzueglich offener Positionswerte)
       Stress-Verlust     (5 %  der Equity nach dem Trade bei 10 % Kursschock gegen jede Position)
       Margin             (hoechstens 80 % der freien Margin)
     Die kleinste dieser Obergrenzen zaehlt; sie wird auf den Mengenschritt ABGERUNDET.
  2. Hebel (Futures): der kleinste, bei dem die Margin reicht - nie ueber dem Limit und nur,
     solange die Liquidation sicher HINTER dem Stop liegt. Reicht selbst der hoechste zulaessige
     Hebel nicht, wird die Menge verkleinert (nie vergroessert).
  3. Endkontrolle mit DERSELBEN Funktion, die auch der Risikomanager benutzt
     (`trading.safety.order_risk.evaluate_order`). Es gibt keine zweite Regel-Implementierung:
     Was SORA freigibt, akzeptiert der Risikomanager bei gleichem Kontostand ebenfalls.

Der Agent erhaelt KEIN SafetyLimits-Objekt, sondern eine unveraenderliche Zahlenkopie
(`SizingPolicy`), die ausserhalb des Agenten aus den Limits erzeugt wird.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from typing import ClassVar, Dict, List, Optional, Sequence

from trading.agents.base import AgentName
from trading.agents.contracts import MarketContext, SizingProposal
from trading.agents.sora import SoraAgent
from trading.ai.provider import AIProvider
from trading.domain.exchange_rules import ExchangeRules, InstrumentRules
from trading.domain.instrument import MarketType, is_instrument
from trading.domain.models import PortfolioState, RiskDecision, Side, TradeSetup
from trading.domain.numeric import is_finite_decimal, is_positive_decimal
from trading.domain.reasons import RejectReason as R
from trading.execution.costs import CostModel, cost_up
from trading.safety.limits import SafetyLimits
from trading.safety.order_risk import evaluate_order, liquidation_price, position_stress_loss

D = Decimal
ZERO = D(0)

_TEXT: Dict[R, str] = {
    R.MISSING_DATA: "Preis- oder Kontodaten unvollstaendig",
    R.NON_FINITE_VALUE: "Ungueltige Zahl (NaN/Infinity/float)",
    R.NON_POSITIVE_PRICE: "Preise muessen positiv sein",
    R.INVALID_SYMBOL: "Instrument fehlt oder ist formal ungueltig",
    R.SYMBOL_MISMATCH: "Marktdaten gehoeren nicht zum Instrument des Setups",
    R.CROSSED_BOOK: "Bid ueber Ask",
    R.NO_EQUITY: "Kein Eigenkapital",
    R.STOP_WRONG_SIDE: "Stop-Loss liegt nicht hinter dem Einstiegskurs",
    R.TAKE_PROFIT_WRONG_SIDE: "Take-Profit liegt nicht vor dem Einstiegskurs",
    R.FUNDING_MISSING: "Funding-Satz fehlt (Kosten nicht bestimmbar)",
    R.EQUITY_UNCERTAIN: "Bewertungskurse offener Positionen fehlen oder sind veraltet",
    R.SPOT_SHORT_NOT_ALLOWED: "Spot-Short ist verboten",
    R.TOO_MANY_POSITIONS: "Maximale Zahl offener Positionen erreicht",
    R.DUPLICATE_POSITION: "Es gibt bereits eine Position in diesem Instrument",
    R.DAILY_LOSS_LIMIT: "Tagesverlust-Limit erreicht",
    R.DRAWDOWN_LIMIT: "Drawdown-Limit erreicht",
    R.TRADE_RISK_TOO_HIGH: "Risiko je Trade waere zu hoch",
    R.PORTFOLIO_RISK_TOO_HIGH: "Gesamtrisiko-Limit (2 %) ausgeschoepft",
    R.GROSS_EXPOSURE_TOO_HIGH: "Brutto-Exposure-Limit (100 %) ausgeschoepft",
    R.STRESS_LOSS_TOO_HIGH: "Stress-Limit (Verlust bei Kursschock) ausgeschoepft",
    R.INSUFFICIENT_MARGIN: "Freie Margin reicht nicht",
    R.LIQUIDATION_TOO_CLOSE: "Liquidation laege zu nah am Stop",
    R.QUANTITY_TOO_SMALL: "Zulaessige Positionsgroesse liegt unter dem handelbaren Minimum",
}

# Gruende, die weder Menge noch Hebel aendern koennen: sofort blockieren.
_SIZE_INDEPENDENT = frozenset({
    R.NON_FINITE_VALUE, R.INVALID_SYMBOL, R.NO_EQUITY, R.INVALID_QUANTITY, R.STOP_WRONG_SIDE,
    R.TAKE_PROFIT_WRONG_SIDE, R.FUNDING_MISSING, R.EQUITY_UNCERTAIN, R.SPOT_SHORT_NOT_ALLOWED,
    R.TOO_MANY_POSITIONS, R.DUPLICATE_POSITION, R.DAILY_LOSS_LIMIT, R.DRAWDOWN_LIMIT, R.PRICE_NOT_ON_TICK,
})


@dataclass(frozen=True)
class SizingPolicy:
    """Schreibgeschuetzte Zahlenkopie der Sicherheitslimits (nur zum Rechnen)."""

    max_portfolio_risk: Decimal
    max_risk_per_trade: Decimal
    max_gross_exposure: Decimal
    stress_shock: Decimal
    max_stress_loss: Decimal
    max_leverage: Decimal
    max_open_positions: int
    max_daily_loss: Decimal
    max_drawdown: Decimal
    liquidation_buffer: Decimal
    maintenance_margin_rate: Decimal

    @staticmethod
    def from_limits(limits: SafetyLimits) -> "SizingPolicy":
        return SizingPolicy(
            max_portfolio_risk=limits.max_portfolio_risk,
            max_risk_per_trade=limits.max_risk_per_trade,
            max_gross_exposure=limits.max_gross_exposure,
            stress_shock=limits.stress_shock,
            max_stress_loss=limits.max_stress_loss,
            max_leverage=limits.max_leverage,
            max_open_positions=limits.max_open_positions,
            max_daily_loss=limits.max_daily_loss,
            max_drawdown=limits.max_drawdown,
            liquidation_buffer=limits.liquidation_buffer,
            maintenance_margin_rate=limits.maintenance_margin_rate,
        )


@dataclass(frozen=True)
class SoraConfig:
    """Eigene, vorsichtigere Praeferenzen von SORA (innerhalb der Policy, nie darueber)."""

    risk_per_trade: Decimal = D("0.004")  # Ziel: 0,4 % des Kapitals je Trade
    max_leverage: Decimal = D("3")
    margin_utilization: Decimal = D("0.8")  # hoechstens 80 % der freien Margin einsetzen
    liquidation_safety: Decimal = D("1.5")  # Liquidation mind. 1,5x so weit weg wie der Stop
    quantity_step: Decimal = D("0.0001")
    min_notional: Decimal = D("10")
    # Dynamisches Risiko (AP-20): ab `drawdown_cut` Rueckgang vom Hoechststand nur noch `drawdown_factor` x Risiko je
    # Trade. Kann das Risiko nur SENKEN (Faktor <= 1); 0 = aus. Die Grenzen des Risikomanagers gelten unveraendert.
    drawdown_cut: Decimal = D("0")
    drawdown_factor: Decimal = D("1")

    def __post_init__(self) -> None:
        for name in ("risk_per_trade", "max_leverage", "margin_utilization",
                     "liquidation_safety", "quantity_step", "min_notional"):
            if not is_positive_decimal(getattr(self, name)):
                raise ValueError("%s muss eine positive Decimal-Zahl sein." % name)
        if self.margin_utilization > 1:
            raise ValueError("margin_utilization darf 1 (=100 %) nicht uebersteigen.")
        if not is_finite_decimal(self.drawdown_cut) or not D(0) <= self.drawdown_cut < 1:
            raise ValueError("drawdown_cut muss zwischen 0 und 1 liegen.")
        if not is_positive_decimal(self.drawdown_factor) or self.drawdown_factor > 1:
            raise ValueError("drawdown_factor muss in (0, 1] liegen - das Risiko darf nur sinken.")

    def risk_for(self, portfolio: PortfolioState) -> Decimal:
        """Risiko je Trade fuer den aktuellen Zustand: gesenkt, solange der Rueckgang >= drawdown_cut ist."""
        if self.drawdown_cut > 0 and portfolio.peak_equity > 0:
            drawdown = (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity
            if drawdown >= self.drawdown_cut:
                return self.risk_per_trade * self.drawdown_factor
        return self.risk_per_trade


class RuleBasedSora(SoraAgent):
    name: ClassVar[AgentName] = AgentName.SORA

    def __init__(
        self,
        policy: SizingPolicy,
        costs: CostModel,
        config: Optional[SoraConfig] = None,
        ai: Optional[AIProvider] = None,
        rules: Optional[ExchangeRules] = None,
    ) -> None:
        super().__init__(ai)
        self._rules = rules  # Boersenregeln (AP-13): Mengenschritt, Mindestgroesse, Hoechsthebel je Instrument
        cfg = config or SoraConfig()
        if cfg.risk_per_trade > policy.max_risk_per_trade:
            raise ValueError("risk_per_trade ueberschreitet das Limit je Trade.")
        self._policy, self._costs, self._cfg = policy, costs, cfg
        self._max_leverage = min(policy.max_leverage, cfg.max_leverage)
        self._liq_factor = max(policy.liquidation_buffer, cfg.liquidation_safety)

    # ------------------------------------------------------------------ Hilfsfunktionen

    def risk_budget(self, portfolio: PortfolioState) -> Decimal:
        """Betrag (in Geld), den ein NEUER Trade nach den beiden Risiko-Grenzen hoechstens riskieren darf
        (ohne Exposure-, Stress- und Margin-Grenze - die begrenzen die Menge zusaetzlich)."""
        per_trade = self._cfg.risk_for(portfolio) * portfolio.equity
        remaining = self._policy.max_portfolio_risk * portfolio.equity - portfolio.open_risk
        return max(min(per_trade, remaining), ZERO)

    @staticmethod
    def _floor(qty: Decimal, step: Decimal) -> Decimal:
        return (qty / step).to_integral_value(rounding=ROUND_DOWN) * step

    @staticmethod
    def _ceil(qty: Decimal, step: Decimal) -> Decimal:
        return (qty / step).to_integral_value(rounding=ROUND_CEILING) * step

    def _leverage_candidates(self, setup: TradeSetup, fill: Decimal) -> List[Decimal]:
        if setup.instrument.market_type is MarketType.SPOT:
            return [D(1)]
        dist_stop = abs(fill - setup.stop_loss)
        out: List[Decimal] = []
        for i in range(1, int(self._max_leverage) + 1):
            liq = liquidation_price(setup.side, fill, D(i), self._policy.maintenance_margin_rate)
            if abs(fill - liq) >= self._liq_factor * dist_stop:
                out.append(D(i))
        return out

    # ------------------------------------------------------------------ Vertrag

    def size(
        self, setup: TradeSetup, context: MarketContext, portfolio: PortfolioState
    ) -> SizingProposal:
        sid = setup.setup_id if isinstance(getattr(setup, "setup_id", None), str) else "?"

        def block(reasons: Sequence[R], *extra: str) -> SizingProposal:
            unique = tuple(dict.fromkeys(reasons))
            notes = tuple(_TEXT.get(r, r.value) for r in unique) + extra
            return SizingProposal(sid, False, ZERO, D(1), notes, unique)

        # --- 1. Lassen sich ueberhaupt Kurse bilden? ---
        snap = context.snapshot
        if not is_instrument(setup.instrument) or not isinstance(setup.side, Side):
            return block([R.INVALID_SYMBOL])
        if snap.instrument != setup.instrument:
            return block([R.SYMBOL_MISMATCH])
        prices = (setup.entry, setup.stop_loss, setup.take_profit, snap.bid, snap.ask)
        if any(x is None for x in prices):
            return block([R.MISSING_DATA])
        if not all(is_finite_decimal(x) for x in prices):
            return block([R.NON_FINITE_VALUE])
        if not all(x > 0 for x in prices):
            return block([R.NON_POSITIVE_PRICE])
        if snap.bid > snap.ask:
            return block([R.CROSSED_BOOK])

        pol, cfg, costs, side = self._policy, self._cfg, self._costs, setup.side
        quote = costs.quote(side, snap)
        fill, mark = quote.fill_price, quote.mark_price
        rules: Optional[InstrumentRules] = None
        if self._rules is not None:
            rules = self._rules.for_instrument(setup.instrument)
            if rules is None:
                return block([R.EXCHANGE_RULES_MISSING])
        step = rules.quantity_step if rules is not None else cfg.quantity_step

        def check(quantity: Decimal, leverage: Decimal) -> RiskDecision:
            return evaluate_order(pol, costs, setup, quantity, leverage, quote, portfolio, rules)

        # --- 2. Gruende, an denen keine Menge etwas aendert (alle gesammelt) ---
        fatal = [r for r in check(step, D(1)).reasons if r in _SIZE_INDEPENDENT]
        if fatal:
            return block(fatal)
        candidates = [lv for lv in self._leverage_candidates(setup, fill)
                      if rules is None or lv <= rules.max_leverage]
        if not candidates:
            return block([R.LIQUIDATION_TOO_CLOSE])

        # --- 3. Groesste Menge, die alle Grenzen zugleich einhaelt (jede Grenze ist linear in der Menge) ---
        equity = portfolio.equity
        fee_unit = costs.fee(fill)
        cost_unit = fee_unit + side.sign * (fill - mark)  # mindert die Equity sofort
        loss_unit = costs.worst_case_loss(side, D(1), fill, setup.stop_loss, quote.funding_rate)

        def cap(budget: Decimal, per_unit: Decimal, limit: Decimal) -> Decimal:
            return budget / (per_unit + limit * cost_unit)

        by_trade = cfg.risk_for(portfolio) * equity / loss_unit
        by_risk = cap(pol.max_portfolio_risk * equity - portfolio.open_risk,
                      loss_unit - fee_unit, pol.max_portfolio_risk)
        by_exposure = cap(pol.max_gross_exposure * equity - portfolio.gross_exposure,
                          mark, pol.max_gross_exposure)

        def limit_for(lev: Decimal) -> Decimal:
            liq = (liquidation_price(side, fill, lev, pol.maintenance_margin_rate)
                   if setup.instrument.market_type is MarketType.FUTURES else None)
            stress_unit = position_stress_loss(
                side, D(1), mark, fill, cost_up(fill / lev), liq, pol.stress_shock, costs)
            by_stress = cap(pol.max_stress_loss * equity - portfolio.stress_loss,
                            stress_unit, pol.max_stress_loss)
            return min(by_trade, by_risk, by_exposure, by_stress)

        def margin_for(lev: Decimal) -> Decimal:
            return portfolio.free_margin * cfg.margin_utilization / (fill / lev + fee_unit)

        leverage = next((lv for lv in candidates if margin_for(lv) >= limit_for(lv)), candidates[-1])
        raw = min(limit_for(leverage), margin_for(leverage))
        quantity = self._floor(raw, step) if raw > 0 else ZERO
        minimum = self._ceil(cfg.min_notional / fill, step)
        if rules is not None:
            minimum = max(minimum, rules.minimum_quantity(fill))

        if quantity < minimum:
            # Welche Grenzen verhindern schon die kleinste handelbare Groesse?
            return block([*check(minimum, leverage).reasons, R.QUANTITY_TOO_SMALL])

        # --- 4. Endkontrolle mit den abgerundeten Werten (dieselbe Funktion wie der Risikomanager) ---
        decision = check(quantity, leverage)
        for _ in range(3):  # Rundungsreste: hoechstens ein paar Mengenschritte zurueck, nie nach oben
            if decision.approved or quantity - step < minimum:
                break
            quantity -= step
            decision = check(quantity, leverage)
        if not decision.approved:
            return block(decision.reasons)

        note = "Risiko %.4f %% des Kapitals, Hebel %sx, Exposure danach %.1f %%, Stress danach %.2f %%" % (
            decision.risk_pct * 100, leverage,
            decision.gross_exposure_pct_after * 100, decision.stress_loss_pct_after * 100)
        return SizingProposal(
            setup_id=sid,
            approve=True,
            quantity=quantity,
            leverage=leverage,
            reasons=(note,),
            planned_loss=decision.worst_case_loss,
            risk_pct=decision.risk_pct,
            notional=quantity * fill,
            margin=decision.margin_required,
            liquidation_price=decision.liquidation_price,
        )


__all__ = ["RuleBasedSora", "SizingPolicy", "SoraConfig"]
