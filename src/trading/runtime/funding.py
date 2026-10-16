"""Funding-Saetze fuer Futures im laufenden Betrieb (D-031) - schliesst die Luecke aus D-026/D-029.

Die Marktueberwachung liefert nur Kerzen. Fuer Futures holt `LiveFunding` nach jeder geschlossenen Kerze die
Abrechnungen der Boerse seit der letzten Abfrage ueber die oeffentliche REST-Schnittstelle (AP-14) und gibt sie VOR der
Kerze an die Engine weiter. Damit
  * kennt die Engine den aktuellen Satz (sonst blockieren Validator/RIN Futures - fail closed),
  * bucht der Executor die Abrechnung auf offene Positionen. Das wirkt je Position hoechstens einmal: Nach einem Neustart
    werden die Abrechnungen des letzten Tages erneut geliefert - bereits gebuchte bleiben wirkungslos, verpasste werden
    nachgeholt.
Kurs zur Abrechnung: Schluss der Kerze (Naeherung fuer den Mark-Preis, wie in AP-14).
Fail closed: Ist die letzte bekannte Abrechnung eines Futures aelter als 12 Stunden (Bitget rechnet hoechstens alle 8 h
ab), loest `LiveFunding` den Not-Aus aus (DATA_ERROR) - sonst wuerden Haltekosten still fehlen.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Iterable, Iterator, List, Sequence, Union

from trading.backtest.data import DataQualityError
from trading.clock import Clock
from trading.domain.instrument import InstrumentId, MarketType
from trading.domain.models import Candle, FundingEvent
from trading.marketdata.history import MAX_FUNDING_GAP_S, ExchangeHistorySource, from_seconds, normalize, to_seconds
from trading.marketdata.public_http import FetchError
from trading.safety.kill_switch import HaltReason, KillSwitch

LOG = logging.getLogger("trading.runtime.funding")
LOOKBACK = timedelta(days=1)
Event = Union[Candle, FundingEvent]


class LiveFunding:
    def __init__(self, history: ExchangeHistorySource, instruments: Sequence[InstrumentId], clock: Clock,
                 kill_switch: KillSwitch) -> None:
        self._history, self._clock, self._kill_switch = history, clock, kill_switch
        self._started = clock.now()
        self._lookback = self._started - LOOKBACK
        self._futures = [i for i in instruments if i.market_type is MarketType.FUTURES]
        self._asked: Dict[InstrumentId, datetime] = {i: self._lookback for i in self._futures}  # bis wohin gefragt
        self._known: Dict[InstrumentId, datetime] = {}  # letzte bekannte Abrechnung
        self._halted = False
        self.failures = 0

    @property
    def needed(self) -> bool:
        return bool(self._futures)

    def events(self, candles: Iterable[Candle]) -> Iterator[Event]:
        for candle in candles:
            if candle.instrument in self._asked:
                yield from self._new_funding(candle)
            yield candle

    def _new_funding(self, candle: Candle) -> List[FundingEvent]:
        instrument = candle.instrument
        since, until = self._asked[instrument], candle.close_time
        events: List[FundingEvent] = []
        try:
            raw = self._history.fetch_funding(instrument, since, until + timedelta(seconds=1))
            rows = normalize(raw, "funding", to_seconds(since), to_seconds(until) + 1, 0, instrument.key)
            for t, rate in rows:
                when = from_seconds(int(t))
                if since < when <= until:
                    events.append(FundingEvent(instrument, when, Decimal(rate), candle.close))
            self._asked[instrument] = until
            if events:
                self._known[instrument] = events[-1].time
        except (FetchError, DataQualityError, ValueError) as exc:
            self.failures += 1
            LOG.warning("Funding fuer %s nicht abrufbar (%s) - naechster Versuch mit der naechsten Kerze",
                        instrument.key, exc, extra={"component": "funding"})
        self._check_age(instrument, until)
        return events

    def _check_age(self, instrument: InstrumentId, now: datetime) -> None:
        known = self._known.get(instrument)
        if known is not None:
            reference = known  # letzte bekannte Abrechnung
        elif self._asked[instrument] > self._lookback:
            reference = self._lookback  # erfolgreich gefragt, aber seit einem Tag keine Abrechnung
        else:
            reference = self._started  # noch nie erfolgreich gefragt: Frist ab dem Start
        age = (now - reference).total_seconds()
        if age > MAX_FUNDING_GAP_S and not self._halted:
            # Ohne bekannte Abrechnung der letzten 12 h fehlen Haltekosten: keine neuen Positionen mehr
            self._halted = True
            note = "Funding fuer %s seit %.0f h unbekannt" % (instrument.key, age / 3600)
            self._kill_switch.trip(HaltReason.DATA_ERROR, note)
            LOG.critical("Not-Aus: %s", note, extra={"component": "funding", "alert": True})


__all__ = ["LiveFunding"]
