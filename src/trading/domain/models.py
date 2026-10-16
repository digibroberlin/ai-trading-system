"""Zentrale Datentypen. Alle Geldbetraege und Preise sind Decimal (nie float)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import FrozenSet, Optional, Tuple

from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.domain.reasons import RejectReason

__all__ = [
    "Candle", "CloseResult", "ClosedTrade", "Exchange", "ExecutionResult", "ExitReason",
    "FundingEvent", "Horizon", "InstrumentId", "Mark", "MarketSnapshot", "MarketType",
    "OrderIntent", "OrderStatus", "PortfolioState", "Position", "RiskDecision", "Side", "TradeSetup",
]


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


class Horizon(str, Enum):
    SCALP = "scalp"
    INTRADAY = "intraday"
    SWING = "swing"


class ExitReason(str, Enum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    LIQUIDATION = "liquidation"
    SIGNAL = "signal"  # gueltiges Ausstiegssignal einer Strategie
    MANUAL = "manual"  # Eingriff von aussen (z. B. Not-Aus)


class OrderStatus(str, Enum):
    FILLED = "filled"
    REJECTED = "rejected"


class _HasInstrument:
    """Bequemer Lesezugriff auf die Bestandteile des Instrument-Schluessels."""

    instrument: InstrumentId

    @property
    def symbol(self) -> str:
        return self.instrument.symbol

    @property
    def market_type(self) -> MarketType:
        return self.instrument.market_type

    @property
    def exchange(self) -> Exchange:
        return self.instrument.exchange


# ----------------------------------------------------------------------------
# Marktdaten
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Candle(_HasInstrument):
    instrument: InstrumentId
    open_time: datetime
    interval_s: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @property
    def close_time(self) -> datetime:
        return self.open_time + timedelta(seconds=self.interval_s)


@dataclass(frozen=True)
class MarketSnapshot(_HasInstrument):
    """Momentaufnahme eines Marktes. `timestamp` ist der Zeitstempel der Boerse."""

    instrument: InstrumentId
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    last: Decimal
    candle: Optional[Candle] = None
    funding_rate: Optional[Decimal] = None  # Pflicht bei Futures

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> Decimal:
        mid = self.mid
        if mid <= 0:
            return Decimal("Infinity")
        return (self.ask - self.bid) / mid * 10000


@dataclass(frozen=True)
class FundingEvent(_HasInstrument):
    """Eine Funding-Abrechnung eines Futures-Instruments (Satz und Kurs kommen von der Boerse).

    Funding wird NIE aus einem fest verdrahteten Zeitplan abgeleitet: Jede Boerse rechnet zu eigenen
    Zeiten ab (Bitget meist alle 8 h, Coinbase stuendlich). Fehlt der Datensatz, darf das nicht
    stillschweigend als "kostenlos" gelten - Datensaetze ohne Funding werden abgelehnt."""

    instrument: InstrumentId
    time: datetime
    rate: Decimal  # positiv: Long zahlt an Short
    mark_price: Decimal


@dataclass(frozen=True)
class Mark:
    """Letzter Bewertungskurs eines Instruments samt Zeitpunkt (fuer die Aktualitaetspruefung)."""

    price: Decimal
    time: datetime


# ----------------------------------------------------------------------------
# Setups, Orders, Positionen
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class TradeSetup(_HasInstrument):
    """Ein von MOMO vorgeschlagener Trade. Stop-Loss und Take-Profit sind Pflicht."""

    setup_id: str
    instrument: InstrumentId
    side: Side
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    horizon: Horizon
    strategy: str
    created_at: datetime
    rationale: str = ""


@dataclass(frozen=True)
class OrderIntent:
    """Auftrag an den Executor. `client_order_id` ist der Schluessel gegen Doppel-Orders."""

    client_order_id: str
    setup: TradeSetup
    quantity: Decimal
    leverage: Decimal = Decimal(1)


@dataclass(frozen=True)
class Position(_HasInstrument):
    """Offene Position. Unveraenderlich: Aenderungen (Funding) erzeugen ein neues Objekt."""

    position_id: str
    setup_id: str
    strategy: str
    instrument: InstrumentId
    side: Side
    quantity: Decimal
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    leverage: Decimal
    opened_at: datetime
    margin: Decimal
    risk_at_open: Decimal  # geplanter Hoechstverlust inkl. ALLER Kosten (auch Einstiegsgebuehr)
    entry_fee: Decimal
    liquidation_price: Optional[Decimal]
    last_funding_ts: datetime
    funding_paid: Decimal = Decimal(0)  # positiv = bezahlt, negativ = erhalten

    @property
    def remaining_risk(self) -> Decimal:
        """Noch moeglicher geplanter Verlust AB JETZT: Die Einstiegsgebuehr ist bereits bezahlt und
        steckt schon im Eigenkapital - sie zaehlt daher nicht ein zweites Mal."""
        return self.risk_at_open - self.entry_fee


@dataclass(frozen=True)
class ClosedTrade(_HasInstrument):
    position_id: str
    setup_id: str
    strategy: str
    instrument: InstrumentId
    side: Side
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    opened_at: datetime
    closed_at: datetime
    exit_reason: ExitReason
    gross_pnl: Decimal
    fees: Decimal  # Einstiegs- plus Ausstiegsgebuehr
    funding: Decimal  # positiv = bezahlt
    net_pnl: Decimal
    risk_at_open: Decimal

    @property
    def r_multiple(self) -> Decimal:
        if self.risk_at_open <= 0:
            return Decimal(0)
        return self.net_pnl / self.risk_at_open


@dataclass(frozen=True)
class PortfolioState:
    """Kontostand, wie ihn Risikomanager und SORA brauchen. Alles bezogen auf JETZT."""

    balance: Decimal  # realisiertes Guthaben (nach allen bereits bezahlten Kosten)
    unrealized_pnl: Decimal  # Kursgewinne/-verluste offener Positionen zum Bewertungskurs
    equity: Decimal  # balance + unrealized_pnl = aktuelle Netto-Equity
    free_margin: Decimal
    day_start_equity: Decimal
    peak_equity: Decimal
    open_risk: Decimal  # Summe des noch moeglichen geplanten Verlusts aller offenen Positionen
    open_count: int
    open_keys: FrozenSet[InstrumentId]
    gross_exposure: Decimal  # Summe |Menge x Bewertungskurs| aller offenen Positionen
    stress_loss: Decimal  # Equity-Verlust bei schlagartigem Kursschock gegen jede Position
    valuation_complete: bool  # False, wenn ein Bewertungskurs fehlt oder veraltet ist


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: Tuple[RejectReason, ...] = ()
    worst_case_loss: Decimal = Decimal(0)
    entry_fee: Decimal = Decimal(0)
    risk_pct: Decimal = Decimal(0)  # Hoechstverlust dieses Trades / aktuelle Equity
    total_risk_pct_after: Decimal = Decimal(0)  # alle offenen Risiken nach dem Trade / Equity nach dem Trade
    gross_exposure_pct_after: Decimal = Decimal(0)
    stress_loss_pct_after: Decimal = Decimal(0)
    margin_required: Decimal = Decimal(0)
    liquidation_price: Optional[Decimal] = None


@dataclass(frozen=True)
class ExecutionResult:
    """Ergebnis einer Order. `replayed=True`: Diese Order-ID war schon bearbeitet; geliefert wird das URSPRUENGLICHE
    Ergebnis (Status, Gruende, Position wie bei der Eroeffnung) - es wurde dabei nichts Neues gehandelt. Die
    Risikobewertung (`risk`) steht dann nur im Ereignisprotokoll der ersten Bearbeitung."""

    status: OrderStatus
    client_order_id: str
    reasons: Tuple[RejectReason, ...] = ()
    position: Optional[Position] = None
    risk: Optional[RiskDecision] = None
    replayed: bool = False


@dataclass(frozen=True)
class CloseResult:
    """Ergebnis einer angeforderten Schliessung (Ausstiegssignal oder Eingriff)."""

    closed: bool
    request_id: str
    reasons: Tuple[RejectReason, ...] = ()
    trade: Optional[ClosedTrade] = None
