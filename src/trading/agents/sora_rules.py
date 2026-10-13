"""SORA - regelbasierte Risikobewertung und Positionsgroessen-Berechnung.

SORA beantwortet: "Wie gross darf diese Position hoechstens sein - oder darf sie gar nicht
eroeffnet werden?" Alle Rechnungen sind deterministisch (kein Zufall, keine KI), zustandslos
und damit thread-sicher.

Rechenweg (immer vom ERWARTETEN EINSTIEGSKURS inkl. Slippage aus, nicht vom Wunsch-Entry):

  Risikobudget     = min( Ziel-Risiko je Trade,
                          2 % Gesamtrisiko - bereits eingeplantes Risiko aller offenen Positionen )
                     (jeweils in % des AKTUELLEN Eigenkapitals; gilt fuer ALLE Strategien/Agenten)
  Verlust je Stueck = Kursverlust bis Stop (inkl. Stop-Slippage) + Gebuehr Ein- und Ausstieg
                      (+ eine Funding-Zahlung als Puffer bei Futures)
  Menge            = Risikobudget / Verlust je Stueck   -> auf Mengenschritt ABGERUNDET
  Hebel (Futures)  = kleinster Hebel, bei dem die Margin reicht; nie hoeher als das Limit und nur
                     solange die Liquidation sicher HINTER dem Stop liegt. Reicht selbst der
                     hoechste zulaessige Hebel nicht, wird die Menge verkleinert (nie vergroessert).

Abrunden garantiert: der tatsaechliche Hoechstverlust liegt nie ueber dem Budget.
Der Risikomanager im Executor prueft danach unabhaengig noch einmal.

Der Agent erhaelt KEIN SafetyLimits-Objekt, sondern eine unveraenderliche Zahlenkopie
(`SizingPolicy`), die ausserhalb des Agenten aus den Limits erzeugt wird.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import ClassVar, List, Optional, Tuple

from trading.agents.base import AgentName
from trading.agents.contracts import MarketContext, SizingProposal
from trading.agents.sora import SoraAgent
from trading.ai.provider import AIProvider
from trading.domain.models import MarketType, PortfolioState, Side, TradeSetup
from trading.domain.numeric import is_finite_decimal, is_positive_decimal
from trading.domain.reasons import RejectReason as R
from trading.execution.costs import CostModel
from trading.safety.limits import SafetyLimits
from trading.safety.risk_manager import liquidation_price

D = Decimal


@dataclass(frozen=True)
class SizingPolicy:
    """Schreibgeschuetzte Zahlenkopie der Sicherheitslimits (nur zum Rechnen)."""

    max_portfolio_risk: Decimal
    max_risk_per_trade: Decimal
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

    def __post_init__(self) -> None:
        for name in ("risk_per_trade", "max_leverage", "margin_utilization",
                     "liquidation_safety", "quantity_step", "min_notional"):
            if not is_positive_decimal(getattr(self, name)):
                raise ValueError("%s muss eine positive Decimal-Zahl sein." % name)
        if self.margin_utilization > 1:
            raise ValueError("margin_utilization darf 1 (=100 %) nicht uebersteigen.")


class RuleBasedSora(SoraAgent):
    name: ClassVar[AgentName] = AgentName.SORA

    def __init__(
        self,
        policy: SizingPolicy,
        costs: CostModel,
        config: Optional[SoraConfig] = None,
        ai: Optional[AIProvider] = None,
    ) -> None:
        super().__init__(ai)
        cfg = config or SoraConfig()
        if cfg.risk_per_trade > policy.max_risk_per_trade:
            raise ValueError("risk_per_trade ueberschreitet das Limit je Trade.")
        self._policy, self._costs, self._cfg = policy, costs, cfg
        self._max_leverage = min(policy.max_leverage, cfg.max_leverage)
        self._liq_factor = max(policy.liquidation_buffer, cfg.liquidation_safety)

    # ------------------------------------------------------------------ Hilfsfunktionen

    def risk_budget(self, portfolio: PortfolioState) -> Decimal:
        """Betrag (in Geld), den ein NEUER Trade hoechstens riskieren darf."""
        equity = portfolio.equity
        per_trade = min(self._cfg.risk_per_trade, self._policy.max_risk_per_trade) * equity
        remaining = self._policy.max_portfolio_risk * equity - portfolio.open_risk
        return max(min(per_trade, remaining), D(0))

    def _floor_qty(self, qty: Decimal) -> Decimal:
        step = self._cfg.quantity_step
        return (qty / step).to_integral_value(rounding=ROUND_DOWN) * step

    # ------------------------------------------------------------------ Vertrag

    def size(
        self, setup: TradeSetup, context: MarketContext, portfolio: PortfolioState
    ) -> SizingProposal:
        reasons = []  # type: List[R]
        notes = []  # type: List[str]
        sid = setup.setup_id if isinstance(getattr(setup, "setup_id", None), str) else "?"

        def block(*extra: Tuple[R, str]) -> SizingProposal:
            for code, text in extra:
                if code not in reasons:
                    reasons.append(code)
                notes.append(text)
            return SizingProposal(sid, False, D(0), D(1), tuple(notes), tuple(reasons))

        snap = context.snapshot
        pol, cfg, costs = self._policy, self._cfg, self._costs

        # --- strukturelle Pruefungen: Abbruch, wenn nicht rechenbar ---
        if not isinstance(setup.side, Side) or not isinstance(setup.market_type, MarketType):
            return block((R.MISSING_DATA, "Seite oder Markttyp fehlen"))
        numbers = (setup.entry, setup.stop_loss, setup.take_profit, snap.bid, snap.ask,
                   portfolio.equity, portfolio.free_margin, portfolio.open_risk,
                   portfolio.day_start_equity, portfolio.peak_equity)
        if any(x is None for x in numbers):
            return block((R.MISSING_DATA, "Preis- oder Kontodaten unvollstaendig"))
        if not all(is_finite_decimal(x) for x in numbers):
            return block((R.NON_FINITE_VALUE, "Ungueltige Zahl (NaN/Infinity/float)"))
        if not all(x > 0 for x in (setup.entry, setup.stop_loss, setup.take_profit,
                                   snap.bid, snap.ask)):
            return block((R.NON_POSITIVE_PRICE, "Preise muessen positiv sein"))
        if portfolio.equity <= 0:
            return block((R.NO_EQUITY, "Kein Eigenkapital"))
        if snap.symbol != setup.symbol or snap.market_type != setup.market_type:
            return block((R.SYMBOL_MISMATCH, "Marktdaten passen nicht zum Setup"))
        if snap.bid > snap.ask:
            return block((R.CROSSED_BOOK, "Bid ueber Ask"))
        if setup.market_type is MarketType.SPOT and setup.side is Side.SHORT:
            return block((R.SPOT_SHORT_NOT_ALLOWED, "Spot-Short ist verboten"))

        side, equity = setup.side, portfolio.equity
        funding = None  # type: Optional[Decimal]
        if setup.market_type is MarketType.FUTURES:
            if snap.funding_rate is None:
                return block((R.FUNDING_MISSING, "Funding-Rate fehlt"))
            if not is_finite_decimal(snap.funding_rate):
                return block((R.NON_FINITE_VALUE, "Funding-Rate ungueltig"))
            funding = snap.funding_rate

        # Erwarteter Einstiegskurs inkl. Slippage - identisch mit der Berechnung im Executor.
        fill = costs.entry_fill_price(side, snap)
        sign = side.sign
        if not sign * (fill - setup.stop_loss) > 0:
            return block((R.STOP_WRONG_SIDE, "Stop-Loss liegt nicht hinter dem Einstiegskurs"))
        if not sign * (setup.take_profit - fill) > 0:
            return block((R.TAKE_PROFIT_WRONG_SIDE, "Take-Profit liegt nicht vor dem Einstiegskurs"))

        # --- Notbremsen und Bestand (gesammelt) ---
        if portfolio.day_start_equity > 0 and (
            (portfolio.day_start_equity - equity) / portfolio.day_start_equity >= pol.max_daily_loss
        ):
            reasons.append(R.DAILY_LOSS_LIMIT)
            notes.append("Tagesverlust-Limit erreicht")
        if portfolio.peak_equity > 0 and (
            (portfolio.peak_equity - equity) / portfolio.peak_equity >= pol.max_drawdown
        ):
            reasons.append(R.DRAWDOWN_LIMIT)
            notes.append("Drawdown-Limit erreicht")
        if portfolio.open_count >= pol.max_open_positions:
            reasons.append(R.TOO_MANY_POSITIONS)
            notes.append("Maximale Zahl offener Positionen erreicht")
        if (setup.symbol, setup.market_type) in portfolio.open_keys:
            reasons.append(R.DUPLICATE_POSITION)
            notes.append("Es gibt bereits eine Position in diesem Markt")

        budget = self.risk_budget(portfolio)
        if budget <= 0:
            reasons.append(R.PORTFOLIO_RISK_TOO_HIGH)
            notes.append("Gesamtrisiko-Limit ausgeschoepft (kein Restbudget)")
        if reasons:
            return block()

        # --- Menge aus dem Risikobudget ---
        unit_loss = costs.worst_case_loss(side, D(1), fill, setup.stop_loss, funding)
        if unit_loss <= 0:
            return block((R.STOP_WRONG_SIDE, "Verlust je Einheit nicht positiv"))
        qty_by_risk = budget / unit_loss
        fee_unit = costs.fee(fill)

        # --- Hebel (nur Futures) ---
        liq_price = None  # type: Optional[Decimal]
        if setup.market_type is MarketType.SPOT:
            candidates = [D(1)]
        else:
            dist_stop = abs(fill - setup.stop_loss)
            candidates = []
            for i in range(1, int(self._max_leverage) + 1):
                lev = D(i)
                liq = liquidation_price(side, fill, lev, pol.maintenance_margin_rate)
                if abs(fill - liq) >= self._liq_factor * dist_stop:
                    candidates.append(lev)
            if not candidates:
                return block((R.LIQUIDATION_TOO_CLOSE,
                              "Selbst mit minimalem Hebel liegt die Liquidation zu nah am Stop"))

        def qty_by_margin(lev: Decimal) -> Decimal:
            return portfolio.free_margin * cfg.margin_utilization / (fill / lev + fee_unit)

        leverage = next((lv for lv in candidates if qty_by_margin(lv) >= qty_by_risk), None)
        margin_limited = leverage is None
        if leverage is None:
            leverage = candidates[-1]
        raw_qty = min(qty_by_risk, qty_by_margin(leverage))
        quantity = self._floor_qty(raw_qty) if raw_qty > 0 else D(0)
        if margin_limited:
            notes.append("Menge wegen verfuegbarer Margin verkleinert")

        if quantity <= 0 or quantity * fill < cfg.min_notional:
            code = R.INSUFFICIENT_MARGIN if margin_limited else R.QUANTITY_TOO_SMALL
            return block((code, "Positionsgroesse unter dem Minimum (Notional < %s)" % cfg.min_notional))

        # --- Endkontrolle mit den abgerundeten Werten ---
        planned = costs.worst_case_loss(side, quantity, fill, setup.stop_loss, funding)
        notional = quantity * fill
        margin = notional / leverage
        if planned > budget or margin + costs.fee(notional) > portfolio.free_margin:
            return block((R.TRADE_RISK_TOO_HIGH, "Interne Endkontrolle fehlgeschlagen"))
        if setup.market_type is MarketType.FUTURES:
            liq_price = liquidation_price(side, fill, leverage, pol.maintenance_margin_rate)

        notes.append("Risiko %.4f %% des Kapitals, Hebel %sx" % (planned / equity * 100, leverage))
        return SizingProposal(
            setup_id=sid,
            approve=True,
            quantity=quantity,
            leverage=leverage,
            reasons=tuple(notes),
            planned_loss=planned,
            risk_pct=planned / equity,
            notional=notional,
            margin=margin,
            liquidation_price=liq_price,
        )
