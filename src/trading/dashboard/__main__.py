"""Dashboard starten:  python3.12 -m trading.dashboard [--strategy NAME] [--simulation] [--port 8000] [--host 127.0.0.1]

Standard: alle Strategien der Bibliothek als Backtest auf erfundenen Daten (Modus BACKTEST).
`--simulation`: die Laufzeit-Engine (AP-21) einmal im Zeitraffer mit erfundenen Daten und simulierter Uhr abspielen und das
Paper-Konto anzeigen (Modus DEMO) - KEIN laufender Betrieb, keine Marktdaten, keine Orders an eine Boerse.
Nur lesend; standardmaessig nur auf diesem Rechner erreichbar (http://127.0.0.1:8000).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from trading.audit.event_log import EventLog, InMemoryEventLog
from trading.backtest import BacktestConfig, BacktestEngine, BacktestResult, MarketDataset, SyntheticDataSource, SyntheticSpec
from trading.clock import Clock
from trading.config import load_settings
from trading.dashboard.state import BacktestSource, DashboardSource, PaperSource
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.strategies import default_strategies

LOCAL = "127.0.0.1"


def synthetic_dataset(days: int = 60) -> MarketDataset:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    spot = InstrumentId(Exchange.SYNTHETIC, MarketType.SPOT, "BTC-USD")
    futures = InstrumentId(Exchange.SYNTHETIC, MarketType.FUTURES, "ETH-USD")
    source = SyntheticDataSource(seed=7, specs={
        spot: SyntheticSpec(start_price=Decimal("30000"), volatility=0.006),
        futures: SyntheticSpec(start_price=Decimal("2000"), volatility=0.008),
    })
    return MarketDataset.build([source.load(i, 3600, start, start + timedelta(days=days)) for i in (spot, futures)])


def backtest_source(dataset: MarketDataset, selected: Optional[str] = None) -> BacktestSource:
    results: List[BacktestResult] = []
    logs: Dict[str, EventLog] = {}
    for strategy in default_strategies():
        holder: List[EventLog] = []

        def factory(clock: Clock, holder: List[EventLog] = holder) -> EventLog:
            holder.append(InMemoryEventLog(clock))
            return holder[-1]

        results.append(BacktestEngine(dataset, strategy, BacktestConfig(), log_factory=factory).run())
        logs[strategy.name] = holder[-1]
    return BacktestSource(results, logs, selected=selected)


def simulation_source(dataset: MarketDataset, strategy_name: Optional[str] = None) -> DashboardSource:
    """Laufzeit-Engine einmal im Zeitraffer (simulierte Uhr, erfundene Daten) - danach nur noch Anzeige."""
    from trading.agents.aki import AkiAgent
    from trading.clock import FixedClock
    from trading.domain.universe import TradingUniverse
    from trading.execution.paper_executor import PaperExecutor
    from trading.runtime.engine import RuntimeEngine, simulate
    from trading.safety.data_validator import SnapshotValidator
    from trading.safety.limits import DEFAULT_LIMITS
    from trading.safety.risk_manager import RiskManager
    from trading.strategies import build_strategy

    clock = FixedClock(dataset.start)
    log = InMemoryEventLog(clock)
    capital = Decimal("10000")
    executor = PaperExecutor(capital, RiskManager(DEFAULT_LIMITS), SnapshotValidator(DEFAULT_LIMITS, clock), log, clock)
    aki = AkiAgent(log)
    strategy = build_strategy(strategy_name or "breakout")
    rates = {s.instrument: s.initial_funding_rate for s in dataset.series if s.initial_funding_rate is not None}
    engine = RuntimeEngine(strategy, executor, aki, clock, TradingUniverse(frozenset(dataset.instruments)),
                           funding_rates=rates)
    simulate(engine, [c for s in dataset.series for c in s.candles] + [f for s in dataset.series for f in s.funding],
             clock)
    return PaperSource(executor, aki, capital, dataset.start, clock.now, log=log, halt=executor.halt_status,
                       strategy=strategy.name)


def main(argv: Optional[Sequence[str]] = None) -> None:
    settings = load_settings()  # bricht ab, falls nicht PAPER oder Boersen-Schluessel gesetzt sind
    parser = argparse.ArgumentParser(prog="python -m trading.dashboard", description="Nur lesendes Dashboard")
    parser.add_argument("--strategy", default=None, help="anzuzeigende Strategie (Standard: die erste)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default=LOCAL, help="Standard 127.0.0.1 = nur dieser Rechner")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--simulation", action="store_true",
                        help="Laufzeit-Engine im Zeitraffer abspielen und als DEMO anzeigen (Strategie-Art, z. B. breakout)")
    args = parser.parse_args(argv)
    import uvicorn  # erst hier: Tests und Kern brauchen den Server nicht

    from trading.dashboard.api import create_app

    print("Modus: %s - Backtest auf erfundenen Daten, nichts wird gehandelt." % settings.mode.value.upper())
    if args.host != LOCAL:
        print("WARNUNG: Das Dashboard ist auch von anderen Rechnern erreichbar (%s). Es hat keine Anmeldung." % args.host)
    dataset = synthetic_dataset(args.days)
    source = simulation_source(dataset, args.strategy) if args.simulation else backtest_source(dataset, args.strategy)
    print("Dashboard: http://%s:%d" % (args.host, args.port))
    uvicorn.run(create_app(source), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
