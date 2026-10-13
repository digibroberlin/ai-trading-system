"""Einfache Referenz-Strategie - NUR zum Pruefen und Vorfuehren der Technik.

Sie ist weder optimiert noch eine Handelsempfehlung. Ergebnisse auf synthetischen Daten sagen nichts
ueber echte Maerkte aus.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Sequence

from trading.backtest.strategy import (
    EntryProposal,
    ExitProposal,
    MarketView,
    Strategy,
    StrategyDecision,
)
from trading.domain.instrument import MarketType
from trading.domain.models import Side


def sma(values: Sequence[Decimal], n: int) -> Decimal:
    return sum(values[-n:], Decimal(0)) / n


class SmaCrossStrategy(Strategy):
    """Gleitende Durchschnitte: Kreuzt der schnelle den langsamen nach oben -> Long (bei Futures nach
    unten -> Short). Dreht das Signal, wird die Position geschlossen. Stop und Ziel als fester Abstand."""

    def __init__(
        self,
        fast: int = 10,
        slow: int = 30,
        stop_pct: Decimal = Decimal("0.02"),
        target_pct: Decimal = Decimal("0.05"),
        allow_short: bool = True,
        name: str = "",
    ) -> None:
        if not 0 < fast < slow:
            raise ValueError("Es muss 0 < fast < slow gelten.")
        self.fast, self.slow, self.stop_pct, self.target_pct = fast, slow, stop_pct, target_pct
        self.allow_short = allow_short
        self.name = name or "sma%d-%d" % (fast, slow)

    def on_bar(self, view: MarketView) -> StrategyDecision:
        entries: List[EntryProposal] = []
        exits: List[ExitProposal] = []
        for instrument in sorted(view.bars, key=lambda i: i.key):
            closes = view.closes(instrument)
            if len(closes) < self.slow + 1:
                continue
            now_up = sma(closes, self.fast) > sma(closes, self.slow)
            was_up = sma(closes[:-1], self.fast) > sma(closes[:-1], self.slow)
            position = view.position(instrument)
            if position is not None:
                if (position.side is Side.LONG) != now_up:
                    exits.append(ExitProposal(instrument, "Signal gedreht"))
                continue
            if now_up == was_up:
                continue  # kein frisches Kreuzen
            price = closes[-1]
            if now_up:
                entries.append(EntryProposal(instrument, Side.LONG, price * (1 - self.stop_pct),
                                             price * (1 + self.target_pct), rationale="SMA-Kreuz aufwaerts"))
            elif self.allow_short and instrument.market_type is MarketType.FUTURES:
                entries.append(EntryProposal(instrument, Side.SHORT, price * (1 + self.stop_pct),
                                             price * (1 - self.target_pct), rationale="SMA-Kreuz abwaerts"))
        return StrategyDecision(tuple(entries), tuple(exits))
