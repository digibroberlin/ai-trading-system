# CLAUDE.md – Arbeitsweise für Claude Code in diesem Projekt

**Verbindlich zuerst lesen:** [AGENTS.md](AGENTS.md) (Stopp-Regeln, Limits, Konventionen). Diese Datei ergänzt sie um den
selbstständigen Entwicklungsablauf. Bei Widerspruch gilt AGENTS.md. Der Eigner ist kein Programmierer: Berichte auf Deutsch,
verständlich, ehrlich (nie unausgeführte Tests als bestanden melden).

## Verbindliche Ergänzung des Eigners (2026-10-16) – gilt dauerhaft

Ergänzung zum Master-Plan, **kein** Neustart: Fortschritte, Architektur und die Aufgaben der sechs Agenten bleiben.

1. **Entwicklung vor Handelsbetrieb.** Ziel ist das technisch fertige System (MOMO, RIN, SORA, KIBO, YUZU, Paper-Executor,
   AKI). Während der Entwicklung **kein** automatischer Demo- oder Echtgeldhandel – nur isolierte Tests, Backtests und
   Simulationen.
2. **Sicherheit.** Technisch und durch automatisierte Tests sicherstellen, dass **keine echte Börsenorder** ausgelöst werden
   kann (`tests/test_no_real_trading.py`, AP-34). Handel ist **standardmäßig aus**. Vorhandene API-Konfigurationen werden auf
   Handelsberechtigungen geprüft, **ohne Geheimnisse auszugeben** (`python -m trading.safety.credentials_check`, AP-34).
   Demo-Handel nur nach **ausdrücklicher Freigabe des Eigners**; echter Handel braucht eine **separate** Freigabe und
   zusätzliche Sicherheitsprüfungen (im Code gibt es heute keinen Weg dorthin).
3. **GitHub-Checkpoints** nach jedem Arbeitspaket: relevante Tests → Fehler beheben → Ergebnisse dokumentieren
   (TESTBERICHT, PROJECT_STATUS) → Geheimnis-Scan → Commit → Push auf den Arbeitszweig und nach `develop`.
   **STABLE** heißt nur ein vollständig geprüfter Stand (Preflight STABIL **und** volle Gegenprobe mit Datenbank).
   Unfertiges nur als **WIP** und ausdrücklich als ungetestet gekennzeichnet. **`main` wird nie eigenständig überschrieben
   oder verändert** – auch kein Zusammenführen ohne Rückmeldung des Eigners (siehe Schritt 7).
4. **Selbstständig** nach `docs/MASTER_PLAN.md` weiterarbeiten; Architektur und Agentenrollen nicht unnötig ändern; nach
   jedem Paket automatisch das nächste offene; bereits geprüfte Komponenten nicht wiederholt umfangreich prüfen; neue
   kritische Fehler immer beheben und testen. Vorrang: Kernfunktionen, Agenten-Integration, Strategien, Backtesting,
   Börsen-Adapter, Dashboard.
5. **Profitabilität und Kosten.** Ziel ist ein hoher, robuster, risikobereinigter **Nettogewinn nach sämtlichen Kosten**
   (Gebühren, Spread, Slippage, Funding, KI, Server). KI-Tokens sparsam: Regeln zuerst, günstigste Modellstufe, nur bei
   belegtem Nutzen. **Keine Strategie gilt ohne belastbare Tests als profitabel.**
6. **Fortschritt und Wiederaufnahme.** Nach jedem Paket PROJECT_STATUS und MASTER_PLAN aktualisieren: was abgeschlossen
   ist, welche Tests bestanden, **welcher Git-Checkpoint zuletzt gesichert wurde**, was fehlt, was als Nächstes folgt –
   so, dass eine neue Sitzung nahtlos weitermachen kann.
7. **Abschluss.** Die Software so weit wie möglich selbstständig fertigstellen; den Eigner nur bei Konten,
   Serverbuchungen, API-Zugängen oder sicherheitskritischen Freigaben ansprechen. Ist alles fertig und getestet:
   stoppen und melden **„SOFTWARE BEREIT FÜR GEMEINSAME DEMO-EINRICHTUNG.“** – weder Demo- noch Echtgeldhandel selbst starten.

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
7. Ist ein Meilenstein erreicht und `python scripts/preflight.py --mutation --record` meldet **STABIL**: den geprüften
   `develop`-Stand als STABLE kennzeichnen – annotierter Tag `stable/NNN-kurzname` (Nachricht: was, Testergebnis) und
   Eintrag in PROJECT_STATUS („Letzter STABLE-Stand: Commit …“). **`main` nicht selbst verändern** (Ergänzung 2026-10-16,
   Punkt 3): Der Eigner führt den STABLE-Stand nach `main` zusammen oder gibt dafür ausdrücklich frei.
8. Weiter mit Schritt 1.

## Git-Regeln

* `main` enthält nur STABILE Stände und wird von Claude **nie eigenständig** verändert (kein Commit, kein Zusammenführen ohne
  Freigabe, nie `--force` auf `main`, `develop` oder Tags).
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

## Arbeitsort: Cloud (seit 2026-10-15, Entscheidung D-008)

Maßgeblich ist GitHub (`digibroberlin/ai-trading-system`). Gearbeitet wird in Cloud-Sitzungen und der stündlichen
Cloud-Routine (D-009). Der Mac-Ordner ist nur noch Sicherung. In einer Cloud-Sitzung zu Beginn:

```bash
python3.12 -m venv /tmp/venv && /tmp/venv/bin/pip install -q --require-hashes -r requirements/dev.lock
bash scripts/cloud_test_db.sh && set -a && source /tmp/ai-trading-test-db.env && set +a   # Wegwerf-PostgreSQL
/tmp/venv/bin/python scripts/preflight.py
```
Geprüft am 2026-10-15 (AP-03): `python3` ist in der Cloud 3.11 – deshalb ausdrücklich `python3.12`. Python 3.9 für die
Kernprüfung gibt es über `uv venv -p 3.9`. `scripts/cloud_test_db.sh` läuft als root ohne Rückfrage (ca. 1 min).
Die Gegenprobe dauert in der Cloud rund 40 min (Mac: ca. 11 min).
Lässt sich in der Cloud keine Datenbank einrichten, meldet `preflight.py` ehrlich NICHT STABIL (Datenbank-Tests übersprungen):
dann nur nach `develop` zusammenführen, **keine** `stable/*`-Tags setzen und den Umstand in `docs/BLOCKERS.md` festhalten.
GitHub Actions prüft `main`, `develop` und `stable/*` zusätzlich mit PostgreSQL. Lässt sich ein Push auf `develop` nicht
ausführen (Rechte der Cloud-Sitzung), die Arbeit auf dem Arbeitszweig pushen und in `docs/BLOCKERS.md` vermerken.

## Wichtige Befehle

```bash
set -a; source <Passwort-Datei außerhalb des Projekts>; set +a
export TEST_ADMIN_URL="postgresql://trading:${POSTGRES_PASSWORD}@127.0.0.1:5432/postgres" TEST_APP_PASSWORD="$APP_DB_PASSWORD"
python scripts/preflight.py            # alle Prüfungen; STABIL nur ohne übersprungene Tests
python scripts/mutation_check.py       # Gegenprobe (ca. 15 min)
python scripts/secret_scan.py --history
python scripts/dependency_audit.py     # bekannte Lücken in Abhängigkeiten (braucht Netz + requirements/audit.lock)
docker-compose --env-file <Passwort-Datei> run --rm tests
```
Compose heißt auf diesem Mac `docker-compose` (Colima), nicht `docker compose`. In zsh Befehle nie über Variablen wie
`$DC` aufrufen (keine Wort-Trennung) – Shell-Funktionen verwenden.
