"""Schnittstelle fuer Handelsstrategien im Backtest - und ihre Anbindung als MOMO.

Eine Strategie sieht ausschliesslich `MarketView`: abgeschlossene Kerzen bis zum Entscheidungszeitpunkt
und die eigenen offenen Positionen. Sie liefert Vorschlaege; ob, wann, zu welchem Kurs und in welcher
Groesse gehandelt wird, entscheiden RIN, SORA, der Risikomanager und der Executor.

Strategien muessen deterministisch sein (gleiche Sicht -> gleiche Entscheidung): kein ungesetzter Zufall,
keine Systemuhr, kein Zustand ausserhalb des Objekts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, List, Mapping, Optional, Sequence, Tuple

from trading.agents.contracts import ExitSignal, MarketContext
from trading.agents.momo import MomoAgent
from trading.clock import Clock
from trading.domain.exchange_rules import ExchangeRules
from trading.domain.instrument import InstrumentId
from trading.domain.models import Candle, Horizon, Position, Side, TradeSetup


@dataclass(frozen=True)
class EntryProposal:
    instrument: InstrumentId
    side: Side
    stop_loss: Decimal
    take_profit: Decimal
    horizon: Horizon = Horizon.INTRADAY
    rationale: str = ""


@dataclass(frozen=True)
class ExitProposal:
    instrument: InstrumentId
    rationale: str = ""


@dataclass(frozen=True)
class StrategyDecision:
    entries: Tuple[EntryProposal, ...] = ()
    exits: Tuple[ExitProposal, ...] = ()


@dataclass(frozen=True)
class MarketView:
    """Alles, was eine Strategie zum Zeitpunkt `now` wissen darf."""

    now: datetime
    bars: Mapping[InstrumentId, Tuple[Candle, ...]]  # nur ABGESCHLOSSENE Kerzen, aelteste zuerst
    positions: Tuple[Position, ...]

    def closes(self, instrument: InstrumentId) -> Tuple[Decimal, ...]:
        return tuple(c.close for c in self.bars.get(instrument, ()))

    def last_close(self, instrument: InstrumentId) -> Optional[Decimal]:
        bars = self.bars.get(instrument, ())
        return bars[-1].close if bars else None

    def position(self, instrument: InstrumentId) -> Optional[Position]:
        return next((p for p in self.positions if p.instrument == instrument), None)


class Strategy(ABC):
    name: str = "strategy"

    @abstractmethod
    def on_bar(self, view: MarketView) -> StrategyDecision:
        """Wird nach jedem Zeitpunkt aufgerufen, an dem mindestens eine Kerze geschlossen hat."""


def on_ticks(rules: Optional[ExchangeRules], p: EntryProposal) -> Tuple[Decimal, Decimal]:
    """Stop und Ziel auf den Preisschritt der Boerse (AP-13), zu Ungunsten: Stop weiter weg, Ziel naeher dran.
    Ohne Regeln fuer das Instrument unveraendert (der Risikomanager lehnt dann ab, wenn ein Regelwerk gilt)."""
    found = rules.for_instrument(p.instrument) if rules is not None else None
    if found is None:
        return p.stop_loss, p.take_profit
    return found.protective_stop(p.side, p.stop_loss), found.conservative_target(p.side, p.take_profit)


class StrategyMomo(MomoAgent):
    """Macht aus einer Strategie den Agenten MOMO: Vorschlaege werden zu Setups und Ausstiegssignalen
    mit fortlaufenden, reproduzierbaren Kennungen und dem Zeitstempel der Entscheidung."""

    def __init__(self, strategy: Strategy, clock: Clock, view: Callable[[], MarketView],
                 rules: Optional[ExchangeRules] = None) -> None:
        super().__init__()
        self._strategy, self._clock, self._view, self._rules = strategy, clock, view, rules
        self._decided_at: Optional[datetime] = None
        self._decision = StrategyDecision()
        self._entry_no = self._exit_no = 0

    def _decide(self) -> StrategyDecision:
        now = self._clock.now()
        if self._decided_at != now:  # je Zeitpunkt wird die Strategie genau einmal befragt
            self._decision = self._strategy.on_bar(self._view())
            self._decided_at = now
        return self._decision

    def scan(self, contexts: Sequence[MarketContext]) -> List[TradeSetup]:
        reference = {c.snapshot.instrument: c.snapshot.last for c in contexts}
        setups: List[TradeSetup] = []
        for p in self._decide().entries:
            self._entry_no += 1
            stop, target = on_ticks(self._rules, p)
            setups.append(TradeSetup(
                setup_id="%s-%06d" % (self._strategy.name, self._entry_no),
                instrument=p.instrument,
                side=p.side,
                entry=reference.get(p.instrument, p.stop_loss),  # ohne aktuellen Kurs blockiert die Pipeline ohnehin
                stop_loss=stop,
                take_profit=target,
                horizon=p.horizon,
                strategy=self._strategy.name,
                created_at=self._clock.now(),
                rationale=p.rationale,
            ))
        return setups

    def exit_signals(
        self, contexts: Sequence[MarketContext], positions: Sequence[Position]
    ) -> List[ExitSignal]:
        signals: List[ExitSignal] = []
        for p in self._decide().exits:
            self._exit_no += 1
            signals.append(ExitSignal("%s-x%06d" % (self._strategy.name, self._exit_no), p.instrument,
                                      self._strategy.name, self._clock.now(), p.rationale))
        return signals
