# CLAUDE.md – Arbeitsweise für Claude Code in diesem Projekt

**Verbindlich zuerst lesen:** [AGENTS.md](AGENTS.md) (Stopp-Regeln, Limits, Konventionen). Diese Datei ergänzt sie um den
selbstständigen Entwicklungsablauf. Bei Widerspruch gilt AGENTS.md. Der Eigner ist kein Programmierer: Berichte auf Deutsch,
verständlich, ehrlich (nie unausgeführte Tests als bestanden melden).

## Grundregel: ENTWICKELN, NICHT HANDELN

* Kein Demo-, Paper- oder Echtgeld-**Betrieb** starten, keinen dauerhaften Bot aktivieren, keine Börsenorders, keine
  Handels-API-Schlüssel anlegen oder verwenden, keine kostenpflichtigen Dienste ohne Zustimmung.
* Erlaubt: Code, Tests mit synthetischen Daten, einzelne simulierte Orders in isolierten Tests, Backtests, öffentliche
  historische Marktdaten **nur lesend** für technische Validierung, Dashboard lokal mit synthetischen Daten.

## Zu Beginn jeder Sitzung

1. `docs/PROJECT_STATUS.md` lesen (aktuelle Aufgabe, letzter stabiler Stand), dann `docs/BLOCKERS.md` und `docs/MASTER_PLAN.md`.
2. `git status`, `git log --oneline -5`, `git tag -l "stable/*"` – stimmt der Stand mit PROJECT_STATUS überein?
3. Prüfen, ob eine andere Sitzung gerade arbeitet: `docs/PROJECT_STATUS.md` → Abschnitt „Sperre“. Ist dort eine fremde, weniger
   als 3 Stunden alte Sperre eingetragen, **nicht** am selben Arbeitspaket weiterarbeiten.

## Entwicklungsablauf (je Arbeitspaket)

1. Nächstes offenes Arbeitspaket aus `docs/MASTER_PLAN.md` wählen (Reihenfolge = Priorität; blockierte überspringen).
2. Zweig anlegen: `git switch -c ap/<nummer>-<kurzname> develop`. In PROJECT_STATUS die Sperre eintragen.
3. Bestehenden Code lesen, umsetzen, Tests schreiben (bei Sicherheitslogik mit Gegenprobe in `scripts/mutation_check.py`).
4. Prüfen: `python scripts/preflight.py` (mit Datenbank-Variablen, siehe README). Fehler analysieren und beheben – nie Tests
   abschwächen. Nach dem dritten erfolglosen Versuch am selben Fehler: in `docs/BLOCKERS.md` dokumentieren, anderes Paket wählen.
5. Doku aktualisieren (PROJECT_STATUS, MASTER_PLAN-Status, ggf. DECISIONS, README „Bekannte Grenzen“).
6. Commit auf dem Arbeitszweig, dann nach `develop` zusammenführen (`git merge --no-ff`), Arbeitszweig löschen, pushen.
7. Ist ein Meilenstein erreicht und `python scripts/preflight.py --mutation --record` meldet **STABIL**:
   `develop` nach `main` zusammenführen, annotierten Tag `stable/NNN-kurzname` setzen (Nachricht: was, Testergebnis), pushen.
8. Weiter mit Schritt 1.

## Git-Regeln

* `main` enthält nur STABILE Stände. Nie direkt auf `main` committen, nie `--force` auf `main`, `develop` oder Tags.
* Unfertiges darf auf Arbeitszweigen gesichert werden (Commit-Nachricht beginnt mit `WIP:`) – es gilt nie als stabil.
* Vor jedem Push: `python scripts/secret_scan.py --history` muss „keine Funde“ melden. Nie `.env`, Passwörter, Schlüssel,
  Datenbank-Abzüge oder `backups/` einchecken.
* Commit-Nachrichten auf Deutsch; letzte Zeile: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Wann der Eigner gebraucht wird (sonst nicht fragen)

Nur bei: Konto/Anmeldung/Verbindung (GitHub, Server, Börse), kostenpflichtigen Diensten, sensiblen Berechtigungen,
unumkehrbaren Aktionen, Lockerung einer Regel aus AGENTS.md §2, Start des Demo-Handels. Dann kurz erklären:
**WAS** ist zu tun, **WO**, **WIE**, **WAS passiert danach**. Nie Passwörter oder private Schlüssel im Chat anfordern.
Offene Punkte stehen in `docs/BLOCKERS.md`; bis zur Antwort an unabhängigen Paketen weiterarbeiten.

## Nutzungslimit oder Abbruch

Vor längeren Schritten und spätestens bei Anzeichen eines Limits: PROJECT_STATUS mit vollständigem Zwischenstand
aktualisieren (was erledigt, was halb, nächster konkreter Schritt), WIP-Commit auf dem Arbeitszweig, pushen.
Limits nie umgehen, keine kostenpflichtige Mehrnutzung aktivieren.

## Wichtige Befehle

```bash
set -a; source <Passwort-Datei außerhalb des Projekts>; set +a
export TEST_ADMIN_URL="postgresql://trading:${POSTGRES_PASSWORD}@127.0.0.1:5432/postgres" TEST_APP_PASSWORD="$APP_DB_PASSWORD"
python scripts/preflight.py            # alle Prüfungen; STABIL nur ohne übersprungene Tests
python scripts/mutation_check.py       # Gegenprobe (ca. 15 min)
python scripts/secret_scan.py --history
docker-compose --env-file <Passwort-Datei> run --rm tests
```
Compose heißt auf diesem Mac `docker-compose` (Colima), nicht `docker compose`. In zsh Befehle nie über Variablen wie
`$DC` aufrufen (keine Wort-Trennung) – Shell-Funktionen verwenden.
