"""Demo: ein kompletter PAPER-Durchlauf mit synthetischen Daten (ohne Agenten-Logik).

Aufruf:  python -m trading.demo

Die Demo oeffnet einen fest vorgegebenen Beispiel-Trade, nur um zu zeigen, dass Executor, Risiko,
Kosten, Funding und Ereignisprotokoll zusammenspielen. Sie ist KEINE Handelsstrategie.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal

from trading.audit.event_log import InMemoryEventLog
from trading.audit.stats import compute_performance
from trading.clock import FixedClock
from trading.config import load_settings
from trading.domain.models import Horizon, MarketType, OrderIntent, Side, TradeSetup
from trading.execution.paper_executor import PaperExecutor
from trading.marketdata.synthetic import SyntheticMarket, funding_events
from trading.safety.data_validator import SnapshotValidator
from trading.safety.limits import DEFAULT_LIMITS
from trading.safety.risk_manager import RiskManager


def main() -> None:
    settings = load_settings()  # bricht ab, falls nicht PAPER oder Boersenschluessel gesetzt
    print("Modus: %s (nur Simulation, kein echtes Geld)" % settings.mode.value)

    start = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = FixedClock(start)
    log = InMemoryEventLog(clock)
    risk = RiskManager(DEFAULT_LIMITS)
    equity = Decimal("10000")
    executor = PaperExecutor(equity, risk, SnapshotValidator(DEFAULT_LIMITS, clock), log, clock)

    rate = Decimal("0.0001")
    market = SyntheticMarket(
        start_time=start, seed=7, drift=0.0002, volatility=0.002,
        market_type=MarketType.FUTURES, funding_rate=rate,
    )
    first = market.next_candle()
    snap = market.snapshot_for(first)
    clock.set(snap.timestamp)
    executor.on_candle(first)

    price = snap.ask
    setup = TradeSetup(
        setup_id="demo-1",
        instrument=market.instrument,
        side=Side.LONG,
        entry=price,
        stop_loss=price * Decimal("0.99"),
        take_profit=price * Decimal("1.02"),
        horizon=Horizon.INTRADAY,
        strategy="demo",
        created_at=snap.timestamp,
    )
    # Menge so waehlen, dass der geplante Hoechstverlust 0,4 % des Kapitals betraegt.
    quote = risk.costs.quote(Side.LONG, snap)
    per_unit = risk.costs.worst_case_loss(Side.LONG, Decimal(1), quote.fill_price, setup.stop_loss, rate)
    quantity = (equity * Decimal("0.004") / per_unit).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
    result = executor.submit(OrderIntent("demo-1", setup, quantity, Decimal(2)), snap)
    print("Order: %s %s" % (result.status.value, [r.value for r in result.reasons]))

    for _ in range(600):
        candle = market.next_candle()
        clock.set(candle.close_time)
        executor.on_candle(candle)
        for event in funding_events([candle], rate):
            executor.apply_funding(event)
        if not executor.open_positions:
            break

    stats = compute_performance(executor.closed_trades, equity)
    print("Abgeschlossene Trades: %d, Netto-Ergebnis: %s USD" % (stats.trade_count, stats.net_pnl))
    print("Endkapital: %s USD" % executor.equity())
    check = log.verify()
    print("Ereignisprotokoll: %d Eintraege, Kette intakt: %s" % (check.checked, check.ok))


if __name__ == "__main__":
    main()
