"""Gemeinsame Schnittstelle der Executor-Varianten (Speicher und PostgreSQL)."""

from __future__ import annotations

from typing import Protocol

from trading.domain.models import ExecutionResult, MarketSnapshot, OrderIntent, PortfolioState


class Executor(Protocol):
    def submit(self, intent: OrderIntent, snapshot: MarketSnapshot) -> ExecutionResult: ...

    def portfolio_state(self) -> PortfolioState: ...
