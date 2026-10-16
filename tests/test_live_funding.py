"""Funding-Saetze fuer Futures im laufenden Betrieb (D-031): vor der Kerze, ohne Doppelte, Nachholen nach einem Neustart,
Not-Aus wenn Abrechnungen ausbleiben - mit einer nachgebildeten Bitget-Schnittstelle (kein Netz)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading.clock import FixedClock
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.domain.models import Candle, FundingEvent
from trading.marketdata.bitget import BitgetHistorySource
from trading.marketdata.public_http import FetchError
from trading.marketdata.ratelimit import RateLimiter
from trading.runtime.funding import LiveFunding
from trading.safety.kill_switch import HaltReason, InMemoryKillSwitch

D = Decimal
UTC = timezone.utc
T0 = datetime(2026, 9, 2, 0, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
PERP = InstrumentId(Exchange.BITGET, MarketType.FUTURES, "BTC-USDT")
SPOT = InstrumentId(Exchange.BITGET, MarketType.SPOT, "BTC-USDT")


def ts(moment):
    return int((moment - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds())


class FakeFundingApi:
    """GET /api/v2/mix/market/history-fund-rate: Abrechnungen alle 8 h bis `until`, neueste zuerst, Seiten zu 100."""

    def __init__(self, clock, fail=0, stop_after=None):
        self.clock, self.fail, self.stop_after, self.calls = clock, fail, stop_after, 0

    def rate(self, t):
        return D("0.0001") if (t // 28800) % 2 else D("-0.00005")

    def __call__(self, url, params):
        assert url.endswith("/api/v2/mix/market/history-fund-rate") and params["productType"] == "USDT-FUTURES"
        self.calls += 1
        if self.fail:
            self.fail -= 1
            raise FetchError("HTTP 503", 503, False)
        end = ts(self.clock.now()) if self.stop_after is None else ts(self.stop_after)
        times = [t for t in range(ts(T0) - 5 * 86400, end + 1, 28800)]
        page, size = int(params["pageNo"]), int(params["pageSize"])
        newest = list(reversed(times))[(page - 1) * size: page * size]
        return {"code": "00000", "msg": "success",
                "data": [{"symbol": "BTCUSDT", "fundingRate": str(self.rate(t)), "fundingTime": str(t * 1000)}
                         for t in newest]}


def candle(instrument, open_time):
    return Candle(instrument, open_time, 3600, D(100), D(101), D(99), D("100.5"), D(1))


def setup(fail=0, stop_after=None, start=T0 + 5 * HOUR):
    clock = FixedClock(start)
    api = FakeFundingApi(clock, fail, stop_after)
    history = BitgetHistorySource(api, clock, None, RateLimiter(50, monotonic=lambda: 0.0, sleep=lambda s: None),
                                  sleep=lambda s: None)
    switch = InMemoryKillSwitch(clock)
    return LiveFunding(history, [PERP, SPOT], clock, switch), clock, switch, api


def feed(funding, clock, opens):
    out = []
    for open_time in opens:
        clock.set(open_time + HOUR) if hasattr(clock, "set") else clock.advance(
            (open_time + HOUR - clock.now()).total_seconds())
        out += list(funding.events([candle(PERP, open_time)]))
    return out


def test_settlements_arrive_before_their_candle_once_and_with_the_exchange_rate():
    funding, clock, switch, api = setup()
    out = feed(funding, clock, [T0 + 5 * HOUR + HOUR * i for i in range(12)])  # 05:00 .. 16:00
    fundings = [e for e in out if isinstance(e, FundingEvent)]
    # erste Kerze: Abrechnungen des letzten Tages (Nachholen nach Neustart), danach 08:00 und 16:00
    assert [e.time for e in fundings] == [T0 - timedelta(hours=16), T0 - timedelta(hours=8), T0,
                                          T0 + 8 * HOUR, T0 + 16 * HOUR]
    assert all(e.rate == api.rate(ts(e.time)) and e.mark_price == D("100.5") for e in fundings)
    eight = out.index(next(e for e in fundings if e.time == T0 + 8 * HOUR))
    assert isinstance(out[eight + 1], Candle) and out[eight + 1].close_time == T0 + 8 * HOUR  # vor ihrer Kerze
    assert len({e.time for e in fundings}) == len(fundings) and not switch.halted


def test_spot_candles_pass_through_untouched():
    funding, clock, switch, api = setup()
    spot = candle(SPOT, T0 + 5 * HOUR)
    assert list(funding.events([spot])) == [spot] and api.calls == 0


def test_temporary_failures_are_retried_with_the_next_candle():
    funding, clock, switch, api = setup(fail=2)
    out = feed(funding, clock, [T0 + 5 * HOUR + HOUR * i for i in range(4)])
    times = [e.time for e in out if isinstance(e, FundingEvent)]
    assert funding.failures == 2 and times == [T0 - 16 * HOUR, T0 - 8 * HOUR, T0, T0 + 8 * HOUR]  # nachgeholt, einmal
    assert not switch.halted  # kurz ausgefallen: kein Not-Aus


def test_missing_settlements_trip_the_kill_switch_once():
    funding, clock, switch, api = setup(stop_after=T0)  # Boerse liefert ab 00:00 nichts mehr
    feed(funding, clock, [T0 + 5 * HOUR + HOUR * i for i in range(8)])  # bis 13:00: letzte Abrechnung 13 h alt
    status = switch.status()
    assert status.halted and status.reason is HaltReason.DATA_ERROR and "Funding" in status.note
    since = status.since
    feed(funding, clock, [T0 + 13 * HOUR + HOUR * i for i in range(3)])
    assert switch.status().since == since


def test_never_reachable_funding_halts_after_twelve_hours():
    funding, clock, switch, api = setup(fail=100)
    feed(funding, clock, [T0 + 5 * HOUR + HOUR * i for i in range(12)])  # 12 h seit dem Start
    assert not switch.halted
    feed(funding, clock, [T0 + 17 * HOUR])
    assert switch.halted


def test_funding_feeds_the_engine_rate():
    from trading.agents.aki import AkiAgent
    from trading.audit.event_log import InMemoryEventLog
    from trading.domain.universe import TradingUniverse
    from trading.execution.paper_executor import PaperExecutor
    from trading.runtime.engine import RuntimeEngine
    from trading.safety.data_validator import SnapshotValidator
    from trading.safety.limits import DEFAULT_LIMITS
    from trading.safety.risk_manager import RiskManager
    from trading.strategies import BreakoutStrategy

    funding, clock, switch, api = setup()
    log = InMemoryEventLog(clock)
    executor = PaperExecutor(D(10000), RiskManager(DEFAULT_LIMITS), SnapshotValidator(DEFAULT_LIMITS, clock), log, clock)
    engine = RuntimeEngine(BreakoutStrategy(), executor, AkiAgent(log), clock, TradingUniverse(frozenset({PERP})))
    for event in feed(funding, clock, [T0 + 5 * HOUR, T0 + 6 * HOUR, T0 + 7 * HOUR]):
        engine.process(event)
    assert engine.stats.fundings == 4 and engine.stats.errors == 0
    assert engine._funding[PERP] == api.rate(ts(T0 + 8 * HOUR))
