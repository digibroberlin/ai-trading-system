"""KIBO - regelbasierte Bewertung technischer Signale (AP-17). Keine Sicherheitsinstanz.

KIBO beantwortet: "Passt das Setup technisch zum Markt?" Grundlage sind abgeschlossene Kerzen (`MarketContext.candles`)
und die Marktphase (`strategies.regime`).

Harte Ablehnung (die KI-Zweitmeinung kann sie nie aufheben):
  * Stop im Rauschen: Stop-Abstand kleiner als `min_stop_atr` x ATR - der Stop wuerde vom normalen Schwanken getroffen.
  * Gegen einen klaren Trend (abschaltbar): Long im Abwaertstrend / Short im Aufwaertstrend.
Bewertung (Startwert 0,5): Trend in Setup-Richtung +0,2; Momentum (Veraenderungsrate) dafuer +0,1 / dagegen -0,1;
RSI ueberkauft bei Long bzw. ueberverkauft bei Short -0,15; Volumen der letzten Kerze >= 1,2x Durchschnitt +0,05,
< 0,5x -0,1. Zustimmung ab `min_score`.
Zu wenig Kerzen: KEIN Urteil (Zustimmung mit 0,5 und Hinweis) - KIBO haelt den Ablauf nicht auf, die Sicherheit haengt an
RIN, SORA und dem Risikomanager. Optional: KI-Zweitmeinung ueber den Orchestrator, nur bei unsicherem Regel-Ergebnis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from trading.agents.ai_review import AIReview, ask, rule_confidence
from trading.agents.base import AgentName
from trading.agents.contracts import AgentOpinion, MarketContext
from trading.agents.kibo import KiboAgent
from trading.domain.models import Side, TradeSetup
from trading.domain.reasons import RejectReason as R
from trading.analysis import indicators as ind
from trading.analysis.regime import DEFAULT_REGIME, RegimeConfig, Trend, detect_regime

D = Decimal
INSTRUCTION = ("Du bist KIBO, Pruefer technischer Handelssignale. Bewerte, ob das Setup technisch zum Markt passt. "
               'Antworte nur als JSON: {"approve": true|false, "confidence": 0..1, "reason": "kurz"}.')


@dataclass(frozen=True)
class KiboConfig:
    regime: RegimeConfig = field(default_factory=lambda: DEFAULT_REGIME)
    block_counter_trend: bool = True
    min_stop_atr: Decimal = D("0.5")
    rsi_period: int = 14
    overbought: Decimal = D("75")
    oversold: Decimal = D("25")
    momentum_period: int = 10
    volume_period: int = 20
    min_score: Decimal = D("0.35")

    def __post_init__(self) -> None:
        for name in ("rsi_period", "momentum_period", "volume_period"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError("%s muss eine ganze Zahl >= 1 sein." % name)
        if not D(0) < self.oversold < self.overbought < D(100):
            raise ValueError("Es muss 0 < oversold < overbought < 100 gelten.")
        for name in ("min_stop_atr", "min_score"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("%s muss eine nicht-negative Decimal-Zahl sein." % name)


class RuleBasedKibo(KiboAgent):
    def __init__(self, config: Optional[KiboConfig] = None, review: Optional[AIReview] = None) -> None:
        super().__init__()
        self._config = config or KiboConfig()
        self._review = review

    def evaluate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        cfg = self._config
        candles = context.candles
        regime = detect_regime(candles, cfg.regime)
        closes = [c.close for c in candles]
        atr = ind.atr(candles, cfg.regime.atr_period)
        strength = ind.rsi(closes, cfg.rsi_period)
        rate = ind.roc(closes, cfg.momentum_period)
        volume = ind.sma([c.volume for c in candles], cfg.volume_period)
        if regime is None or atr is None or strength is None or rate is None or volume is None:
            return AgentOpinion(AgentName.KIBO, setup.setup_id, True, D("0.5"),
                                ("KIBO: zu wenig Kerzen fuer ein Urteil (%d) - kein Einwand" % len(candles),))
        sign = 1 if setup.side is Side.LONG else -1
        notes: List[str] = []
        hard: List[R] = []
        if abs(setup.entry - setup.stop_loss) < cfg.min_stop_atr * atr:
            hard.append(R.STOP_INSIDE_NOISE)
            notes.append("Stop-Abstand %s unter %s x ATR %s (im normalen Schwanken)"
                         % (abs(setup.entry - setup.stop_loss), cfg.min_stop_atr, round(atr, 8)))
        against = (regime.trend is Trend.DOWN and sign > 0) or (regime.trend is Trend.UP and sign < 0)
        if against and cfg.block_counter_trend:
            hard.append(R.AGAINST_TREND)
            notes.append("gegen den klaren %s-Trend (Effizienz %.2f)" % (regime.trend.value, regime.efficiency))

        score = D("0.5")
        aligned = (regime.trend is Trend.UP and sign > 0) or (regime.trend is Trend.DOWN and sign < 0)
        if aligned:
            score += D("0.2")
            notes.append("Trend in Setup-Richtung")
        if rate * sign > 0:
            score += D("0.1")
        elif rate * sign < 0:
            score -= D("0.1")
            notes.append("Momentum dagegen (%+.1f %%)" % (rate * 100))
        if (sign > 0 and strength > cfg.overbought) or (sign < 0 and strength < cfg.oversold):
            score -= D("0.15")
            notes.append("RSI %.0f gegen die Richtung ausgereizt" % strength)
        if volume > 0:
            ratio = candles[-1].volume / volume
            if ratio >= D("1.2"):
                score += D("0.05")
            elif ratio < D("0.5"):
                score -= D("0.1")
                notes.append("schwaches Volumen (%.0f %% des Durchschnitts)" % (ratio * 100))
        score = max(D(0), min(D(1), score))
        if hard:
            return AgentOpinion(AgentName.KIBO, setup.setup_id, False, D(0), tuple("KIBO: " + n for n in notes),
                                tuple(hard))
        approve = score >= cfg.min_score
        payload: Dict[str, object] = dict(regime.as_payload(), side=setup.side.value, horizon=setup.horizon.value,
                                          strategy=setup.strategy, rsi=str(round(strength, 1)),
                                          momentum=str(round(rate, 4)), score=str(score))
        decided, hint = ask(self._review, AgentName.KIBO.value, "kibo.setup_review", INSTRUCTION, payload, setup,
                            approve, rule_confidence(score, cfg.min_score))
        if hint:
            notes.append(hint)
        text = tuple("KIBO: " + n for n in notes) or ("KIBO: technisch ohne Einwand",)
        if decided:
            return AgentOpinion(AgentName.KIBO, setup.setup_id, True, score, text)
        return AgentOpinion(AgentName.KIBO, setup.setup_id, False, score, text + ("KIBO: Bewertung %s zu schwach" % score,),
                            (R.TECHNICALS_WEAK,))


__all__ = ["KiboConfig", "RuleBasedKibo"]
