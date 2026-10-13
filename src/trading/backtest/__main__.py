"""Vorfuehrung:  python -m trading.backtest

Laesst die Referenz-Strategie (gleitende Durchschnitte) in zwei Einstellungen auf erfundenen Daten laufen
und zeigt die Kennzahlen. Das ist ein Technik-Test - KEINE Aussage ueber echte Maerkte.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading.backtest import BacktestConfig, BacktestEngine, MarketDataset, SyntheticDataSource, SyntheticSpec, comparison_table
from trading.backtest.reference import SmaCrossStrategy
from trading.config import load_settings
from trading.domain.instrument import Exchange, InstrumentId, MarketType


def main() -> None:
    settings = load_settings()  # bricht ab, falls nicht PAPER oder Boersen-Schluessel gesetzt sind
    print("Modus: %s - nur simulierte Trades auf erfundenen Daten" % settings.mode.value.upper())
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    spot = InstrumentId(Exchange.SYNTHETIC, MarketType.SPOT, "BTC-USD")
    futures = InstrumentId(Exchange.SYNTHETIC, MarketType.FUTURES, "ETH-USD")
    source = SyntheticDataSource(seed=7, specs={
        spot: SyntheticSpec(start_price=Decimal("30000"), volatility=0.006),
        futures: SyntheticSpec(start_price=Decimal("2000"), volatility=0.008),
    })
    dataset = MarketDataset.build([source.load(i, 3600, start, start + timedelta(days=60)) for i in (spot, futures)])
    print("Datensatz: %d Instrumente, %s bis %s, Fingerabdruck %s" % (
        len(dataset.instruments), dataset.start.date(), dataset.end.date(), dataset.fingerprint[:12]))
    results = [BacktestEngine(dataset, SmaCrossStrategy(fast, slow), BacktestConfig()).run()
               for fast, slow in ((8, 24), (12, 48))]
    for row in comparison_table(results):
        print("%-10s Rendite %7.3f %% | max. Drawdown %6.3f %% | Trades %3d | Gewinnrate %5.1f %% | "
              "Gebuehren %8.2f | Funding %6.2f | %s" % (
                  row["strategy"], row["return_pct"], row["max_drawdown_pct"], row["trades"],
                  row["win_rate"] * 100, row["total_fees"], row["total_funding"], row["result_hash"][:12]))
    r = results[0]
    print("Blockierte Setups (%s): %s" % (r.strategy, r.blocked_by_reason or "keine"))
    print("Neutrale Platzhalter-Agenten: %s | Protokolleintraege: %d" % (", ".join(r.placeholders), r.events))


if __name__ == "__main__":
    main()
