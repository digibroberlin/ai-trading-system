"""Zentrale Datentypen. Alle Geldbetraege und Preise sind Decimal (nie float)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import FrozenSet, Optional, Tuple

from trading.domain.reasons import RejectReason


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


class MarketType(str, Enum):
    SPOT = "spot"
    FUTURES = "futures"


class Horizon(str, Enum):
    SCALP = "scalp"
    INTRADAY = "intraday"
    SWING = "swing"


class ExitReason(str, Enum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    LIQUIDATION = "liquidation"


class OrderStatus(str, Enum):
    FILLED = "filled"
    REJECTED = "rejected"


# ----------------------------------------------------------------------------
# Marktdaten
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Candle:
    symbol: str
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
class MarketSnapshot:
    """Momentaufnahme eines Marktes. `timestamp` ist der Zeitstempel der Boerse."""

    symbol: str
    market_type: MarketType
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


# ----------------------------------------------------------------------------
# Setups, Orders, Positionen
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class TradeSetup:
    """Ein von MOMO vorgeschlagener Trade. Stop-Loss und Take-Profit sind Pflicht."""

    setup_id: str
    symbol: str
    market_type: MarketType
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


@dataclass
class Position:
    position_id: str
    setup_id: str
    strategy: str
    symbol: str
    market_type: MarketType
    side: Side
    quantity: Decimal
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    leverage: Decimal
    opened_at: datetime
    margin: Decimal
    risk_at_open: Decimal  # geplanter Hoechstverlust inkl. Kosten
    entry_fee: Decimal
    liquidation_price: Optional[Decimal]
    last_funding_ts: datetime
    funding_paid: Decimal = Decimal(0)  # positiv = bezahlt, negativ = erhalten


@dataclass(frozen=True)
class ClosedTrade:
    position_id: str
    setup_id: str
    strategy: str
    symbol: str
    market_type: MarketType
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
    """Kontostand, wie ihn der Risikomanager braucht."""

    equity: Decimal
    free_margin: Decimal
    day_start_equity: Decimal
    peak_equity: Decimal
    open_risk: Decimal  # Summe des geplanten Risikos aller offenen Positionen
    open_count: int
    open_keys: FrozenSet[Tuple[str, MarketType]]


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: Tuple[RejectReason, ...] = ()
    worst_case_loss: Decimal = Decimal(0)
    risk_pct: Decimal = Decimal(0)  # Anteil am Eigenkapital
    total_risk_pct_after: Decimal = Decimal(0)
    margin_required: Decimal = Decimal(0)
    liquidation_price: Optional[Decimal] = None


@dataclass(frozen=True)
class ExecutionResult:
    status: OrderStatus
    client_order_id: str
    reasons: Tuple[RejectReason, ...] = ()
    position: Optional[Position] = None
    risk: Optional[RiskDecision] = None
