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
--     laufen nacheinander (Sperre auf der Grenzen-Zeile) - mehrere Prozesse koennen das Budget nicht gemeinsam sprengen.
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
    approved_by   TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (daily_usd <= monthly_usd)
);
INSERT INTO ai_budget_limits (id) VALUES (1);

CREATE OR REPLACE FUNCTION ai_budget_limits_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_user = 'trading_app' THEN
        -- Die Anwendung darf nur sperren (nie Grenzen aendern, nie entsperren).
        IF NEW.per_call_usd IS DISTINCT FROM OLD.per_call_usd OR NEW.daily_usd IS DISTINCT FROM OLD.daily_usd
           OR NEW.monthly_usd IS DISTINCT FROM OLD.monthly_usd OR NEW.approved_by IS DISTINCT FROM OLD.approved_by
           OR (OLD.blocked AND NOT NEW.blocked) THEN
            RAISE EXCEPTION 'KI-Budget: Grenzen setzen und entsperren darf nur ein Administrator'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    ELSIF NEW.per_call_usd IS DISTINCT FROM OLD.per_call_usd OR NEW.daily_usd IS DISTINCT FROM OLD.daily_usd
          OR NEW.monthly_usd IS DISTINCT FROM OLD.monthly_usd THEN
        IF NEW.approved_by IS NULL OR btrim(NEW.approved_by) = '' THEN
            RAISE EXCEPTION 'KI-Budget: neue Grenzen nur mit Namen der Freigabe (approved_by)'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    NEW.updated_at := now();
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
    SELECT * INTO lim FROM ai_budget_limits WHERE id = 1 FOR UPDATE;  -- serialisiert alle Reservierungen
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
