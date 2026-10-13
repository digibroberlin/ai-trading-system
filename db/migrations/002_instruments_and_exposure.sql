-- =====================================================================================
-- Migration 002: Instrument-Schluessel, Zustand je Instrument, Exposure-Limit, haertere Trigger
--
-- Rein ADDITIV: Es wird keine Zeile und keine Tabelle geloescht. Bestehende Positionen und Trades
-- stammen aus synthetischen Daten und erhalten die Boerse 'synthetic'.
--
--   H1  Jede Position / jeder Trade / jeder Bewertungskurs gehoert zu (Boerse, Marktart, Handelspaar).
--   H3  Je Instrument wird gespeichert, bis wohin Kerzen verarbeitet sind (Schutz vor Wiederholungen).
--   H5  Zweiter Gurt in der Datenbank auch fuer das Brutto-Exposure (100 %).
--   M1  Der Limit-Trigger schreibt die Konto-Zeile: Unter REPEATABLE READ/SERIALIZABLE scheitert damit
--       jede Transaktion, die einen veralteten Stand sieht (vorher liess sich das Limit so umgehen).
--   M2  Die Anwendungsrolle darf Risiko-Stammdaten offener Positionen nicht mehr umschreiben.
-- =====================================================================================

-- 1. Boerse als Teil des Instrument-Schluessels -------------------------------------------
ALTER TABLE positions ADD COLUMN exchange TEXT NOT NULL DEFAULT 'synthetic'
    CHECK (exchange IN ('synthetic', 'coinbase', 'bitget'));
ALTER TABLE positions ALTER COLUMN exchange DROP DEFAULT;

ALTER TABLE closed_trades ADD COLUMN exchange TEXT NOT NULL DEFAULT 'synthetic'
    CHECK (exchange IN ('synthetic', 'coinbase', 'bitget'));
ALTER TABLE closed_trades ALTER COLUMN exchange DROP DEFAULT;

-- Pro INSTRUMENT hoechstens eine offene Position (bisher: pro Symbol und Marktart, ohne Boerse).
DROP INDEX positions_one_open_per_market;
CREATE UNIQUE INDEX positions_one_open_per_instrument
    ON positions (account_id, exchange, market_type, symbol) WHERE status = 'OPEN';

-- 2. Marktzustand je Instrument -----------------------------------------------------------
CREATE TABLE instrument_state (
    exchange          TEXT        NOT NULL CHECK (exchange IN ('synthetic', 'coinbase', 'bitget')),
    market_type       TEXT        NOT NULL CHECK (market_type IN ('spot', 'futures')),
    symbol            TEXT        NOT NULL,
    mark              NUMERIC     CHECK (mark > 0),       -- letzter Bewertungskurs ...
    mark_ts           TIMESTAMPTZ,                        -- ... und sein Zeitpunkt (Aktualitaetspruefung)
    ref_price         NUMERIC     CHECK (ref_price > 0),  -- letzter gueltiger Kurs fuer den Sprung-Filter
    ref_ts            TIMESTAMPTZ,
    last_candle_close TIMESTAMPTZ,                        -- bis hierhin sind Kerzen verarbeitet
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (exchange, market_type, symbol)
);

-- Bisher galt ein Kurs je Symbol fuer Spot UND Futures gemeinsam; er wird fuer beide uebernommen.
INSERT INTO instrument_state (exchange, market_type, symbol, mark, mark_ts, ref_price, ref_ts)
SELECT 'synthetic', m.market_type, s.symbol, s.mark, s.updated_at, s.last_price, s.last_ts
  FROM market_state s CROSS JOIN (VALUES ('spot'), ('futures')) AS m(market_type);

COMMENT ON TABLE market_state IS
    'VERALTET seit Migration 002 (ersetzt durch instrument_state). Wird nicht mehr gelesen oder geschrieben.';
REVOKE INSERT, UPDATE ON market_state FROM trading_app;

-- 3. Exposure-Limit am Konto (eingefroren wie das Risikolimit) ------------------------------
ALTER TABLE accounts ADD COLUMN max_gross_exposure NUMERIC NOT NULL DEFAULT 1.0
    CHECK (max_gross_exposure > 0 AND max_gross_exposure <= 1.0);

CREATE OR REPLACE FUNCTION accounts_freeze() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.mode <> OLD.mode OR NEW.max_portfolio_risk <> OLD.max_portfolio_risk
       OR NEW.max_gross_exposure <> OLD.max_gross_exposure
       OR NEW.initial_equity <> OLD.initial_equity OR NEW.account_id <> OLD.account_id THEN
        RAISE EXCEPTION 'Modus, Limits und Startkapital eines Kontos sind unveraenderlich'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;

-- 4. Zweiter Gurt beim COMMIT: Gesamtrisiko UND Brutto-Exposure ----------------------------
-- Geprueft wird der Zustand NACH der Order gegen die Equity nach Kosten - dieselbe Rechnung wie in
-- trading.safety.order_risk. (Der Stress-Test wird nur in der Anwendung gerechnet, unter der Konto-Sperre.)
CREATE OR REPLACE FUNCTION positions_risk_cap() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    acc       accounts%ROWTYPE;
    remaining NUMERIC;
    exposure  NUMERIC;
BEGIN
    -- Schreibzugriff auf die Konto-Zeile (ohne Schluesselspalten: kollidiert nicht mit den
    -- Fremdschluessel-Sperren anderer Transaktionen). Er reiht konkurrierende Transaktionen ein, und
    -- unter REPEATABLE READ/SERIALIZABLE bricht er ab, wenn das Konto inzwischen veraendert wurde.
    UPDATE accounts SET version = version WHERE account_id = NEW.account_id RETURNING * INTO acc;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Konto % existiert nicht', NEW.account_id USING ERRCODE = 'foreign_key_violation';
    END IF;

    SELECT COALESCE(SUM(risk_at_open - entry_fee), 0) INTO remaining
      FROM positions WHERE account_id = NEW.account_id AND status = 'OPEN';
    SELECT COALESCE(SUM(p.quantity * COALESCE(s.mark, p.entry_price)), 0) INTO exposure
      FROM positions p
      LEFT JOIN instrument_state s
        ON s.exchange = p.exchange AND s.market_type = p.market_type AND s.symbol = p.symbol
     WHERE p.account_id = NEW.account_id AND p.status = 'OPEN';

    IF acc.equity <= 0 OR remaining > acc.max_portfolio_risk * acc.equity THEN
        RAISE EXCEPTION 'Gesamtrisiko-Limit verletzt: Restrisiko % > % x Eigenkapital %',
            remaining, acc.max_portfolio_risk, acc.equity USING ERRCODE = 'check_violation';
    END IF;
    IF exposure > acc.max_gross_exposure * acc.equity THEN
        RAISE EXCEPTION 'Exposure-Limit verletzt: Positionswert % > % x Eigenkapital %',
            exposure, acc.max_gross_exposure, acc.equity USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

-- 5. Rechte der Anwendungsrolle ------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON instrument_state TO trading_app;
-- Offene Positionen: nur Funding fortschreiben und schliessen. Menge, Kurse, Stop und Risiko sind fest.
REVOKE UPDATE ON positions FROM trading_app;
GRANT UPDATE (status, closed_at, funding_paid, last_funding_ts) ON positions TO trading_app;
