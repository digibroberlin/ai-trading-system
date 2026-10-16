"""Vorfuehrung:  python -m trading.backtest

Laesst die ganze Strategie-Bibliothek (AP-16) auf erfundenen Daten durch die volle Pipeline laufen und stellt sie den
Vergleichsmassstaeben Kaufen-und-Halten und Nicht-Handeln gegenueber. Das ist ein Technik-Test - KEINE Aussage ueber
echte Maerkte (zufaellige Daten haben keinen ausnutzbaren Vorteil; nach Kosten ist ein leichtes Minus zu erwarten).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading.backtest import BacktestConfig, BacktestEngine, MarketDataset, SyntheticDataSource, SyntheticSpec, comparison_table
from trading.config import load_settings
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.strategies import buy_and_hold, default_strategies


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
    config = BacktestConfig()
    results = [BacktestEngine(dataset, strategy, config).run() for strategy in default_strategies()]
    for row in comparison_table(results):
        print("%-16s Rendite %7.3f %% | max. Drawdown %6.3f %% | Trades %3d | Gewinnrate %5.1f %% | "
              "Gebuehren %8.2f | Funding %6.2f | %s" % (
                  row["strategy"], row["return_pct"], row["max_drawdown_pct"], row["trades"],
                  row["win_rate"] * 100, row["total_fees"], row["total_funding"], row["result_hash"][:12]))
    hold = buy_and_hold(dataset, config.initial_equity, config.costs)
    print("%-16s Rendite %7.3f %% | max. Drawdown %6.3f %% | (Vergleich: ohne Stop, ohne Hebel, nach Kosten)" % (
        hold.name, hold.return_pct, hold.stats.max_drawdown_pct))
    blocked = {r.strategy: r.blocked_by_reason for r in results if r.blocked_by_reason}
    print("Blockierte Setups: %s" % (blocked or "keine"))
    print("Neutrale Platzhalter-Agenten: %s" % ", ".join(results[0].placeholders))


if __name__ == "__main__":
    main()
