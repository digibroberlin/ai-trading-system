"""Daten, die zwischen den Agenten ausgetauscht werden."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Optional, Tuple

from trading.agents.base import AgentName
from trading.domain.instrument import InstrumentId
from trading.domain.models import Candle, MarketSnapshot
from trading.domain.reasons import RejectReason


@dataclass(frozen=True)
class MarketContext:
    """Alles, was ein Agent ueber einen Markt sehen darf (nur lesend).

    `candles` enthaelt ausschliesslich ABGESCHLOSSENE Kerzen bis zum Entscheidungszeitpunkt."""

    snapshot: MarketSnapshot
    candles: Tuple[Candle, ...] = ()
    # Alternative Daten fuer YUZU (Funding, Open Interest, Stimmung ...), frei erweiterbar.
    extras: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentOpinion:
    """Einschaetzung von RIN, KIBO oder YUZU zu einem Setup."""

    agent: AgentName
    setup_id: str
    approve: bool
    score: Decimal  # Zuversicht 0 (keine) bis 1 (sehr hoch)
    reasons: Tuple[str, ...] = ()  # Klartext fuer Menschen
    reject_reasons: Tuple[RejectReason, ...] = ()  # maschinenlesbar (nur bei approve=False)

    def __post_init__(self) -> None:
        if not (Decimal(0) <= self.score <= Decimal(1)):
            raise ValueError("score muss zwischen 0 und 1 liegen.")


@dataclass(frozen=True)
class SizingProposal:
    """Positionsgroesse, die SORA vorschlaegt. Der Risikomanager prueft sie trotzdem erneut."""

    setup_id: str
    approve: bool
    quantity: Decimal
    leverage: Decimal
    reasons: Tuple[str, ...] = ()  # Klartext fuer Menschen
    reject_reasons: Tuple[RejectReason, ...] = ()  # maschinenlesbar (nur bei approve=False)
    planned_loss: Decimal = Decimal(0)  # Hoechstverlust inkl. Gebuehren/Slippage/Funding
    risk_pct: Decimal = Decimal(0)  # Anteil am Eigenkapital
    notional: Decimal = Decimal(0)
    margin: Decimal = Decimal(0)
    liquidation_price: Optional[Decimal] = None


@dataclass(frozen=True)
class ExitSignal:
    """Ausstiegssignal fuer eine offene Position (von MOMO bzw. einer Strategie)."""

    signal_id: str
    instrument: InstrumentId
    strategy: str
    created_at: datetime
    rationale: str = ""
