"""Arbeitsprozesse fuer die Mehrprozess-Tests.

Jeder Worker ist ein EIGENER Betriebssystem-Prozess (`python tests/pg_workers.py <art> <json-argumente>`).
Alle starten zu einer gemeinsamen Uhrzeit (`start_at`), damit sie wirklich gleichzeitig zugreifen.
Ergebnisse werden als JSON-Zeile auf stdout gemeldet.
"""

from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal
from typing import Any, Dict, List

from helpers import FUT, NO_SLIP, T0, inst, make_candle, make_setup, snapshot

from trading.clock import FixedClock
from trading.domain.models import ExitReason, FundingEvent, MarketType, OrderIntent
from trading.persistence.pg_executor import PostgresPaperExecutor
from trading.safety.limits import DEFAULT_LIMITS
from trading.safety.risk_manager import RiskManager

D = Decimal


def emit(obj: Dict[str, Any]) -> None:
    obj["pid"] = os.getpid()
    print(json.dumps(obj), flush=True)


def wait_until(start_at: float) -> None:
    time.sleep(max(0.0, start_at - time.time()))


def make_executor(url: str, account: str, delay: float = 0.0, fault_hook: Any = None) -> PostgresPaperExecutor:
    risk = RiskManager(DEFAULT_LIMITS, NO_SLIP)
    if delay:
        original = risk.evaluate

        def slow(*a: Any, **k: Any) -> Any:  # Zeitfenster fuer Wettlaeufe kuenstlich vergroessern
            decision = original(*a, **k)
            time.sleep(delay)
            return decision

        risk.evaluate = slow  # type: ignore[method-assign]
    return PostgresPaperExecutor(url, account, D(10000), risk, FixedClock(T0), fault_hook)


def order_worker(a: Dict[str, Any]) -> None:
    """a: url, account, specs=[(order_id, symbol)], start_at.
    Futures, 9 Stueck zu 100, Stop 95, 2x Hebel: Hoechstverlust 46,143 (0,46 %), Exposure 9 %, Stress 0,9 %.
    Vier solche Positionen passen unter das 2-%-Gesamtrisiko, die fuenfte nicht."""
    ex = make_executor(a["url"], a["account"], 0.05)
    wait_until(a["start_at"])
    for oid, sym in a["specs"]:
        setup = make_setup(oid, symbol=sym, market_type=MarketType.FUTURES, stop=D(95), tp=D(110))
        res = ex.submit(OrderIntent(oid, setup, D(a.get("qty", 9)), D(2)), snapshot(symbol=sym, ts=T0, **FUT))
        emit({"order": oid, "status": res.status.value, "reasons": [r.value for r in res.reasons],
              "replayed": res.replayed, "position": res.position.position_id if res.position else None})
    ex.close()


def halt_worker(a: Dict[str, Any]) -> None:
    """Loest den globalen Not-Aus aus, sobald mindestens `after_fills` Positionen eroeffnet sind - also sicher MITTEN im
    Handel der anderen Prozesse (nicht vorher, nicht nachher; unabhaengig davon, wie schnell die Prozesse starten)."""
    import psycopg

    ex = make_executor(a["url"], a["account"])
    wait_until(a["start_at"])
    with psycopg.connect(a["url"], autocommit=True) as conn:
        deadline = time.time() + 60
        while time.time() < deadline:
            row = conn.execute("SELECT count(*) FROM positions").fetchone()
            if row is not None and row[0] >= a["after_fills"]:
                break
            time.sleep(0.005)
    emit({"halt": ex.halt("Mehrprozess-Test")})
    ex.close()


def crash_worker(a: Dict[str, Any]) -> None:
    """Fuehrt eine Order aus; bei hang=True haelt er VOR dem Commit an und wartet auf den Kill."""

    def hook(stage: str) -> None:
        if stage == "before_commit" and a["hang"]:
            open(a["ready_file"], "w").write("ready")
            time.sleep(120)

    ex = make_executor(a["url"], a["account"], 0.0, hook)
    setup = make_setup("crash-1", symbol="CRSH-USD", market_type=MarketType.FUTURES, stop=D(95), tp=D(110))
    ex.submit(OrderIntent("crash-1", setup, D(9), D(2)), snapshot(symbol="CRSH-USD", ts=T0, **FUT))
    emit({"done": True})
    ex.close()


def pipeline_worker(a: Dict[str, Any]) -> None:
    """Komplette Pipeline (RIN + SORA + Executor + AKI-Protokoll in PostgreSQL) in einem eigenen Prozess."""
    from stubs import StubKibo, StubMomo, StubYuzu

    from trading.agents import (
        AkiAgent,
        MarketContext,
        RinConfig,
        RuleBasedRin,
        RuleBasedSora,
        SizingPolicy,
    )
    from trading.domain.universe import TradingUniverse
    from trading.persistence.pg_event_log import PostgresEventLog
    from trading.pipeline import TradingPipeline

    symbol = a["symbol"]
    clock = FixedClock(T0)
    ex = make_executor(a["url"], a["account"], 0.02)
    log = PostgresEventLog(a["url"], clock)
    rin = RuleBasedRin(TradingUniverse.of(futures=[symbol]), clock, RinConfig(), NO_SLIP)
    sora = RuleBasedSora(SizingPolicy.from_limits(DEFAULT_LIMITS), NO_SLIP)
    # Weiter Stop (5 %): SORA plant je 0,4 % Risiko; fuenf Positionen passen unter alle Grenzen.
    setup = make_setup("pipe-" + symbol, symbol=symbol, market_type=MarketType.FUTURES,
                       entry=D(100), stop=D(95), tp=D(115))
    pipe = TradingPipeline(StubMomo([setup]), rin, StubKibo(), StubYuzu(), sora, AkiAgent(log), ex)
    wait_until(a["start_at"])
    outcome = pipe.run_cycle([MarketContext(snapshot(symbol=symbol, ts=T0, **FUT))])[0]
    emit({"symbol": symbol, "executed": outcome.executed, "stage": outcome.stage,
          "reasons": [r.value for r in outcome.reasons]})
    ex.close()
    log.close()


def state_worker(a: Dict[str, Any]) -> None:
    """Liest den Zustand in einem FRISCHEN Prozess (= Neustart) und meldet ihn."""
    ex = make_executor(a["url"], a["account"])
    st = ex.portfolio_state()
    emit({
        "positions": sorted([p.position_id, p.symbol, str(p.quantity), str(p.entry_price),
                             str(p.risk_at_open), str(p.entry_fee)] for p in ex.open_positions),
        "balance": str(ex.balance), "equity": str(st.equity), "peak": str(st.peak_equity),
        "open_risk": str(st.open_risk),
    })
    ex.close()


def operation_worker(a: Dict[str, Any]) -> None:
    """Dieselbe Operation aus vielen Prozessen: a["op"] in funding | candle | close (Instrument BTC-USD Futures)."""
    from datetime import timedelta

    clock = FixedClock(T0 + timedelta(hours=4))
    risk = RiskManager(DEFAULT_LIMITS, NO_SLIP)
    ex = PostgresPaperExecutor(a["url"], a["account"], D(10000), risk, clock)
    instrument = inst("BTC-USD", MarketType.FUTURES)
    wait_until(a["start_at"])
    if a["op"] == "funding":
        result: Any = str(ex.apply_funding(FundingEvent(instrument, clock.now(), D("0.0001"), D(100))))
    elif a["op"] == "candle":
        candle = make_candle(clock.now() - timedelta(minutes=1), D(100), D(100), D(94), D(95),
                             market_type=MarketType.FUTURES)
        result = len(ex.on_candle(candle))
    else:
        res = ex.close_position(instrument, snapshot(ts=clock.now(), **FUT), ExitReason.SIGNAL, "c%d" % os.getpid())
        result = [res.closed, [r.value for r in res.reasons]]
    emit({"op": a["op"], "result": result})
    ex.close()


WORKERS = {"order": order_worker, "halt": halt_worker, "crash": crash_worker, "pipeline": pipeline_worker, "state": state_worker,
           "operation": operation_worker}

if __name__ == "__main__":
    try:
        WORKERS[sys.argv[1]](json.loads(sys.argv[2]))
    except Exception as exc:  # noqa: BLE001
        emit({"error": repr(exc)})
        sys.exit(1)
