# Sicherungen

Dieser Ordner enthaelt lokale Sicherungen (Projekt-Archive, Datenbank-Abzuege, Git-Buendel).
Sie werden **nicht** in Git aufgenommen und **nicht** zu GitHub hochgeladen (`.gitignore`).

Datenbank-Sicherungen mit Rückspiel-Test (AP-26):
`ADMIN_DATABASE_URL=... python scripts/backup.py create` → `trading-<Zeit>.dump` (+ `.sha256`, `.json`, nur für den
Eigentümer lesbar), dann `python scripts/backup.py verify backups/trading-<Zeit>.dump`; ältere entfernen mit
`python scripts/backup.py prune --keep 14`.

Wiederherstellung: siehe `docs/RECOVERY.md`.
