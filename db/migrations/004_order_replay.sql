-- =====================================================================================
-- Migration 004: Wiederholte Order liefert das urspruengliche Ergebnis (AP-08, Audit M7)
--
-- Rein ADDITIV. `orders` merkt sich zusaetzlich:
--   * fingerprint: SHA-256 ueber den vollstaendigen Auftrag (ID, Setup, Menge, Hebel). Nur ein Auftrag mit genau
--     diesem Fingerabdruck gilt als Wiederholung; gleiche ID mit anderem Inhalt bleibt ein abgelehntes Duplikat.
--   * position_id: die eroeffnete Position (nur bei 'filled').
-- Alte Eintraege behalten NULL: Ihr Ergebnis laesst sich nicht belegen, eine Wiederholung wird weiter abgelehnt.
-- Die Tabelle bleibt unveraenderlich (Trigger aus Migration 001); die Anwendung darf nur einfuegen.
-- =====================================================================================

ALTER TABLE orders
    ADD COLUMN fingerprint TEXT CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    ADD COLUMN position_id TEXT,
    ADD CONSTRAINT orders_filled_has_position CHECK (
        fingerprint IS NULL OR ((status = 'filled') = (position_id IS NOT NULL))),
    ADD CONSTRAINT orders_position_fk FOREIGN KEY (account_id, position_id) REFERENCES positions (account_id, position_id);
