"""Marktueberwachung live beobachten (AP-15) - nur lesend, KEIN Handel.

  PYTHONPATH=src python3.12 -m trading.marketdata.watch --exchange bitget --market spot --symbol BTC-USDT \
      --interval 60 --minutes 5

Zeigt jede geschlossene, gepruefte Kerze und am Ende den Zustand (Verbindungen, gefuellte/offene Luecken, verworfene
Nachrichten). Der Not-Aus ist hier ein eigener im Speicher: Er betrifft keinen Handel, zeigt aber, ob der Monitor ihn
ausgeloest haette (Rueckgabewert 1).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import timedelta
from typing import Callable, Optional, Sequence

from trading.clock import Clock, SystemClock
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.marketdata.bitget import BitgetHistorySource
from trading.marketdata.bitget_ws import BitgetStream
from trading.marketdata.coinbase import CoinbaseHistorySource
from trading.marketdata.coinbase_ws import CoinbaseStream
from trading.marketdata.history import ExchangeHistorySource
from trading.marketdata.public_http import JsonGet, PublicHttpGet
from trading.marketdata.stream import ExchangeStream, MarketStreamMonitor, Transport
from trading.safety.kill_switch import InMemoryKillSwitch


def main(argv: Optional[Sequence[str]] = None, transport: Optional[Transport] = None, http: Optional[JsonGet] = None,
         clock: Optional[Clock] = None, sleep: Callable[[float], None] = time.sleep,
         out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(prog="python -m trading.marketdata.watch", description=__doc__.splitlines()[0])
    parser.add_argument("--exchange", choices=["bitget", "coinbase"], required=True)
    parser.add_argument("--market", choices=[m.value for m in MarketType], default="spot")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--minutes", type=float, default=5.0)
    args = parser.parse_args(argv)

    clock = clock or SystemClock()
    instrument = InstrumentId(Exchange(args.exchange), MarketType(args.market), args.symbol)
    protocol: ExchangeStream
    backfill: ExchangeHistorySource
    if args.exchange == "bitget":
        protocol, backfill = BitgetStream(), BitgetHistorySource(http or PublicHttpGet(), clock)
    else:
        protocol, backfill = CoinbaseStream(), CoinbaseHistorySource(http or PublicHttpGet(), clock)
    if transport is None:
        from trading.marketdata.public_ws import PublicWebSocket  # erst hier: braucht wsproto (Python >= 3.10)

        transport = PublicWebSocket()
    kill_switch = InMemoryKillSwitch(clock)
    monitor = MarketStreamMonitor(protocol, transport, [instrument], args.interval, clock,
                                  kill_switch, backfill, sleep=sleep)
    end = clock.now() + timedelta(minutes=args.minutes)
    for candle in monitor.candles(stop=lambda: clock.now() >= end):
        out("%s %s O %s H %s L %s C %s V %s" % (candle.open_time.isoformat(), instrument.key, candle.open, candle.high,
                                                candle.low, candle.close, candle.volume))
    s = monitor.status
    out("Zustand: %d Verbindungen, %d Abbrueche, %d Kerzen, %d Luecken gefuellt, %d offen, %d verworfen%s" % (
        s.connects, s.disconnects, s.candles_out, s.gaps_filled, s.gaps_given_up, s.rejected,
        ", NOT-AUS: %s" % kill_switch.status().note if kill_switch.halted else ""))
    for issue in list(s.issues)[-10:]:
        out("  %s" % issue)
    return 1 if kill_switch.halted else 0


if __name__ == "__main__":
    sys.exit(main())
