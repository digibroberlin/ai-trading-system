-- =====================================================================================
-- Migration 006: Hartes KI-Budget in der Datenbank (Kostenoptimierung, Entscheidung D-016)
--
-- Rein ADDITIV.
--   * ai_budget_limits (eine Zeile): Obergrenzen je Aufruf, Tag und Monat in USD. Standard 0 = kein bezahlter Aufruf.
--     Setzen darf sie NUR ein Administrator (nach Freigabe des Eigners, B-004). Die Anwendung kann sie nicht aendern.
--   * ai_budget_entries: jede Reservierung VOR einem KI-Aufruf und ihre Abrechnung danach. Nur anhaengen; abgerechnet
--     wird genau einmal; nichts wird geloescht.
--   * Zweiter Gurt: Beim Anlegen einer Reservierung prueft die Datenbank selbst, dass Einzel-, Tages- und Monatsgrenze
--     eingehalten sind (Reservierungen zaehlen voll, abgerechnete mit den tatsaechlichen Kosten). Alle Reservierungen
--     laufen nacheinander: Jede SCHREIBT die Grenzen-Zeile (Zaehler `reservations`). Unter READ COMMITTED wartet die
--     naechste und sieht danach den neuen Verbrauch; unter REPEATABLE READ / SERIALIZABLE bricht sie mit einem
--     Serialisierungsfehler ab (eine reine Lesesperre FOR UPDATE haette dort einen veralteten Stand gezaehlt) - mehrere
--     Prozesse koennen das Budget so bei keiner Isolationsstufe gemeinsam sprengen.
--     Tag und Zeitpunkt setzt die DATENBANK (UTC), nicht die Anwendung: Eine falsch gehende Uhr kann so nicht in einen
--     anderen Tag oder Monat buchen. (Gegen eine Anwendung, die absichtlich falsche Kosten abrechnet, schuetzt der Gurt
--     nicht - er schuetzt gegen falsche Konfiguration, falsche Uhr und Wettlaeufe.)
--   * Sperre: Die Anwendung kann das Budget sperren (z. B. wenn tatsaechliche Kosten ueber der Reservierung lagen),
--     aufheben kann nur ein Administrator.
-- =====================================================================================

CREATE TABLE ai_budget_limits (
    id            SMALLINT    PRIMARY KEY CHECK (id = 1),
    per_call_usd  NUMERIC     NOT NULL DEFAULT 0 CHECK (per_call_usd >= 0),
    daily_usd     NUMERIC     NOT NULL DEFAULT 0 CHECK (daily_usd >= 0),
    monthly_usd   NUMERIC     NOT NULL DEFAULT 0 CHECK (monthly_usd >= 0),
    blocked       BOOLEAN     NOT NULL DEFAULT false,
    block_reason  TEXT        NOT NULL DEFAULT '' CHECK (length(block_reason) <= 500),
    approved_by   TEXT,        -- wer die aktuellen Grenzen freigegeben hat ...
    approved_at   TIMESTAMPTZ, -- ... und wann (jede Aenderung braucht einen NEUEN Zeitpunkt)
    unblocked_by  TEXT,
    unblocked_at  TIMESTAMPTZ,
    reservations  BIGINT      NOT NULL DEFAULT 0,  -- Zaehler; jede Reservierung schreibt ihn (Serialisierung, s. o.)
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- letzte Aenderung von Grenzen, Sperre oder Freigabe
    CHECK (daily_usd <= monthly_usd)
);
INSERT INTO ai_budget_limits (id) VALUES (1);

CREATE OR REPLACE FUNCTION ai_budget_limits_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_user = 'trading_app' THEN
        -- Die Anwendung darf nur sperren (nie Grenzen aendern, nie entsperren).
        IF NEW.per_call_usd IS DISTINCT FROM OLD.per_call_usd OR NEW.daily_usd IS DISTINCT FROM OLD.daily_usd
           OR NEW.monthly_usd IS DISTINCT FROM OLD.monthly_usd OR NEW.approved_by IS DISTINCT FROM OLD.approved_by
           OR NEW.approved_at IS DISTINCT FROM OLD.approved_at OR NEW.unblocked_by IS DISTINCT FROM OLD.unblocked_by
           OR NEW.unblocked_at IS DISTINCT FROM OLD.unblocked_at
           OR (OLD.blocked AND NOT NEW.blocked) THEN
            RAISE EXCEPTION 'KI-Budget: Grenzen setzen und entsperren darf nur ein Administrator'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    ELSE
        -- Neue Grenzen: Name UND neuer Zeitpunkt der Freigabe (eine fruehere Freigabe gilt nicht weiter).
        IF (NEW.per_call_usd IS DISTINCT FROM OLD.per_call_usd OR NEW.daily_usd IS DISTINCT FROM OLD.daily_usd
            OR NEW.monthly_usd IS DISTINCT FROM OLD.monthly_usd)
           AND (NEW.approved_by IS NULL OR btrim(NEW.approved_by) = '' OR NEW.approved_at IS NULL
                OR NEW.approved_at IS NOT DISTINCT FROM OLD.approved_at) THEN
            RAISE EXCEPTION 'KI-Budget: neue Grenzen nur mit Namen und neuem Zeitpunkt der Freigabe'
                USING ERRCODE = 'check_violation';
        END IF;
        -- Entsperren: Name UND neuer Zeitpunkt.
        IF OLD.blocked AND NOT NEW.blocked
           AND (NEW.unblocked_by IS NULL OR btrim(NEW.unblocked_by) = '' OR NEW.unblocked_at IS NULL
                OR NEW.unblocked_at IS NOT DISTINCT FROM OLD.unblocked_at) THEN
            RAISE EXCEPTION 'KI-Budget: Entsperren nur mit Namen und neuem Zeitpunkt'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF ROW(NEW.per_call_usd, NEW.daily_usd, NEW.monthly_usd, NEW.blocked, NEW.block_reason, NEW.approved_by,
           NEW.approved_at, NEW.unblocked_by, NEW.unblocked_at)
       IS DISTINCT FROM ROW(OLD.per_call_usd, OLD.daily_usd, OLD.monthly_usd, OLD.blocked, OLD.block_reason,
           OLD.approved_by, OLD.approved_at, OLD.unblocked_by, OLD.unblocked_at) THEN
        NEW.updated_at := now();
    ELSE
        NEW.updated_at := OLD.updated_at;  -- reine Reservierung: kein "geaendert"
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER ai_budget_limits_guard BEFORE UPDATE ON ai_budget_limits
    FOR EACH ROW EXECUTE FUNCTION ai_budget_limits_guard();
CREATE TRIGGER ai_budget_limits_no_insert_delete BEFORE INSERT OR DELETE ON ai_budget_limits
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER ai_budget_limits_no_truncate BEFORE TRUNCATE ON ai_budget_limits
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

CREATE TABLE ai_budget_entries (
    reservation_id TEXT        PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL,
    day            DATE        NOT NULL,
    agent          TEXT        NOT NULL,
    model          TEXT        NOT NULL,
    reserved       NUMERIC     NOT NULL CHECK (reserved >= 0),
    actual         NUMERIC     CHECK (actual >= 0),
    settled_at     TIMESTAMPTZ,
    CHECK ((actual IS NULL) = (settled_at IS NULL))
);
CREATE INDEX ai_budget_entries_day_idx ON ai_budget_entries (day);

CREATE OR REPLACE FUNCTION ai_budget_reserve_check() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    lim   ai_budget_limits%ROWTYPE;
    today NUMERIC;
    month NUMERIC;
BEGIN
    NEW.created_at := now();                           -- Zeit und Tag von der Datenbank, nicht von der Anwendung
    NEW.day := (now() AT TIME ZONE 'UTC')::date;
    -- serialisiert alle Reservierungen - SCHREIBEND, damit es auch unter REPEATABLE READ / SERIALIZABLE gilt
    UPDATE ai_budget_limits SET reservations = reservations + 1 WHERE id = 1 RETURNING * INTO lim;
    IF NOT FOUND OR lim.blocked THEN
        RAISE EXCEPTION 'KI-Budget gesperrt oder nicht vorhanden' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.actual IS NOT NULL THEN
        RAISE EXCEPTION 'KI-Budget: neue Reservierung ist noch nicht abgerechnet' USING ERRCODE = 'check_violation';
    END IF;
    SELECT COALESCE(SUM(COALESCE(actual, reserved)), 0) INTO today FROM ai_budget_entries WHERE day = NEW.day;
    SELECT COALESCE(SUM(COALESCE(actual, reserved)), 0) INTO month FROM ai_budget_entries
     WHERE day >= date_trunc('month', NEW.day)::date AND day < (date_trunc('month', NEW.day) + interval '1 month')::date;
    IF NEW.reserved > lim.per_call_usd OR today + NEW.reserved > lim.daily_usd
       OR month + NEW.reserved > lim.monthly_usd THEN
        RAISE EXCEPTION 'KI-Budget reicht nicht (Aufruf %, heute %, Monat %; Grenzen %/%/%)',
            NEW.reserved, today, month, lim.per_call_usd, lim.daily_usd, lim.monthly_usd
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER ai_budget_reserve_check BEFORE INSERT ON ai_budget_entries
    FOR EACH ROW EXECUTE FUNCTION ai_budget_reserve_check();

CREATE OR REPLACE FUNCTION ai_budget_settle_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.actual IS NOT NULL OR NEW.actual IS NULL
       OR NEW.reservation_id <> OLD.reservation_id OR NEW.created_at <> OLD.created_at OR NEW.day <> OLD.day
       OR NEW.agent <> OLD.agent OR NEW.model <> OLD.model OR NEW.reserved <> OLD.reserved THEN
        RAISE EXCEPTION 'KI-Budget: Eintraege werden genau einmal abgerechnet und sonst nie geaendert'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.settled_at := now();
    RETURN NEW;
END;
$$;
CREATE TRIGGER ai_budget_settle_once BEFORE UPDATE ON ai_budget_entries
    FOR EACH ROW EXECUTE FUNCTION ai_budget_settle_once();
CREATE TRIGGER ai_budget_entries_no_delete BEFORE DELETE ON ai_budget_entries
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER ai_budget_entries_no_truncate BEFORE TRUNCATE ON ai_budget_entries
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

GRANT SELECT ON ai_budget_limits TO trading_app;
GRANT UPDATE (blocked, block_reason) ON ai_budget_limits TO trading_app;
GRANT SELECT, INSERT ON ai_budget_entries TO trading_app;
GRANT UPDATE (actual, settled_at) ON ai_budget_entries TO trading_app;
