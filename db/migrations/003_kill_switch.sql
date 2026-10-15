-- =====================================================================================
-- Migration 003: Globaler Not-Aus (AP-07)
--
-- Rein ADDITIV. Eine einzige Zeile (id = 1) haelt den Zustand fuer ALLE Konten und Prozesse.
--
--   * Ausloesen (halted: false -> true) darf auch die Anwendungsrolle - jeder Prozess muss stoppen koennen.
--   * Aufheben (true -> false) darf NUR ein Administrator, mit Namen (released_by). Die Anwendung kann den Not-Aus
--     also nie selbst aufheben, auch nicht durch einen Fehler.
--   * Solange er ausgeloest ist, bleiben Grund und Zeitpunkt des ERSTEN Ausloesers stehen.
--   * Zweiter Gurt: Ist der Not-Aus ausgeloest, laesst die Datenbank keine neue Position zu (Schliessen bleibt erlaubt).
-- =====================================================================================

CREATE TABLE trading_halt (
    id          SMALLINT    PRIMARY KEY CHECK (id = 1),
    halted      BOOLEAN     NOT NULL,
    reason      TEXT        CHECK (reason IN ('MANUAL', 'DATA_ERROR', 'PROTOCOL_ERROR', 'DAILY_LOSS', 'DRAWDOWN')),
    since       TIMESTAMPTZ,
    note        TEXT        NOT NULL DEFAULT '' CHECK (length(note) <= 500),
    released_by TEXT,
    released_at TIMESTAMPTZ,
    changes     BIGINT      NOT NULL DEFAULT 0,
    CHECK (NOT halted OR (reason IS NOT NULL AND since IS NOT NULL))
);
INSERT INTO trading_halt (id, halted) VALUES (1, false);

CREATE OR REPLACE FUNCTION trading_halt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.halted AND NOT NEW.halted THEN
        IF current_user = 'trading_app' THEN
            RAISE EXCEPTION 'Not-Aus darf nur ein Administrator aufheben' USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- Name und Zeitpunkt muessen zu DIESER Freigabe gehoeren: neuer Zeitpunkt, nicht vor dem Ausloesen.
        IF NEW.released_by IS NULL OR btrim(NEW.released_by) = '' OR NEW.released_at IS NULL
           OR NEW.released_at IS NOT DISTINCT FROM OLD.released_at OR NEW.released_at < OLD.since THEN
            RAISE EXCEPTION 'Aufheben des Not-Aus nur mit Namen und Zeitpunkt' USING ERRCODE = 'check_violation';
        END IF;
        NEW.reason := NULL;  -- Grund/Zeitpunkt/Hinweis stehen weiter im Ereignisprotokoll
        NEW.since := NULL;
        NEW.note := '';
    ELSIF OLD.halted AND NEW.halted THEN
        IF NEW.reason IS DISTINCT FROM OLD.reason OR NEW.since IS DISTINCT FROM OLD.since
           OR NEW.note IS DISTINCT FROM OLD.note THEN
            RAISE EXCEPTION 'Grund und Zeitpunkt des ausgeloesten Not-Aus bleiben stehen'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    ELSIF NOT OLD.halted AND NEW.halted THEN
        IF current_user = 'trading_app' AND (NEW.released_by IS DISTINCT FROM OLD.released_by
                                             OR NEW.released_at IS DISTINCT FROM OLD.released_at) THEN
            RAISE EXCEPTION 'Freigabe-Angaben darf die Anwendung nicht setzen' USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    NEW.changes := OLD.changes + 1;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trading_halt_guard BEFORE UPDATE ON trading_halt
    FOR EACH ROW EXECUTE FUNCTION trading_halt_guard();
CREATE TRIGGER trading_halt_no_insert_delete BEFORE INSERT OR DELETE ON trading_halt
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER trading_halt_no_truncate BEFORE TRUNCATE ON trading_halt
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- Zweiter Gurt: keine neue Position bei ausgeloestem Not-Aus (fehlt die Zeile, gilt er als ausgeloest).
CREATE OR REPLACE FUNCTION positions_respect_halt() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    IF COALESCE((SELECT halted FROM trading_halt WHERE id = 1), true) THEN
        RAISE EXCEPTION 'Not-Aus ist ausgeloest: keine neue Position' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER positions_respect_halt BEFORE INSERT ON positions
    FOR EACH ROW EXECUTE FUNCTION positions_respect_halt();

GRANT SELECT ON trading_halt TO trading_app;
GRANT UPDATE (halted, reason, since, note) ON trading_halt TO trading_app;
