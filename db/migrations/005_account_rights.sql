-- =====================================================================================
-- Migration 005: Rechte der Anwendungsrolle am Konto (AP-10, Audit M2 Rest)
--
-- 1. Konten legt nur noch ein Administrator an (trading.persistence.accounts). Jedes Konto hat ein
--    eigenes 2-%-Risikobudget - koennte die Anwendung selbst Konten anlegen, koennte sie ihr Budget
--    beliebig vervielfachen.
-- 2. Den Kontostand muss die Anwendung weiter schreiben (jeder Handel aendert ihn). Die Datenbank
--    prueft aber jede Aenderung durch einen Nicht-Administrator auf Plausibilitaet:
--      * genau ein Versionsschritt je Aenderung (version + 1),
--      * der Positionszaehler laeuft nur vorwaerts (keine Wiederverwendung von Positionsnummern),
--      * der Handelstag laeuft nur vorwaerts; innerhalb eines Tages bleibt die Tagesstart-Equity fest
--        (sonst liesse sich die Tagesverlust-Notbremse zuruecksetzen),
--      * der Hoechststand faellt nie und liegt nie unter der Equity (sonst waere die Drawdown-Bremse
--        wirkungslos); zuruecksetzen darf ihn nur die Freigabe des Not-Aus durch einen Administrator.
--    Eine Aenderung, die nichts aendert (Sperr-Trick im Risiko-Gurt aus Migration 002), bleibt erlaubt.
-- Rein additiv: keine Daten werden veraendert.
-- =====================================================================================

REVOKE INSERT ON accounts FROM trading_app;

CREATE OR REPLACE FUNCTION accounts_app_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW IS NOT DISTINCT FROM OLD THEN
        RETURN NEW;  -- reine Sperre (UPDATE ... SET version = version), aendert nichts
    END IF;
    IF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
        RETURN NEW;  -- Administrator (z. B. Freigabe des Not-Aus setzt die Bezugswerte bewusst neu)
    END IF;
    IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'Konto %: Version muss genau um 1 steigen (% -> %)', OLD.account_id, OLD.version, NEW.version
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.position_counter < OLD.position_counter THEN
        RAISE EXCEPTION 'Konto %: Positionszaehler darf nicht sinken', OLD.account_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.day < OLD.day THEN
        RAISE EXCEPTION 'Konto %: Handelstag darf nicht zurueckspringen', OLD.account_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.day = OLD.day AND NEW.day_start_equity <> OLD.day_start_equity THEN
        RAISE EXCEPTION 'Konto %: Tagesstart-Equity ist innerhalb eines Tages fest', OLD.account_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.peak_equity < OLD.peak_equity OR NEW.peak_equity < NEW.equity THEN
        RAISE EXCEPTION 'Konto %: Hoechststand darf nicht sinken und nicht unter der Equity liegen', OLD.account_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER accounts_app_guard BEFORE UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION accounts_app_guard();
