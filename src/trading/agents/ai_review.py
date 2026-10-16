"""Optionale KI-Zweitmeinung fuer KIBO und YUZU - immer ueber den Kosten-Orchestrator (D-016).

Ablauf: Die Regeln entscheiden zuerst. Nur wenn sie UNSICHER sind (keine harte Ablehnung) und eine Pruefung konfiguriert
ist, wird der Orchestrator gefragt. Er entscheidet selbst ueber Cache, Aktivierung, Wertschranke und Budget - bei Budget 0
oder Nutzen 0 bleibt es bei den Regeln. Eine KI-Antwort zaehlt nur, wenn sie ein eindeutiges `approve` (wahr/falsch)
enthaelt; sonst gelten die Regeln. Harte Ablehnungen der Regeln kann die KI nie aufheben.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Optional, Tuple

from trading.ai.activation import TriggerEvent
from trading.ai.orchestrator import AIOrchestrator, AnalysisRequest, RuleAnswer, Source, Tier
from trading.domain.models import TradeSetup

D = Decimal


@dataclass(frozen=True)
class AIReview:
    orchestrator: AIOrchestrator
    value_usd: Decimal = D(0)  # geschaetzter Nutzen einer besseren Entscheidung; 0 = nie ein bezahlter Aufruf
    max_tier: Tier = Tier.CHEAP

    def __post_init__(self) -> None:
        if not isinstance(self.value_usd, Decimal) or not self.value_usd.is_finite() or self.value_usd < 0:
            raise ValueError("value_usd muss eine nicht-negative Decimal-Zahl sein.")


def rule_confidence(score: Decimal, threshold: Decimal) -> Decimal:
    """Wie sicher ist die Regel-Entscheidung? 0,5 an der Schwelle, bis 0,95 mit wachsendem Abstand."""
    return min(D("0.95"), D("0.5") + abs(score - threshold))


def ask(
    review: Optional[AIReview], agent: str, kind: str, instruction: str, payload: Mapping[str, object],
    setup: TradeSetup, approve: bool, confidence: Decimal,
) -> Tuple[bool, Optional[str]]:
    """(Entscheidung, Hinweis fuer Menschen oder None, wenn die Regeln gelten)."""
    if review is None:
        return approve, None
    analysis = review.orchestrator.analyze(
        AnalysisRequest(agent, kind, instruction, payload, review.value_usd, TriggerEvent.SETUP_CANDIDATE,
                        setup.instrument.key, setup.setup_id, review.max_tier),
        RuleAnswer({"approve": approve}, confidence),
    )
    answer = analysis.answer
    if analysis.source in (Source.AI, Source.CACHE) and answer is not None and isinstance(answer.get("approve"), bool):
        decided = bool(answer["approve"])
        return decided, "KI-Zweitmeinung (%s, Sicherheit %.2f): %s" % (
            analysis.model or analysis.source.value, analysis.confidence, "zustimmen" if decided else "ablehnen")
    return approve, None


__all__ = ["AIReview", "ask", "rule_confidence"]
