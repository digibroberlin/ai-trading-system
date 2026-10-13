-- =====================================================================================
-- Datenbankschema des Paper-Trading-Systems (PostgreSQL 16)
--
-- Schutzebenen (Verteidigung in der Tiefe):
--   1. Das Ereignisprotokoll ist append-only: Trigger verbieten UPDATE/DELETE/TRUNCATE,
--      pruefen Sequenz, Verkettung und den SHA-256-Hash JEDER neuen Zeile selbst.
--   2. Die Anwendungsrolle `trading_app` hat nur die noetigsten Rechte (kein UPDATE/DELETE
--      auf das Protokoll, keine Aenderung der Limits, kein TRUNCATE, kein DDL).
--   3. Das 2-%-Gesamtrisiko wird zusaetzlich beim COMMIT von der Datenbank erzwungen
--      (Constraint-Trigger mit Zeilensperre) - selbst wenn ein Prozess seine Sperre vergisst.
--   4. Das Konto kann nur im Modus 'paper' existieren; die Limits sind per Trigger eingefroren.
-- =====================================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'trading_app') THEN
        CREATE ROLE trading_app NOLOGIN;
    END IF;
END $$;

-- Allgemeine Sperre fuer unveraenderliche Tabellen -------------------------------------
CREATE OR REPLACE FUNCTION forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% ist auf %.% nicht erlaubt (unveraenderlich / append-only)',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
END;
$$;

-- 1. Ereignisprotokoll (AKI) -----------------------------------------------------------
CREATE TABLE event_log (
    seq          BIGINT      PRIMARY KEY CHECK (seq > 0),
    ts_text      TEXT        NOT NULL,   -- exakt der gehashte Zeitstempel-Text
    ts           TIMESTAMPTZ NOT NULL,   -- zum bequemen Abfragen
    event_type   TEXT        NOT NULL,
    payload_json TEXT        NOT NULL,   -- kanonischer JSON-Text, exakt das, was gehasht wurde
    payload      JSONB       NOT NULL,   -- zum bequemen Abfragen
    prev_hash    TEXT        NOT NULL CHECK (length(prev_hash) = 64),
    hash         TEXT        NOT NULL UNIQUE CHECK (length(hash) = 64)
);
CREATE INDEX event_log_type_idx ON event_log (event_type, ts);

-- Zeiger auf das aktuelle Kettenende; wird nur vom Trigger unten fortgeschrieben.
CREATE TABLE event_log_head (
    id   SMALLINT PRIMARY KEY CHECK (id = 1),
    seq  BIGINT   NOT NULL,
    hash TEXT     NOT NULL
);
INSERT INTO event_log_head VALUES (1, 0, repeat('0', 64));

CREATE OR REPLACE FUNCTION event_log_before_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    head     event_log_head%ROWTYPE;
    expected TEXT;
BEGIN
    SELECT * INTO head FROM event_log_head WHERE id = 1 FOR UPDATE;  -- serialisiert alle Schreiber
    IF NEW.seq <> head.seq + 1 THEN
        RAISE EXCEPTION 'event_log: falsche Sequenznummer (erwartet %, erhalten %)', head.seq + 1, NEW.seq
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.prev_hash <> head.hash THEN
        RAISE EXCEPTION 'event_log: Verkettung gebrochen (prev_hash passt nicht zum Kettenende)'
            USING ERRCODE = 'check_violation';
    END IF;
    expected := encode(sha256(convert_to(
        NEW.seq::text || '|' || NEW.ts_text || '|' || NEW.event_type || '|' || NEW.prev_hash || '|' || NEW.payload_json,
        'UTF8')), 'hex');
    IF NEW.hash <> expected THEN
        RAISE EXCEPTION 'event_log: Hash passt nicht zum Inhalt' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.payload IS DISTINCT FROM NEW.payload_json::jsonb THEN
        RAISE EXCEPTION 'event_log: payload und payload_json sind verschieden' USING ERRCODE = 'check_violation';
    END IF;
    UPDATE event_log_head SET seq = NEW.seq, hash = NEW.hash WHERE id = 1;
    RETURN NEW;
END;
$$;

CREATE TRIGGER event_log_chain BEFORE INSERT ON event_log
    FOR EACH ROW EXECUTE FUNCTION event_log_before_insert();
CREATE TRIGGER event_log_no_update_delete BEFORE UPDATE OR DELETE ON event_log
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER event_log_no_truncate BEFORE TRUNCATE ON event_log
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- Das Kettenende darf nur aus dem Insert-Trigger heraus (Verschachtelungstiefe 2) geaendert werden.
CREATE OR REPLACE FUNCTION event_log_head_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR pg_trigger_depth() < 2 THEN
        RAISE EXCEPTION 'event_log_head darf nicht direkt veraendert werden' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER event_log_head_guard BEFORE UPDATE OR DELETE ON event_log_head
    FOR EACH ROW EXECUTE FUNCTION event_log_head_guard();
CREATE TRIGGER event_log_head_no_truncate BEFORE TRUNCATE ON event_log_head
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- 2. Konto -------------------------------------------------------------------------------
CREATE TABLE accounts (
    account_id         TEXT        PRIMARY KEY,
    mode               TEXT        NOT NULL DEFAULT 'paper' CHECK (mode = 'paper'),
    initial_equity     NUMERIC     NOT NULL CHECK (initial_equity > 0),
    balance            NUMERIC     NOT NULL,
    equity             NUMERIC     NOT NULL,
    peak_equity        NUMERIC     NOT NULL,
    day                DATE        NOT NULL,
    day_start_equity   NUMERIC     NOT NULL,
    position_counter   BIGINT      NOT NULL DEFAULT 0,
    max_portfolio_risk NUMERIC     NOT NULL DEFAULT 0.02
                       CHECK (max_portfolio_risk > 0 AND max_portfolio_risk <= 0.02),
    version            BIGINT      NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION accounts_freeze() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.mode <> OLD.mode OR NEW.max_portfolio_risk <> OLD.max_portfolio_risk
       OR NEW.initial_equity <> OLD.initial_equity OR NEW.account_id <> OLD.account_id THEN
        RAISE EXCEPTION 'Modus, Risikolimit und Startkapital eines Kontos sind unveraenderlich'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER accounts_freeze BEFORE UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION accounts_freeze();
CREATE TRIGGER accounts_no_delete BEFORE DELETE ON accounts
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER accounts_no_truncate BEFORE TRUNCATE ON accounts
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- 3. Positionen ---------------------------------------------------------------------------
CREATE TABLE positions (
    account_id        TEXT        NOT NULL REFERENCES accounts (account_id),
    position_id       TEXT        NOT NULL,
    status            TEXT        NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    setup_id          TEXT        NOT NULL,
    strategy          TEXT        NOT NULL,
    symbol            TEXT        NOT NULL,
    market_type       TEXT        NOT NULL CHECK (market_type IN ('spot', 'futures')),
    side              TEXT        NOT NULL CHECK (side IN ('long', 'short')),
    quantity          NUMERIC     NOT NULL CHECK (quantity > 0),
    entry_price       NUMERIC     NOT NULL CHECK (entry_price > 0),
    stop_loss         NUMERIC     NOT NULL CHECK (stop_loss > 0),
    take_profit       NUMERIC     NOT NULL CHECK (take_profit > 0),
    leverage          NUMERIC     NOT NULL CHECK (leverage >= 1),
    opened_at         TIMESTAMPTZ NOT NULL,
    margin            NUMERIC     NOT NULL,
    risk_at_open      NUMERIC     NOT NULL CHECK (risk_at_open > 0),
    entry_fee         NUMERIC     NOT NULL,
    liquidation_price NUMERIC,
    last_funding_ts   TIMESTAMPTZ NOT NULL,
    funding_paid      NUMERIC     NOT NULL DEFAULT 0,
    closed_at         TIMESTAMPTZ,
    PRIMARY KEY (account_id, position_id)
);
-- Pro Markt hoechstens EINE offene Position - auch gegen Wettlaeufe zwischen Prozessen.
CREATE UNIQUE INDEX positions_one_open_per_market
    ON positions (account_id, symbol, market_type) WHERE status = 'OPEN';

CREATE OR REPLACE FUNCTION positions_closed_is_final() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'CLOSED' THEN
        RAISE EXCEPTION 'Geschlossene Positionen sind unveraenderlich' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER positions_closed_is_final BEFORE UPDATE ON positions
    FOR EACH ROW EXECUTE FUNCTION positions_closed_is_final();
CREATE TRIGGER positions_no_delete BEFORE DELETE ON positions
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER positions_no_truncate BEFORE TRUNCATE ON positions
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- Zweiter Gurt fuer das 2-%-Limit: Beim COMMIT jeder Transaktion, die eine Position anlegt,
-- wird das Restrisiko aller offenen Positionen gegen das Kontolimit geprueft. Die Zeilensperre
-- auf dem Konto serialisiert konkurrierende Transaktionen; nach dem Warten sieht die Pruefung
-- die bereits eingetragenen Positionen der anderen Prozesse.
CREATE OR REPLACE FUNCTION positions_risk_cap() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    acc       accounts%ROWTYPE;
    remaining NUMERIC;
BEGIN
    -- FOR NO KEY UPDATE (nicht FOR UPDATE): serialisiert die Trigger untereinander, kollidiert aber
    -- nicht mit der Fremdschluessel-Sperre (FOR KEY SHARE) anderer offener Transaktionen -
    -- sonst koennten sich zwei Transaktionen gegenseitig beim COMMIT blockieren.
    SELECT * INTO acc FROM accounts WHERE account_id = NEW.account_id FOR NO KEY UPDATE;
    SELECT COALESCE(SUM(risk_at_open - entry_fee), 0) INTO remaining
      FROM positions WHERE account_id = NEW.account_id AND status = 'OPEN';
    IF acc.equity <= 0 OR remaining > acc.max_portfolio_risk * acc.equity THEN
        RAISE EXCEPTION 'Gesamtrisiko-Limit verletzt: Restrisiko % > % x Eigenkapital %',
            remaining, acc.max_portfolio_risk, acc.equity USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER positions_risk_cap AFTER INSERT ON positions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION positions_risk_cap();

-- 4. Abgeschlossene Trades, Orders, Marktzustand -------------------------------------------
CREATE TABLE closed_trades (
    account_id    TEXT        NOT NULL REFERENCES accounts (account_id),
    position_id   TEXT        NOT NULL,
    setup_id      TEXT        NOT NULL,
    strategy      TEXT        NOT NULL,
    symbol        TEXT        NOT NULL,
    market_type   TEXT        NOT NULL,
    side          TEXT        NOT NULL,
    quantity      NUMERIC     NOT NULL,
    entry_price   NUMERIC     NOT NULL,
    exit_price    NUMERIC     NOT NULL,
    opened_at     TIMESTAMPTZ NOT NULL,
    closed_at     TIMESTAMPTZ NOT NULL,
    exit_reason   TEXT        NOT NULL,
    gross_pnl     NUMERIC     NOT NULL,
    fees          NUMERIC     NOT NULL,
    funding       NUMERIC     NOT NULL,
    net_pnl       NUMERIC     NOT NULL,
    risk_at_open  NUMERIC     NOT NULL,
    PRIMARY KEY (account_id, position_id)
);
CREATE TRIGGER closed_trades_immutable BEFORE UPDATE OR DELETE ON closed_trades
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER closed_trades_no_truncate BEFORE TRUNCATE ON closed_trades
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- Jede client_order_id wird pro Konto hoechstens EINMAL bearbeitet (auch ueber Prozesse hinweg).
CREATE TABLE orders (
    account_id      TEXT        NOT NULL REFERENCES accounts (account_id),
    client_order_id TEXT        NOT NULL,
    status          TEXT        NOT NULL CHECK (status IN ('filled', 'rejected')),
    reasons         TEXT        NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (account_id, client_order_id)
);
CREATE TRIGGER orders_immutable BEFORE UPDATE OR DELETE ON orders
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER orders_no_truncate BEFORE TRUNCATE ON orders
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

CREATE TABLE market_state (
    symbol     TEXT        PRIMARY KEY,
    mark       NUMERIC,
    last_ts    TIMESTAMPTZ,
    last_price NUMERIC,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5. Rechte der Anwendungsrolle ---------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO trading_app;
GRANT SELECT, INSERT ON event_log TO trading_app;
GRANT SELECT ON event_log_head TO trading_app;
GRANT SELECT, INSERT ON accounts TO trading_app;
GRANT UPDATE (balance, equity, peak_equity, day, day_start_equity, position_counter, version, updated_at)
    ON accounts TO trading_app;
GRANT SELECT, INSERT, UPDATE ON positions TO trading_app;
GRANT SELECT, INSERT ON closed_trades, orders TO trading_app;
GRANT SELECT, INSERT, UPDATE ON market_state TO trading_app;
