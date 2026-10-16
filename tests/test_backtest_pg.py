"""Backtest ueber die PostgreSQL-Ausfuehrung: gleiches Ergebnis wie im Speicher, alles dauerhaft protokolliert.

Die Engine entscheidet in beiden Faellen mit demselben Code. Dieser Test beweist, dass der Weg ueber die Datenbank
(sperren -> laden -> rechnen -> speichern) nichts veraendert: keine Rundung, keine Reihenfolge, kein Ereignis.
"""

import pytest
# Ohne psycopg (z. B. Python 3.9, Kern-Pruefung) sichtbar ueberspringen statt beim Einsammeln abzubrechen.
psycopg = pytest.importorskip("psycopg", reason="psycopg nicht installiert - Datenbank-Tests uebersprungen")
from bt_helpers import config, synthetic
from conftest import requires_db
from helpers import D

from trading.audit.event_log import InMemoryEventLog
from trading.audit.events import EventType
from trading.backtest import BacktestEngine, BacktestError
from trading.backtest.reference import SmaCrossStrategy
from trading.persistence.pg_event_log import PostgresEventLog
from trading.persistence.pg_executor import PostgresPaperExecutor

pytestmark = requires_db


def in_memory(data, strategy, cfg):
    logs = []

    def log_factory(clock):
        logs.append(InMemoryEventLog(clock))
        return logs[0]

    return BacktestEngine(data, strategy, cfg, log_factory=log_factory).run(), logs[0]


def through_postgres(pg, data, strategy, cfg, account="backtest"):
    logs = []

    def log_factory(clock):
        logs.append(PostgresEventLog(pg.app_url, clock))
        return logs[0]

    def executor_factory(clock, risk, log):
        pg.create_account(account, cfg.initial_equity, clock.now().date())
        return PostgresPaperExecutor(pg.app_url, account, risk, clock)

    return BacktestEngine(data, strategy, cfg, log_factory=log_factory, executor_factory=executor_factory).run(), logs[0]


def test_backtest_through_postgres_equals_the_in_memory_backtest(pg):
    data, cfg = synthetic(days=8, vol=0.01), config()
    mem, mem_log = in_memory(data, SmaCrossStrategy(5, 12), cfg)
    db, db_log = through_postgres(pg, data, SmaCrossStrategy(5, 12), cfg)

    assert len(mem.trades) >= 5 and mem.report.total_funding != 0  # der Lauf hat wirklich etwas getan
    assert db.trades == mem.trades
    assert db.equity_curve == mem.equity_curve
    assert db.open_positions == mem.open_positions
    assert db.report == mem.report
    assert db.blocked_by_reason == mem.blocked_by_reason
    assert db.result_hash == mem.result_hash

    # Das Protokoll in der Datenbank enthaelt dieselben Ereignisse in derselben Reihenfolge - und dieselbe Hash-Kette.
    mem_events, db_events = mem_log.events(), PostgresEventLog(pg.app_url).events()
    assert [e.event_type for e in db_events] == [e.event_type for e in mem_events]
    assert [e.payload for e in db_events] == [e.payload for e in mem_events]
    assert [e.hash for e in db_events] == [e.hash for e in mem_events]
    assert db.log_head == mem.log_head and db.events == mem.events
    assert PostgresEventLog(pg.app_url).verify().ok


def test_backtest_state_in_the_database_matches_the_reported_result(pg):
    from psycopg.rows import dict_row

    data, cfg = synthetic(days=8, vol=0.01), config()
    result, _ = through_postgres(pg, data, SmaCrossStrategy(5, 12), cfg)
    with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn:
        account = conn.execute("SELECT * FROM accounts WHERE account_id = 'backtest'").fetchone()
        open_rows = conn.execute("SELECT count(*) AS n FROM positions WHERE status = 'OPEN'").fetchone()
        closed = conn.execute("SELECT count(*) AS n, coalesce(sum(net_pnl), 0) AS pnl FROM closed_trades").fetchone()
        fills = conn.execute("SELECT count(*) AS n FROM event_log WHERE event_type = 'ORDER_FILLED'").fetchone()
    assert open_rows["n"] == len(result.open_positions)
    assert closed["n"] == len(result.trades) and closed["pnl"] == result.report.realized_pnl
    assert fills["n"] == result.setups_executed
    # Guthaben = Startkapital + realisiertes Ergebnis - bereits bezahlte Kosten der noch offenen Positionen
    paid_on_open = sum((p.entry_fee + p.funding_paid for p in result.open_positions), D(0))
    spot_cost = sum((p.quantity * p.entry_price for p in result.open_positions if p.market_type.value == "spot"), D(0))
    assert account["balance"] in (cfg.initial_equity + result.report.realized_pnl - paid_on_open,
                                  cfg.initial_equity + result.report.realized_pnl - paid_on_open - spot_cost)


def test_a_second_backtest_over_the_same_period_in_the_same_database_fails_loudly(pg):
    """Die Datenbank fuehrt je Instrument EINE Marktzeitleiste. Wer denselben Zeitraum noch einmal einspielt,
    liefert aus Sicht der Datenbank veraltete Kerzen: Der Lauf bricht ab, statt ein falsches Ergebnis zu melden.
    (Backtests laufen deshalb im Speicher; ueber PostgreSQL braucht jeder Lauf eine frische Datenbank.)"""
    data, cfg = synthetic(days=5, vol=0.01), config()
    alone, _ = in_memory(data, SmaCrossStrategy(5, 12), cfg)
    first, _ = through_postgres(pg, data, SmaCrossStrategy(5, 12), cfg, account="lauf-a")
    assert first.result_hash == alone.result_hash
    with pytest.raises(BacktestError, match="Marktdaten verworfen"):
        through_postgres(pg, data, SmaCrossStrategy(4, 9), cfg, account="lauf-b")
    log = PostgresEventLog(pg.app_url)
    assert len(log.of_type(EventType.BACKTEST_FINISHED)) == 1  # der zweite Lauf hat KEIN Ergebnis gemeldet
    assert log.of_type(EventType.DATA_REJECTED) and log.verify().ok
    assert not [e for e in log.of_type(EventType.ORDER_FILLED) if e.payload["position"]["strategy"] == "sma4-9"]
