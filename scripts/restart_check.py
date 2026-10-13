"""Container-/System-Neustart-Pruefung (von Hand zu starten, siehe docs/TESTBERICHT.md).

    python scripts/restart_check.py seed   STATEFILE    # Konto + 2 offene Positionen anlegen, Zustand merken
    python scripts/restart_check.py verify STATEFILE    # nach dem Neustart: Zustand mit dem gemerkten vergleichen

Benoetigt DATABASE_URL (eingeschraenkte Rolle trading_app). Verwendet nur synthetische Daten.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

from trading.audit.event_log import verify_events
from trading.clock import FixedClock
from trading.domain.models import Horizon, MarketType, OrderIntent, Side, TradeSetup
from trading.execution.costs import CostModel
from trading.persistence.pg_event_log import PostgresEventLog
from trading.persistence.pg_executor import PostgresPaperExecutor
from trading.safety.limits import DEFAULT_LIMITS
from trading.safety.risk_manager import RiskManager

D = Decimal
T0 = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
NO_SLIP = CostModel(slippage_bps=D(0), stop_slippage_bps=D(0))


def executor(url: str) -> PostgresPaperExecutor:
    return PostgresPaperExecutor(url, "restart-check", D(10000), RiskManager(DEFAULT_LIMITS, NO_SLIP), FixedClock(T0))


def state(url: str) -> dict:
    ex = executor(url)
    st = ex.portfolio_state()
    log = PostgresEventLog(url)
    events = log.events()
    result = {
        "positions": sorted([p.position_id, p.symbol, str(p.quantity), str(p.entry_price), str(p.risk_at_open)]
                            for p in ex.open_positions),
        "balance": str(ex.balance), "equity": str(st.equity), "open_risk": str(st.open_risk),
        "events": len(events), "last_hash": events[-1].hash if events else None,
        "chain_ok": verify_events(list(events)).ok,
    }
    ex.close()
    log.close()
    return result


def seed(url: str) -> None:
    ex = executor(url)
    for oid, sym, side in (("rc-1", "BTC-USD", Side.LONG), ("rc-2", "ETH-USD", Side.SHORT)):
        sign = side.sign
        setup = TradeSetup(oid, sym, MarketType.FUTURES, side, D(100), D(100) - sign * D(1), D(100) + sign * D(3),
                           Horizon.INTRADAY, "restart-check", T0)
        snap = __import__("trading.domain.models", fromlist=["MarketSnapshot"]).MarketSnapshot(
            sym, MarketType.FUTURES, T0, D(100), D(100), D(100), None, D("0.0001"))
        res = ex.submit(OrderIntent(oid, setup, D(40), D(2)), snap)
        print(oid, res.status.value, [r.value for r in res.reasons])
    ex.close()


def main() -> int:
    mode, statefile = sys.argv[1], sys.argv[2]
    url = os.environ["DATABASE_URL"]
    if mode == "seed":
        seed(url)
        s = state(url)
        json.dump(s, open(statefile, "w"), indent=1)
        print("gemerkt:", json.dumps(s))
        return 0 if len(s["positions"]) == 2 and s["chain_ok"] else 1
    expected = json.load(open(statefile))
    got = state(url)
    ok = got == expected
    print("erwartet:", json.dumps(expected))
    print("gefunden:", json.dumps(got))
    print("ERGEBNIS:", "IDENTISCH - Positionen und Protokoll erhalten" if ok else "ABWEICHUNG!")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
