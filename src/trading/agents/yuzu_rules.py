"""YUZU - regelbasierte Bewertung von Marktbedingungen und alternativen Signalen (AP-17). Keine Sicherheitsinstanz.

YUZU beantwortet: "Ist das Umfeld fuer dieses Setup gerade guenstig?" Grundlage: Marktphase und Volatilitaet,
Funding (Futures), Spread und Volumen, optional Stimmung (`MarketContext.extras["sentiment"]`, -1..1).

Harte Ablehnung (die KI-Zweitmeinung kann sie nie aufheben):
  * Volatilitaet extrem: ATR ueber `max_atr_pct` des Kurses je Kerze.
  * Funding stark gegen die Position: Long zahlt bzw. Short zahlt mehr als `max_funding` je Abrechnung.
  * Zu teuer/illiquide: Spread groesser als `max_spread_stop_share` des Stop-Abstands, oder das Volumen der letzten
    Kerzen unter `min_volume_ratio` des Durchschnitts.
Bewertung (Startwert 0,5): Funding ueberfuellt (ueber `crowded_funding`) -0,15, Funding zugunsten der Position +0,05;
hohe Volatilitaet bei Scalping -0,15; ruhiger Markt bei Scalping +0,05; Stimmung klar dagegen -0,15, dafuer +0,05.
Zu wenig Kerzen: kein Urteil (Zustimmung 0,5 mit Hinweis).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from trading.agents.ai_review import AIReview, ask, rule_confidence
from trading.agents.base import AgentName
from trading.agents.contracts import AgentOpinion, MarketContext
from trading.agents.yuzu import YuzuAgent
from trading.domain.instrument import MarketType
from trading.domain.models import Horizon, Side, TradeSetup
from trading.domain.reasons import RejectReason as R
from trading.analysis import indicators as ind
from trading.analysis.regime import DEFAULT_REGIME, RegimeConfig, Volatility, detect_regime

D = Decimal
INSTRUCTION = ("Du bist YUZU, Pruefer von Marktbedingungen (Volatilitaet, Funding, Liquiditaet, Stimmung). Bewerte, ob "
               'das Umfeld fuer das Setup guenstig ist. Antworte nur als JSON: {"approve": true|false, '
               '"confidence": 0..1, "reason": "kurz"}.')


@dataclass(frozen=True)
class YuzuConfig:
    regime: RegimeConfig = field(default_factory=lambda: DEFAULT_REGIME)
    max_atr_pct: Decimal = D("0.05")  # ueber 5 % Spanne je Kerze: zu wild
    crowded_funding: Decimal = D("0.0005")  # 0,05 % je Abrechnung gegen die Position: ueberfuellte Seite
    max_funding: Decimal = D("0.002")  # ab 0,2 % je Abrechnung gegen die Position: ablehnen
    max_spread_stop_share: Decimal = D("0.2")  # Spread hoechstens 20 % des Stop-Abstands
    volume_recent: int = 3
    volume_period: int = 20
    min_volume_ratio: Decimal = D("0.3")
    min_score: Decimal = D("0.35")

    def __post_init__(self) -> None:
        for name in ("volume_recent", "volume_period"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError("%s muss eine ganze Zahl >= 1 sein." % name)
        for name in ("max_atr_pct", "crowded_funding", "max_funding", "max_spread_stop_share", "min_volume_ratio",
                     "min_score"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("%s muss eine nicht-negative Decimal-Zahl sein." % name)
        if self.crowded_funding > self.max_funding:
            raise ValueError("crowded_funding darf nicht ueber max_funding liegen.")


def _sentiment(context: MarketContext) -> Optional[Decimal]:
    raw = context.extras.get("sentiment") if context.extras else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str, Decimal)):
        return None
    try:
        value = Decimal(str(raw))
    except ArithmeticError:
        return None
    return value if value.is_finite() and D(-1) <= value <= D(1) else None


class RuleBasedYuzu(YuzuAgent):
    def __init__(self, config: Optional[YuzuConfig] = None, review: Optional[AIReview] = None) -> None:
        super().__init__()
        self._config = config or YuzuConfig()
        self._review = review

    def evaluate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        cfg = self._config
        candles = context.candles
        regime = detect_regime(candles, cfg.regime)
        recent = ind.sma([c.volume for c in candles], cfg.volume_recent)
        normal = ind.sma([c.volume for c in candles], cfg.volume_period)
        if regime is None or recent is None or normal is None:
            return AgentOpinion(AgentName.YUZU, setup.setup_id, True, D("0.5"),
                                ("YUZU: zu wenig Kerzen fuer ein Urteil (%d) - kein Einwand" % len(candles),))
        sign = 1 if setup.side is Side.LONG else -1
        snap = context.snapshot
        notes: List[str] = []
        hard: List[R] = []
        if regime.atr_pct > cfg.max_atr_pct:
            hard.append(R.VOLATILITY_TOO_HIGH)
            notes.append("Volatilitaet extrem (%.1f %% je Kerze)" % (regime.atr_pct * 100))
        stop_distance = abs(setup.entry - setup.stop_loss)
        spread = snap.ask - snap.bid
        if stop_distance > 0 and spread > cfg.max_spread_stop_share * stop_distance:
            hard.append(R.LIQUIDITY_TOO_LOW)
            notes.append("Spread %s ueber %s des Stop-Abstands" % (spread, cfg.max_spread_stop_share))
        if normal > 0 and recent / normal < cfg.min_volume_ratio:
            if R.LIQUIDITY_TOO_LOW not in hard:
                hard.append(R.LIQUIDITY_TOO_LOW)
            notes.append("Volumen eingebrochen (%.0f %% des Durchschnitts)" % (recent / normal * 100))
        paid = None  # Funding, das die Position je Abrechnung zahlt (positiv) oder erhaelt (negativ)
        if setup.instrument.market_type is MarketType.FUTURES and snap.funding_rate is not None:
            paid = snap.funding_rate * sign
            if paid > cfg.max_funding:
                hard.append(R.FUNDING_UNFAVORABLE)
                notes.append("Funding %.3f %% je Abrechnung gegen die Position" % (paid * 100))

        score = D("0.5")
        if paid is not None and paid > cfg.crowded_funding:
            score -= D("0.15")
            notes.append("Seite ueberfuellt (Funding %.3f %%)" % (paid * 100))
        elif paid is not None and paid < 0:
            score += D("0.05")
        if setup.horizon is Horizon.SCALP:
            if regime.volatility is Volatility.HIGH:
                score -= D("0.15")
                notes.append("unruhiger Markt fuer Scalping")
            elif regime.volatility is Volatility.LOW:
                score += D("0.05")
        mood = _sentiment(context)
        if mood is not None:
            if mood * sign <= D("-0.5"):
                score -= D("0.15")
                notes.append("Stimmung klar dagegen (%s)" % mood)
            elif mood * sign >= D("0.5"):
                score += D("0.05")
        score = max(D(0), min(D(1), score))
        if hard:
            return AgentOpinion(AgentName.YUZU, setup.setup_id, False, D(0), tuple("YUZU: " + n for n in notes),
                                tuple(hard))
        approve = score >= cfg.min_score
        payload: Dict[str, object] = dict(regime.as_payload(), side=setup.side.value, horizon=setup.horizon.value,
                                          funding=str(paid) if paid is not None else "",
                                          sentiment=str(mood) if mood is not None else "", score=str(score))
        decided, hint = ask(self._review, AgentName.YUZU.value, "yuzu.conditions_review", INSTRUCTION, payload, setup,
                            approve, rule_confidence(score, cfg.min_score))
        if hint:
            notes.append(hint)
        text = tuple("YUZU: " + n for n in notes) or ("YUZU: Umfeld ohne Einwand",)
        if decided:
            return AgentOpinion(AgentName.YUZU, setup.setup_id, True, score, text)
        return AgentOpinion(AgentName.YUZU, setup.setup_id, False, score, text + ("YUZU: Bewertung %s zu schwach" % score,),
                            (R.CONDITIONS_UNFAVORABLE,))


__all__ = ["RuleBasedYuzu", "YuzuConfig"]
