"""PostgreSQL-Trigger, Rechte und Constraints (gegen eine echte Datenbank)."""

import psycopg
import pytest
from conftest import requires_db
from helpers import D, T0

from trading.audit.events import EventType
from trading.clock import FixedClock
from trading.persistence.pg_event_log import PostgresEventLog

pytestmark = requires_db

from psycopg import errors as E  # noqa: E402


def admin(pg):
    return psycopg.connect(pg.admin_url, autocommit=True)


def app(pg):
    return psycopg.connect(pg.app_url, autocommit=True)


def fill_log(pg, n=4):
    log = PostgresEventLog(pg.app_url, FixedClock(T0))
    for i in range(n):
        log.append(EventType.SETUP_PROPOSED, {"i": i, "preis": D("1.50")})
    return log


# ------------------------------------------------------------------ Ereignisprotokoll


def test_chain_written_by_python_verifies_and_database_accepts_it(pg):
    log = fill_log(pg)
    events = log.events()
    assert [e.seq for e in events] == [1, 2, 3, 4]
    assert log.verify().ok
    with admin(pg) as c:
        assert c.execute("SELECT seq, hash FROM event_log_head").fetchone() == (4, events[-1].hash)
        assert c.execute("SELECT count(*) FROM event_log").fetchone() == (4,)


def test_database_computes_the_same_hash_as_python(pg):
    log = fill_log(pg, 2)
    with admin(pg) as c:
        ok = c.execute(
            "SELECT bool_and(hash = encode(sha256(convert_to(seq::text||'|'||ts_text||'|'||event_type"
            "||'|'||prev_hash||'|'||payload_json,'UTF8')),'hex')) FROM event_log"
        ).fetchone()
    assert ok == (True,) and log.verify().ok


@pytest.mark.parametrize("sql", [
    "UPDATE event_log SET event_type = 'X' WHERE seq = 1",
    "DELETE FROM event_log WHERE seq = 1",
    "TRUNCATE event_log",
])
def test_event_log_cannot_be_changed_even_by_the_administrator(pg, sql):
    fill_log(pg)
    with admin(pg) as c, pytest.raises(E.InsufficientPrivilege):
        c.execute(sql)
    with admin(pg) as c:
        assert c.execute("SELECT count(*) FROM event_log").fetchone() == (4,)


@pytest.mark.parametrize("sql", [
    "UPDATE event_log SET event_type = 'X' WHERE seq = 1",
    "DELETE FROM event_log",
    "TRUNCATE event_log",
    "UPDATE event_log_head SET seq = 0",
    "DELETE FROM event_log_head",
    "TRUNCATE event_log_head",
    "DROP TABLE event_log",
    "ALTER TABLE event_log DISABLE TRIGGER ALL",
])
def test_application_role_cannot_tamper_with_the_log(pg, sql):
    fill_log(pg)
    with app(pg) as c, pytest.raises((E.InsufficientPrivilege, E.UndefinedTable, E.ProgrammingError)):
        c.execute(sql)


def test_head_cannot_be_updated_directly_even_by_the_administrator(pg):
    fill_log(pg)
    with admin(pg) as c, pytest.raises(E.InsufficientPrivilege):
        c.execute("UPDATE event_log_head SET seq = 99, hash = repeat('a', 64)")


def _insert(c, seq, prev, hsh, payload='{"a":1}', payload_jsonb='{"a":1}'):
    c.execute(
        "INSERT INTO event_log (seq, ts_text, ts, event_type, payload_json, payload, prev_hash, hash)"
        " VALUES (%s, '2025-01-01T12:00:00+00:00', '2025-01-01T12:00:00+00:00', 'T', %s, %s::jsonb, %s, %s)",
        (seq, payload, payload_jsonb, prev, hsh),
    )


def test_database_refuses_forged_rows(pg):
    log = fill_log(pg, 2)
    last = log.events()[-1]
    with admin(pg) as c:
        with pytest.raises(E.CheckViolation):  # Sequenzluecke
            _insert(c, 9, last.hash, "0" * 64)
        with pytest.raises(E.CheckViolation):  # falsche Verkettung
            _insert(c, 3, "f" * 64, "0" * 64)
        with pytest.raises(E.CheckViolation):  # Hash passt nicht zum Inhalt
            _insert(c, 3, last.hash, "e" * 64)
        assert c.execute("SELECT count(*) FROM event_log").fetchone() == (2,)


def test_verify_detects_tampering_by_a_superuser_who_disables_the_triggers(pg):
    log = fill_log(pg)
    with admin(pg) as c:  # nur ein Superuser kann das - die Hash-Kette entlarvt es trotzdem
        c.execute("ALTER TABLE event_log DISABLE TRIGGER ALL")
        c.execute("UPDATE event_log SET payload_json = '{\"i\":99}' WHERE seq = 2")
    res = log.verify()
    assert not res.ok and res.first_bad_seq == 2


def test_removing_the_last_events_is_detected_via_the_head_pointer(pg):
    log = fill_log(pg, 5)
    assert log.verify().ok
    with admin(pg) as c:  # Superuser schaltet die Trigger ab und loescht das Ende des Protokolls
        c.execute("ALTER TABLE event_log DISABLE TRIGGER ALL")
        c.execute("DELETE FROM event_log WHERE seq > 3")
    res = log.verify()
    assert not res.ok and "Zeiger" in res.message  # die Kette selbst waere noch lueckenlos!
    from trading.audit.event_log import verify_events
    assert verify_events(list(log.events())).ok


def test_concurrent_appends_from_several_connections_keep_one_chain(pg):
    logs = [PostgresEventLog(pg.app_url, FixedClock(T0)) for _ in range(4)]
    import threading

    def work(log, k):
        for i in range(15):
            log.append(EventType.AGENT_OPINION, {"w": k, "i": i})

    threads = [threading.Thread(target=work, args=(lg, k)) for k, lg in enumerate(logs)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    events = logs[0].events()
    assert [e.seq for e in events] == list(range(1, 61))
    assert logs[0].verify().ok


# ------------------------------------------------------------------ Konto / Limits / Modus


def _account(c, **kw):
    cols = dict(account_id="a1", initial_equity=10000, balance=10000, equity=10000,
                peak_equity=10000, day="2025-01-01", day_start_equity=10000)
    cols.update(kw)
    c.execute(
        "INSERT INTO accounts (" + ",".join(cols) + ") VALUES (" + ",".join(["%s"] * len(cols)) + ")",
        list(cols.values()),
    )


def test_only_paper_mode_and_max_two_percent_can_exist(pg):
    with admin(pg) as c:
        with pytest.raises(E.CheckViolation):
            _account(c, mode="live")
        with pytest.raises(E.CheckViolation):
            _account(c, max_portfolio_risk="0.05")
        with pytest.raises(E.CheckViolation):
            _account(c, initial_equity=0)
        _account(c)
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("UPDATE accounts SET max_portfolio_risk = 0.01")  # auch Verschaerfung nur per Neuanlage
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("UPDATE accounts SET mode = 'live'")
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("DELETE FROM accounts")


def test_application_role_cannot_touch_limits_or_mode_columns(pg):
    with admin(pg) as c:
        _account(c)
    with app(pg) as c:
        for col in ("max_portfolio_risk = 0.02", "mode = 'paper'", "initial_equity = 1"):
            with pytest.raises(E.InsufficientPrivilege):
                c.execute("UPDATE accounts SET " + col)
        c.execute("UPDATE accounts SET balance = balance")  # erlaubte Spalte


def test_database_hard_cap_matches_the_python_hard_cap(pg):
    from trading.safety.limits import HARD_MAX_PORTFOLIO_RISK

    with admin(pg) as c:
        _account(c)
        assert c.execute("SELECT max_portfolio_risk FROM accounts").fetchone() == (HARD_MAX_PORTFOLIO_RISK,)


# ------------------------------------------------------------------ Positionen und 2-%-Constraint


def _pos(c, pid, symbol, risk, fee="0", status="OPEN", account="a1"):
    c.execute(
        "INSERT INTO positions (account_id, position_id, status, setup_id, strategy, symbol, market_type,"
        " side, quantity, entry_price, stop_loss, take_profit, leverage, opened_at, margin, risk_at_open,"
        " entry_fee, last_funding_ts) VALUES (%s,%s,%s,'s','t',%s,'futures','long',1,100,99,103,2,now(),50,%s,%s,now())",
        (account, pid, status, symbol, risk, fee),
    )


def test_risk_cap_is_enforced_by_the_database_at_commit(pg):
    with admin(pg) as c:
        _account(c)
    conn = psycopg.connect(pg.app_url)  # ohne autocommit: eine Transaktion
    _pos(conn, "P1", "AAA-USD", 150)  # 1,5 % -> ok
    conn.commit()
    _pos(conn, "P2", "BBB-USD", 60)  # zusammen 2,1 % -> Datenbank lehnt beim COMMIT ab
    with pytest.raises(E.CheckViolation):
        conn.commit()
    conn.rollback()
    with admin(pg) as c:
        assert c.execute("SELECT count(*) FROM positions").fetchone() == (1,)


def test_risk_cap_counts_only_remaining_risk_entry_fee_is_already_paid(pg):
    with admin(pg) as c:
        _account(c)
    conn = psycopg.connect(pg.app_url)
    _pos(conn, "P1", "AAA-USD", 150)
    _pos(conn, "P2", "BBB-USD", 52, fee="3")  # 150 + (52 - 3) = 199 <= 200
    conn.commit()
    _pos(conn, "P3", "CCC-USD", 2)  # 199 + 2 = 201 > 200
    with pytest.raises(E.CheckViolation):
        conn.commit()


def test_two_open_transactions_cannot_jointly_exceed_the_cap_even_without_locking(pg):
    """Genau der Wettlauf, den die Sperre verhindern soll - hier OHNE jede Sperre im Client."""
    with admin(pg) as c:
        _account(c)
    a, b = psycopg.connect(pg.app_url), psycopg.connect(pg.app_url)
    _pos(a, "PA", "AAA-USD", 120)  # einzeln ok (1,2 %)
    _pos(b, "PB", "BBB-USD", 120)  # einzeln ok (1,2 %), zusammen 2,4 %
    a.commit()
    with pytest.raises(E.CheckViolation):
        b.commit()  # der zweite sieht nach dem Warten den ersten und scheitert
    b.rollback()
    with admin(pg) as c:
        assert c.execute("SELECT count(*) FROM positions WHERE status = 'OPEN'").fetchone() == (1,)


def test_second_open_position_in_same_market_is_refused_by_the_database(pg):
    with admin(pg) as c:
        _account(c)
        _pos(c, "P1", "AAA-USD", 10)
        with pytest.raises(E.UniqueViolation):
            _pos(c, "P2", "AAA-USD", 10)
        c.execute("UPDATE positions SET status = 'CLOSED', closed_at = now() WHERE position_id = 'P1'")
        _pos(c, "P3", "AAA-USD", 10)  # nach dem Schliessen wieder moeglich


def test_closed_positions_orders_and_trades_are_immutable(pg):
    with admin(pg) as c:
        _account(c)
        _pos(c, "P1", "AAA-USD", 10)
        c.execute("UPDATE positions SET status = 'CLOSED', closed_at = now()")
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("UPDATE positions SET funding_paid = 5")
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("DELETE FROM positions")
        c.execute("INSERT INTO orders VALUES ('a1','o1','filled','',now())")
        with pytest.raises(E.UniqueViolation):
            c.execute("INSERT INTO orders VALUES ('a1','o1','rejected','',now())")  # Doppel-Order
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("UPDATE orders SET status = 'rejected'")
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("TRUNCATE orders")


def test_application_role_has_no_delete_truncate_or_ddl(pg):
    with admin(pg) as c:
        _account(c)
    with app(pg) as c:
        for sql in ("DELETE FROM positions", "TRUNCATE positions", "DROP TABLE positions",
                    "CREATE TABLE x (a int)", "DELETE FROM orders", "UPDATE closed_trades SET fees = 0"):
            with pytest.raises((E.InsufficientPrivilege, E.ProgrammingError)):
                c.execute(sql)
