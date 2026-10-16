"""Gemeinsame Schnittstelle der Executor-Varianten (Speicher und PostgreSQL)."""

from __future__ import annotations

from decimal import Decimal
from typing import Callable, List, Optional, Protocol, Tuple

from trading.domain.instrument import InstrumentId
from trading.domain.models import (
    Candle,
    ClosedTrade,
    CloseResult,
    ExecutionResult,
    ExitReason,
    FundingEvent,
    MarketSnapshot,
    OrderIntent,
    PortfolioState,
    Position,
)


class Executor(Protocol):
    def submit(self, intent: OrderIntent, snapshot: MarketSnapshot) -> ExecutionResult: ...

    def submit_sized(
        self,
        client_order_id: str,
        snapshot: MarketSnapshot,
        sizer: Callable[[PortfolioState], Optional[OrderIntent]],
    ) -> Optional[ExecutionResult]: ...

    def close_position(
        self, instrument: InstrumentId, snapshot: MarketSnapshot, reason: ExitReason, request_id: str
    ) -> CloseResult: ...

    def on_candle(self, candle: Candle) -> List[ClosedTrade]: ...

    def apply_funding(self, event: FundingEvent) -> Decimal: ...

    def portfolio_state(self) -> PortfolioState: ...

    @property
    def open_positions(self) -> Tuple[Position, ...]: ...

    @property
    def closed_trades(self) -> Tuple[ClosedTrade, ...]: ...
