"""Historische Marktdaten einer echten Boerse laden und pruefen (AP-14) - nur lesend, oeffentliche Schnittstellen.

  PYTHONPATH=src python3.12 -m trading.marketdata --exchange coinbase --market spot --symbol BTC-USD \
      --interval 3600 --start 2026-09-01 --end 2026-09-08
  PYTHONPATH=src python3.12 -m trading.marketdata --exchange bitget --market futures --symbol BTC-USDT ...

Ergebnis: Zahl der Kerzen und Funding-Abrechnungen, Pruefergebnis (`MarketDataset.build`) und Fingerabdruck. Die Daten
landen im Zwischenspeicher (`--cache`, Standard `data/marketdata`, nicht im Repository). Keine Schluessel, keine Orders.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from trading.backtest.data import DataQualityError, MarketDataset
from trading.clock import Clock, SystemClock
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.marketdata.bitget import BitgetHistorySource
from trading.marketdata.coinbase import CoinbaseHistorySource
from trading.marketdata.daycache import DayCache
from trading.marketdata.public_http import FetchError, JsonGet, PublicHttpGet

SOURCES = {"coinbase": CoinbaseHistorySource, "bitget": BitgetHistorySource}


def _day(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main(argv: Optional[Sequence[str]] = None, http: Optional[JsonGet] = None, clock: Optional[Clock] = None,
         out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(prog="python -m trading.marketdata", description=__doc__.splitlines()[0])
    parser.add_argument("--exchange", choices=sorted(SOURCES), required=True)
    parser.add_argument("--market", choices=[m.value for m in MarketType], default="spot")
    parser.add_argument("--symbol", required=True, help="kanonisch, z. B. BTC-USD oder BTC-USDT")
    parser.add_argument("--interval", type=int, default=3600, help="Sekunden (z. B. 60, 900, 3600)")
    parser.add_argument("--start", type=_day, required=True, help="JJJJ-MM-TT (UTC)")
    parser.add_argument("--end", type=_day, required=True, help="JJJJ-MM-TT (UTC, ausschliesslich)")
    parser.add_argument("--cache", type=Path, default=Path("data/marketdata"))
    args = parser.parse_args(argv)

    instrument = InstrumentId(Exchange(args.exchange), MarketType(args.market), args.symbol)
    source = SOURCES[args.exchange](http or PublicHttpGet(), clock or SystemClock(), DayCache(args.cache))
    try:
        series = source.load(instrument, args.interval, args.start, args.end)
        dataset = MarketDataset.build([series])
    except (ValueError, FetchError) as exc:  # DataQualityError ist ein ValueError
        out("ABGELEHNT: %s" % exc)
        return 1 if isinstance(exc, DataQualityError) else 2
    out("%s, %d s: %d Kerzen, %d Funding-Abrechnungen, %s bis %s" % (
        instrument.key, args.interval, len(series.candles), len(series.funding), dataset.start, dataset.end))
    out("Pruefung: in Ordnung (%d Warnungen); Fingerabdruck %s; Abrufe bei der Boerse: %d" % (
        len(dataset.warnings), dataset.fingerprint[:16], source.requests))
    for warning in dataset.warnings[:10]:
        out("  Warnung: %s" % warning)
    return 0


if __name__ == "__main__":
    sys.exit(main())
