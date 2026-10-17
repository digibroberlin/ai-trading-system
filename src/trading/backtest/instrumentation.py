"""Messung im Backtest (AP-20): Entscheidungslatenz je Agent und Schatten-Urteile von KIBO/YUZU.

* `Stopwatch` + `Timed*`: messen die REINE Rechenzeit jedes Agenten-Aufrufs (time.perf_counter) - im Backtest also die
  Latenz der Regeln, ohne Netz. Die Zeiten sind nicht deterministisch und gehen deshalb NIE in Ergebnis-Hashes ein.
* `ShadowKibo` / `ShadowYuzu`: rechnen das echte Urteil, stimmen aber IMMER zu und merken sich, was sie entschieden
  haetten. So laesst sich je ausgefuehrtem Trade zeigen, ob ein Filter ihn verhindert haette - der Nutzen eines Filters
  wird je Trade gepaart messbar (viel mehr Stichproben als je Zeitfenster). Ein Schatten ist KEIN Filter: Laeufe mit
  Schatten zaehlen wie Laeufe ohne KIBO/YUZU und werden so ausgewiesen.
Beides aendert keine Sicherheitsregel: RIN, SORA, Risikomanager und Executor laufen unveraendert.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from trading.agents.base import AgentName
from trading.agents.contracts import AgentOpinion, ExitSignal, MarketContext, SizingProposal
from trading.agents.kibo import KiboAgent
from trading.agents.momo import MomoAgent
from trading.agents.rin import RinAgent
from trading.agents.sora import SoraAgent
from trading.agents.yuzu import YuzuAgent
from trading.domain.models import PortfolioState, Position, TradeSetup


@dataclass
class Stopwatch:
    """Summen und Einzelwerte (Mikrosekunden) je Agent."""

    samples: Dict[str, List[float]] = field(default_factory=dict)

    def add(self, agent: str, seconds: float) -> None:
        self.samples.setdefault(agent, []).append(seconds * 1e6)

    def summary(self) -> Dict[str, Dict[str, float]]:
        """Je Agent: Aufrufe, Mittel, Median, 95-%-Wert und Maximum in Mikrosekunden."""
        out: Dict[str, Dict[str, float]] = {}
        for agent, values in sorted(self.samples.items()):
            ordered = sorted(values)
            n = len(ordered)
            out[agent] = {"calls": float(n), "mean_us": sum(ordered) / n, "median_us": ordered[n // 2],
                          "p95_us": ordered[min(n - 1, int(n * 0.95))], "max_us": ordered[-1]}
        return out


class TimedMomo(MomoAgent):
    def __init__(self, inner: MomoAgent, watch: Stopwatch) -> None:
        super().__init__()
        self._inner, self._watch = inner, watch

    def scan(self, contexts: Sequence[MarketContext]) -> List[TradeSetup]:
        start = time.perf_counter()
        try:
            return self._inner.scan(contexts)
        finally:
            self._watch.add("MOMO", time.perf_counter() - start)

    def exit_signals(self, contexts: Sequence[MarketContext], positions: Sequence[Position]) -> List[ExitSignal]:
        start = time.perf_counter()
        try:
            return self._inner.exit_signals(contexts, positions)
        finally:
            self._watch.add("MOMO_EXIT", time.perf_counter() - start)


class TimedRin(RinAgent):
    def __init__(self, inner: RinAgent, watch: Stopwatch) -> None:
        super().__init__()
        self._inner, self._watch = inner, watch

    def validate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        start = time.perf_counter()
        try:
            return self._inner.validate(setup, context)
        finally:
            self._watch.add("RIN", time.perf_counter() - start)


class TimedSora(SoraAgent):
    def __init__(self, inner: SoraAgent, watch: Stopwatch) -> None:
        super().__init__()
        self._inner, self._watch = inner, watch

    def size(self, setup: TradeSetup, context: MarketContext, portfolio: PortfolioState) -> SizingProposal:
        start = time.perf_counter()
        try:
            return self._inner.size(setup, context, portfolio)
        finally:
            self._watch.add("SORA", time.perf_counter() - start)


class TimedKibo(KiboAgent):
    def __init__(self, inner: KiboAgent, watch: Stopwatch) -> None:
        super().__init__()
        self._inner, self._watch = inner, watch

    def evaluate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        start = time.perf_counter()
        try:
            return self._inner.evaluate(setup, context)
        finally:
            self._watch.add("KIBO", time.perf_counter() - start)


class TimedYuzu(YuzuAgent):
    def __init__(self, inner: YuzuAgent, watch: Stopwatch) -> None:
        super().__init__()
        self._inner, self._watch = inner, watch

    def evaluate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        start = time.perf_counter()
        try:
            return self._inner.evaluate(setup, context)
        finally:
            self._watch.add("YUZU", time.perf_counter() - start)


@dataclass(frozen=True)
class ShadowVerdict:
    approve: bool
    score: Decimal
    reasons: Tuple[str, ...]  # Klartext-Gruende des echten Urteils
    codes: Tuple[str, ...] = ()  # maschinenlesbare Ablehnungsgruende des echten Urteils


def _shadow(name: AgentName, setup: TradeSetup, real: AgentOpinion, verdicts: Dict[str, ShadowVerdict]) -> AgentOpinion:
    verdicts[setup.setup_id] = ShadowVerdict(real.approve, real.score, tuple(real.reasons),
                                             tuple(r.value for r in real.reject_reasons))
    note = "Schatten: %s haette %s (%s)" % (name.value, "zugestimmt" if real.approve else "abgelehnt",
                                             "; ".join(real.reasons) or "-")
    return AgentOpinion(name, setup.setup_id, True, real.score, (note,))


class ShadowKibo(KiboAgent):
    """Rechnet KIBO, stimmt aber immer zu; `verdicts[setup_id]` = echtes Urteil."""

    def __init__(self, inner: KiboAgent) -> None:
        super().__init__()
        self._inner = inner
        self.verdicts: Dict[str, ShadowVerdict] = {}

    def evaluate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        return _shadow(AgentName.KIBO, setup, self._inner.evaluate(setup, context), self.verdicts)


class ShadowYuzu(YuzuAgent):
    """Rechnet YUZU, stimmt aber immer zu; `verdicts[setup_id]` = echtes Urteil."""

    def __init__(self, inner: YuzuAgent) -> None:
        super().__init__()
        self._inner = inner
        self.verdicts: Dict[str, ShadowVerdict] = {}

    def evaluate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        return _shadow(AgentName.YUZU, setup, self._inner.evaluate(setup, context), self.verdicts)


def verdict_of(agent: object, setup_id: str) -> Optional[ShadowVerdict]:
    verdicts = getattr(agent, "verdicts", None)
    return None if verdicts is None else verdicts.get(setup_id)


__all__ = ["ShadowKibo", "ShadowVerdict", "ShadowYuzu", "Stopwatch", "TimedKibo", "TimedMomo", "TimedRin",
           "TimedSora", "TimedYuzu", "verdict_of"]
