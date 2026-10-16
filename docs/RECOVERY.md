# Wiederherstellung – zu einem früheren, funktionierenden Stand zurück

Jeder stabile Stand hat ein Etikett (Git-Tag) der Form `stable/NNN-name`. Es wurde nur gesetzt, nachdem alle Prüfungen
bestanden waren (`scripts/preflight.py` → STABIL). Die Liste mit Datum und Testergebnis steht in `docs/TEST_RESULTS.md`
und in den Tag-Nachrichten.

## 1. Welche stabilen Stände gibt es?

```bash
git fetch --tags                       # nur nötig, wenn GitHub eingerichtet ist
git tag -l "stable/*" -n3              # Liste mit Beschreibung und Testergebnis
```

## 2. Einen stabilen Stand ansehen oder benutzen (nichts geht verloren)

```bash
git switch -c rueckkehr/002 stable/002-stage1-core-backtesting   # neuer Zweig genau auf diesem Stand
```
Der bisherige Stand bleibt auf `develop` und `main` erhalten. Zurück: `git switch develop`.

## 3. `develop` endgültig auf einen stabilen Stand zurücksetzen

Nur wenn die Arbeit danach wirklich verworfen werden soll. Vorher wird der aktuelle Stand gesichert:
```bash
git branch sicherung/vor-ruecksetzung-$(date +%F) develop
git switch develop && git reset --hard stable/002-stage1-core-backtesting
git push --force-with-lease origin develop    # nur develop – main und Tags werden nie überschrieben
```

## 4. Komplett neu von GitHub (z. B. neuer Rechner)

```bash
git clone git@github-ai-trading:<IHR-NUTZERNAME>/ai-trading-system.git
cd ai-trading-system && git switch --detach stable/<NNN-name>
```
Danach wie im README: Passwort-Datei außerhalb des Projekts anlegen, `docker-compose ... up -d --build db`,
`docker-compose ... run --rm migrate`, Tests laufen lassen.

## 5. Lokale Sicherungen (ohne GitHub)

* `backups/git-vor-bereinigung_2026-10-15.bundle` – die vollständige Git-Geschichte vor der Bereinigung am 2026-10-15
  (damals wurden zwei Datenbank-Abzüge aus der Geschichte entfernt). Wiederherstellen:
  `git clone backups/git-vor-bereinigung_2026-10-15.bundle /pfad/ausserhalb`.
* `backups/projekt_2026-10-13_vor-stufe1.tar.gz` – Projektordner vor Stufe 1.
* `backups/datenbank_*.sql` – Abzüge der lokalen Datenbank `trading`.

## 6. Datenbank wiederherstellen

**Seit AP-26 (D-028):** Sicherungen entstehen mit `python scripts/backup.py create` (Datei `backups/trading-<Zeit>.dump`
plus `.sha256` und `.json`) und werden mit `python scripts/backup.py verify backups/trading-<Zeit>.dump` geprüft: Der
Rückspiel-Test spielt sie in eine Wegwerf-Datenbank ein und vergleicht Zeilen, Schema-Stand und Ereigniskette. Eine
bestandene Sicherung wird – wie unten – nur in eine **neue, leere** Datenbank zurückgespielt:
```bash
createdb trading_wiederhergestellt            # als Datenbank-Administrator
pg_restore --exit-on-error --dbname trading_wiederhergestellt backups/trading-<Zeit>.dump
```

Ältere Abzüge (`.sql`): Die Datenbank enthält nur Paper-Daten. Ein Abzug wird in eine **neue, leere** Datenbank eingespielt – nie über die bestehende:
```bash
docker exec ai-trading-system-db-1 createdb -U trading trading_wiederhergestellt
docker exec -i ai-trading-system-db-1 psql -U trading -d trading_wiederhergestellt < backups/datenbank_<datum>.sql
```
Erst nach Prüfung (`scripts/restart_check.py verify`) wird entschieden, ob sie die alte ersetzt.

## 7. Was niemals passieren darf

* `git push --force` auf `main` oder auf `stable/*`-Tags.
* `docker-compose down -v` oder `colima delete` ohne ausdrückliche Anweisung des Eigners (löscht die Datenbank).
