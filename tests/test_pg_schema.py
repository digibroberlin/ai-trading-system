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


def _pos(c, pid, symbol, risk, fee="0", status="OPEN", account="a1", qty=1, exchange="synthetic", market="futures"):
    c.execute(
        "INSERT INTO positions (account_id, position_id, status, setup_id, strategy, exchange, symbol, market_type,"
        " side, quantity, entry_price, stop_loss, take_profit, leverage, opened_at, margin, risk_at_open,"
        " entry_fee, last_funding_ts) VALUES (%s,%s,%s,'s','t',%s,%s,%s,'long',%s,100,99,103,2,now(),50,%s,%s,now())",
        (account, pid, status, exchange, symbol, market, qty, risk, fee),
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


# ------------------------------------------------------------------ Stufe 1: Instrument-Schluessel, Exposure, haertere Trigger


def test_one_open_position_per_instrument_not_per_symbol(pg):
    with admin(pg) as c:
        _account(c)
        _pos(c, "P1", "BTC-USD", 10, market="spot")
        _pos(c, "P2", "BTC-USD", 10, market="futures")  # anderes Instrument: erlaubt
        _pos(c, "P3", "BTC-USD", 10, market="spot", exchange="coinbase")  # andere Boerse: erlaubt
        with pytest.raises(E.UniqueViolation):
            _pos(c, "P4", "BTC-USD", 10, market="spot")
        with pytest.raises(E.CheckViolation):
            _pos(c, "P5", "ETH-USD", 10, exchange="kraken")  # unbekannte Boerse
        with pytest.raises(E.NotNullViolation):
            c.execute("INSERT INTO positions (account_id, position_id, status, setup_id, strategy, symbol,"
                      " market_type, side, quantity, entry_price, stop_loss, take_profit, leverage, opened_at,"
                      " margin, risk_at_open, entry_fee, last_funding_ts) VALUES ('a1','P6','OPEN','s','t',"
                      "'SOL-USD','spot','long',1,100,99,103,1,now(),50,1,0,now())")  # ohne Boerse


@pytest.mark.parametrize("level", ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"])
def test_risk_cap_holds_under_every_isolation_level_without_client_locking(pg, level):
    """Audit M1: Unter REPEATABLE READ liess sich der zweite Gurt umgehen (240 statt 200)."""
    with admin(pg) as c:
        _account(c)
    a, b = psycopg.connect(pg.app_url), psycopg.connect(pg.app_url)
    for conn in (a, b):
        conn.execute("SET TRANSACTION ISOLATION LEVEL " + level)
    _pos(a, "PA", "AAA-USD", 120)
    _pos(b, "PB", "BBB-USD", 120)
    a.commit()
    with pytest.raises((E.CheckViolation, E.SerializationFailure)):
        b.commit()
    b.rollback()
    with admin(pg) as c:
        assert c.execute("SELECT sum(risk_at_open - entry_fee) FROM positions WHERE status='OPEN'").fetchone() == (120,)


def test_gross_exposure_cap_is_enforced_by_the_database_at_commit(pg):
    with admin(pg) as c:
        _account(c)  # Equity 10 000
    conn = psycopg.connect(pg.app_url)
    _pos(conn, "P1", "AAA-USD", 10, qty=60)  # 60 x 100 = 6 000
    conn.commit()
    _pos(conn, "P2", "BBB-USD", 10, qty=41)  # zusammen 10 100 > 10 000
    with pytest.raises(E.CheckViolation):
        conn.commit()
    conn.rollback()
    _pos(conn, "P3", "BBB-USD", 10, qty=40)  # zusammen genau 10 000: erlaubt
    conn.commit()


def test_exposure_cap_values_positions_at_the_current_mark_of_their_instrument(pg):
    with admin(pg) as c:
        _account(c)
        _pos(c, "P1", "AAA-USD", 10, qty=60)
        c.execute("INSERT INTO instrument_state (exchange, market_type, symbol, mark, mark_ts)"
                  " VALUES ('synthetic','futures','AAA-USD', 170, now())")  # Kurs gestiegen: 60 x 170 = 10 200
        c.execute("INSERT INTO instrument_state (exchange, market_type, symbol, mark, mark_ts)"
                  " VALUES ('synthetic','spot','AAA-USD', 1, now())")  # anderes Instrument: zaehlt nicht
    conn = psycopg.connect(pg.app_url)
    _pos(conn, "P2", "BBB-USD", 1, qty=1)
    with pytest.raises(E.CheckViolation):
        conn.commit()


def test_exposure_limit_column_is_capped_and_frozen(pg):
    with admin(pg) as c:
        with pytest.raises(E.CheckViolation):
            _account(c, max_gross_exposure="1.5")
        _account(c)
        assert c.execute("SELECT max_gross_exposure FROM accounts").fetchone() == (D("1.0"),)
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("UPDATE accounts SET max_gross_exposure = 0.5")
    with app(pg) as c, pytest.raises(E.InsufficientPrivilege):
        c.execute("UPDATE accounts SET max_gross_exposure = 1.0")


def test_application_role_cannot_rewrite_risk_data_of_open_positions(pg):
    """Audit M2: Menge, Kurse, Stop und Risiko einer Position sind fuer die Anwendung nicht aenderbar."""
    with admin(pg) as c:
        _account(c)
        _pos(c, "P1", "AAA-USD", 10)
    with app(pg) as c:
        for col in ("risk_at_open = 0.01", "entry_fee = 9", "stop_loss = 1", "quantity = 99", "entry_price = 1",
                    "margin = 0", "leverage = 50", "symbol = 'X-USD'", "exchange = 'coinbase'"):
            with pytest.raises(E.InsufficientPrivilege):
                c.execute("UPDATE positions SET " + col)
        c.execute("UPDATE positions SET funding_paid = 1, last_funding_ts = now()")  # erlaubt
        c.execute("UPDATE positions SET status = 'CLOSED', closed_at = now()")  # erlaubt
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("UPDATE positions SET status = 'OPEN'")  # geschlossen bleibt geschlossen


def test_deprecated_market_state_table_is_read_only_for_the_application(pg):
    with app(pg) as c:
        c.execute("SELECT count(*) FROM market_state")
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("INSERT INTO market_state (symbol, mark) VALUES ('BTC-USD', 1)")
        c.execute("INSERT INTO instrument_state (exchange, market_type, symbol, mark, mark_ts)"
                  " VALUES ('synthetic','spot','BTC-USD', 100, now())")
        with pytest.raises(E.CheckViolation):
            c.execute("UPDATE instrument_state SET mark = 0")
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("DELETE FROM instrument_state")


# ------------------------------------------------------------------ Migrations-Laeufer


def versions(db):
    with psycopg.connect(db.admin_url) as c:
        return c.execute("SELECT version, name, note FROM schema_migrations ORDER BY version").fetchall()


def test_fresh_database_gets_all_migrations_exactly_once(pg):
    from conftest import MIGRATIONS
    from trading.persistence.migrate import load_migrations, migrate, status

    expected = [(m.version, m.name, "") for m in load_migrations(MIGRATIONS)]
    assert versions(pg) == expected and len(expected) >= 2
    assert migrate(pg.admin_url, MIGRATIONS) == []  # zweiter Lauf: nichts mehr zu tun
    assert versions(pg) == expected
    assert all(line.endswith("angewandt") for line in status(pg.admin_url, MIGRATIONS))


def test_changed_or_misnumbered_migration_files_are_refused(pg, tmp_path):
    import shutil

    from conftest import MIGRATIONS
    from trading.persistence.migrate import MigrationError, load_migrations, migrate

    tampered = tmp_path / "m"
    shutil.copytree(MIGRATIONS, tampered)
    first = sorted(tampered.glob("001_*.sql"))[0]
    first.write_text(first.read_text(encoding="utf-8") + "\n-- nachtraeglich geaendert\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="veraendert"):
        migrate(pg.admin_url, tampered)

    gap = tmp_path / "gap"
    shutil.copytree(MIGRATIONS, gap)
    (gap / "005_spaeter.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="lueckenlos"):
        load_migrations(gap)
    (gap / "005_spaeter.sql").unlink()
    (gap / "Kaputt.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="Muster"):
        load_migrations(gap)


def test_failing_migration_changes_nothing(pg, tmp_path):
    import shutil

    from conftest import MIGRATIONS
    from trading.persistence.migrate import load_migrations, migrate

    broken = tmp_path / "m"
    shutil.copytree(MIGRATIONS, broken)
    nxt = len(load_migrations(MIGRATIONS)) + 1
    (broken / ("%03d_kaputt.sql" % nxt)).write_text(
        "CREATE TABLE halb_fertig (a int); SELECT 1/0;", encoding="utf-8")
    with pytest.raises(psycopg.errors.DivisionByZero):
        migrate(pg.admin_url, broken)
    with admin(pg) as c:
        assert c.execute("SELECT to_regclass('public.halb_fertig')").fetchone() == (None,)  # zurueckgerollt
    assert len(versions(pg)) == nxt - 1


def test_database_without_version_table_is_not_touched_unless_baselined(pg_v1):
    """So sah die echte Datenbank vor Stufe 1 aus: Schema 001, aber keine Versionstabelle."""
    from conftest import MIGRATIONS
    from trading.persistence.migrate import MigrationError, migrate

    with admin(pg_v1) as c:
        c.execute("DROP TABLE schema_migrations")  # nur in der kurzlebigen Test-DB
    with pytest.raises(MigrationError, match="baseline"):
        migrate(pg_v1.admin_url, MIGRATIONS)
    with admin(pg_v1) as c:
        assert c.execute("SELECT to_regclass('public.instrument_state')").fetchone() == (None,)
    assert migrate(pg_v1.admin_url, MIGRATIONS, baseline_existing=True) == [2]
    assert [(v, note) for v, _, note in versions(pg_v1)] == [(1, "baseline: bestand bereits"), (2, "")]


def test_migration_002_keeps_existing_positions_trades_and_the_log(pg_v1):
    """Daten im alten Format (wie in der echten Datenbank) muessen die Migration unveraendert ueberstehen."""
    from conftest import MIGRATIONS
    from trading.clock import FixedClock
    from trading.persistence.migrate import migrate
    from trading.persistence.pg_executor import PostgresPaperExecutor
    from trading.safety.limits import DEFAULT_LIMITS
    from trading.safety.risk_manager import RiskManager
    from helpers import NO_SLIP, inst, make_candle
    from trading.domain.models import MarketType

    with admin(pg_v1) as c:  # Stand vor Stufe 1 von Hand anlegen (altes Spaltenformat)
        _account(c, account_id="main", balance="9995.2", equity="9995.2")
        old = ("INSERT INTO positions (account_id, position_id, status, setup_id, strategy, symbol, market_type, side,"
               " quantity, entry_price, stop_loss, take_profit, leverage, opened_at, margin, risk_at_open, entry_fee,"
               " liquidation_price, last_funding_ts) VALUES ('main', %s, %s, 's', 't', %s, 'futures', %s, 9, 100, %s, %s,"
               " 2, %s, 450, 46.143, 0.54, %s, %s)")
        c.execute(old, ("P000001", "OPEN", "BTC-USD", "long", 95, 110, T0, 50.5, T0))
        c.execute(old, ("P000002", "CLOSED", "ETH-USD", "short", 105, 90, T0, 149.5, T0))
        c.execute("INSERT INTO closed_trades VALUES ('main','P000002','s','t','ETH-USD','futures','short',9,100,99,"
                  "%s,%s,'take_profit',9,1,0,8,46)", (T0, T0))
        c.execute("INSERT INTO orders VALUES ('main','alt-1','filled','',%s)", (T0,))
        c.execute("INSERT INTO market_state (symbol, mark, last_ts, last_price) VALUES ('BTC-USD', 101, %s, 101)", (T0,))
        c.execute("UPDATE accounts SET position_counter = 2 WHERE account_id = 'main'")
    log = fill_log(pg_v1, 3)
    head_before = log.events()[-1].hash

    assert migrate(pg_v1.admin_url, MIGRATIONS) == [2]

    with admin(pg_v1) as c:
        assert c.execute("SELECT position_id, status, exchange, symbol, quantity, risk_at_open FROM positions"
                         " ORDER BY position_id").fetchall() == [
            ("P000001", "OPEN", "synthetic", "BTC-USD", D(9), D("46.143")),
            ("P000002", "CLOSED", "synthetic", "ETH-USD", D(9), D("46.143"))]
        assert c.execute("SELECT exchange, net_pnl FROM closed_trades").fetchall() == [("synthetic", D(8))]
        assert c.execute("SELECT market_type, mark, ref_price FROM instrument_state WHERE symbol='BTC-USD'"
                         " ORDER BY market_type").fetchall() == [("futures", D(101), D(101)), ("spot", D(101), D(101))]
        assert c.execute("SELECT count(*) FROM market_state").fetchone() == (1,)  # nichts geloescht
        assert c.execute("SELECT balance, max_gross_exposure FROM accounts").fetchone() == (D("9995.2"), D("1.0"))
    assert log.verify().ok and log.events()[-1].hash == head_before  # Protokoll unberuehrt

    # Der neue Executor arbeitet auf den migrierten Daten weiter:
    clock = FixedClock(T0)
    ex = PostgresPaperExecutor(pg_v1.app_url, "main", D(10000), RiskManager(DEFAULT_LIMITS, NO_SLIP), clock)
    (pos,) = ex.open_positions
    assert pos.instrument == inst("BTC-USD", MarketType.FUTURES) and pos.quantity == D(9)
    assert ex.portfolio_state().unrealized_pnl == D(9)  # bewertet zum uebernommenen Kurs 101
    clock.advance(60)
    (trade,) = ex.on_candle(make_candle(T0, D(101), D(111), D(101), D(110), market_type=MarketType.FUTURES))
    assert trade.exit_reason.value == "take_profit" and trade.position_id == "P000001"
    assert ex.open_positions == () and log.verify().ok
