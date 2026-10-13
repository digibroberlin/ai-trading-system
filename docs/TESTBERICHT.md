# Testbericht – Phasen 1 bis 3 (Grundgerüst, SORA/RIN, PostgreSQL & Mehrprozess-Sicherheit)

Stand: 2026-10-13 · Daten: ausschließlich synthetisch · Modus: ausschließlich PAPER · keine Börsenverbindung

## 1. Auf einen Blick

| Prüfung | Ergebnis | Umgebung |
|---|---|---|
| **Alle Tests** | **354 von 354 bestanden**, 0 übersprungen | Mac, Python **3.12.14**, PostgreSQL **16.15** (Docker/Colima) |
| **Alle Tests im Container** | **354 von 354 bestanden**, 0 übersprungen | Docker-Image `python:3.12-slim` (Python **3.12.15**, Linux/arm64) gegen PostgreSQL 16 |
| Datenbank-Tests 3× wiederholt | 3 von 3 Läufen grün (je 58 Tests; der 59. kam danach hinzu) | Mac, Python 3.12 |
| Tests ohne Datenbank (zum Vergleich) | 295 bestanden, **59 übersprungen** (sichtbar als „skipped“) | Python 3.9, keine DB konfiguriert |
| Gegenproben: absichtlich eingebaute Fehler | **alle 27 entdeckt** (5 + 12 + 10) | siehe Abschnitt 4 |
| mypy `--strict` (42 Dateien), ruff | keine Fehler | Python 3.12 |
| Demo (`python -m trading.demo`) im Container | läuft, Protokoll-Kette intakt | Docker |
| Neustart-Prüfungen (offene Paper-Positionen) | **alle bestanden** (12 Prüfungen, siehe Abschnitt 3.5) | Docker/Colima |

Tests nach Datei (Summe 354): `test_rin` 62 · `test_sora` 40 · `test_paper_executor` 34 · `test_limits_and_config` 29 · `test_risk_manager` 28 ·
`test_pg_schema` 27 · `test_concurrency` 22 · `test_pipeline_and_agents` 22 · `test_data_validator` 18 · `test_event_log` 14 · `test_pg_multiprocess` 13 ·
`test_pg_executor` 11 · `test_rin_sora_pipeline` 10 · `test_stats_and_synthetic` 9 · `test_pg_restart` 8 · `test_no_real_trading` 7.

## 2. Was in Phase 3 eingerichtet wurde (alles mit Ihrer Freigabe)

**Installiert über Homebrew** (alles in `/opt/homebrew`, ohne Administrator-Rechte, kostenlos): Python **3.12.14** (+ `mpdecimal`), **Colima 0.10.3** (+ `lima` 2.2.0),
**Docker CLI 29.8.1**, **docker-compose 5.5.1**. System-Python 3.9 und `python3` im Terminal sind **unverändert**.

**Außerhalb des Projektordners entstanden:** `/opt/homebrew/Cellar/…` (ca. 215 MB), `~/.colima` (2,8 GB, die Docker-VM), `~/.docker/config.json` (Colima setzt den Docker-Kontext
„colima“; das Verzeichnis gab es vorher nicht). Docker-Images: `postgres:16` (663 MB), `python:3.12-slim` (205 MB) und die zwei gebauten Projekt-Images; ein Daten-Volume `ai-trading-system_pgdata`.
Test-Umgebungen und die Passwort-Datei liegen im temporären Session-Ordner, **nicht** in OneDrive. Andere Projekte wurden nicht berührt; es entstanden keine Kosten.

**Rückgängig machen:** `colima delete` (löscht VM, Images, Daten) · `brew uninstall python@3.12 colima docker docker-compose` · `rm -rf ~/.docker ~/.colima`.

## 3. Ergebnisse der einzelnen Aufgaben

### 3.1 Docker-Container und PostgreSQL gestartet
PostgreSQL 16.15 läuft im Container, gebunden nur an `127.0.0.1:5432`. Beim ersten Start wurden `db/00_roles.sh` (Anwendungsrolle `trading_app`, Passwort aus der Umgebung) und `db/schema.sql`
fehlerfrei ausgeführt: 7 Tabellen, 16 Trigger. Der App-Container verbindet sich als `trading_app` (kein Superuser).

### 3.2 Alle Tests mit Python 3.12
354/354 auf dem Mac **und** im Container (siehe oben). Die bisherigen 293 Tests liefen vorab unter 3.12.14 ebenfalls ohne Änderung am Code.

### 3.3 PostgreSQL-Trigger und AKI-Logging (`test_pg_schema.py`, 27 Tests)
* **Unveränderlichkeit:** `UPDATE`, `DELETE`, `TRUNCATE` auf `event_log` werden auch vom **Administrator** abgelehnt (Trigger); die Anwendungsrolle darf zusätzlich weder `DROP` noch `ALTER … DISABLE TRIGGER`.
* **Datenbank prüft jede neue Zeile selbst:** falsche Sequenznummer, falsche Verkettung, falscher Hash, abweichender Inhalt (`payload` ≠ `payload_json`) → abgelehnt. Die Datenbank berechnet den SHA-256-Hash mit demselben Ergebnis wie Python.
* **Kettenende-Zeiger** (`event_log_head`) kann nicht direkt geändert werden (nur aus dem Insert-Trigger heraus).
* **Manipulation durch einen Superuser** (Trigger abschalten, Inhalt ändern oder die letzten Einträge löschen) wird von `verify()` **erkannt**, auch das Entfernen der letzten Einträge (Zeiger-Abgleich).
* **Gleichzeitiges Schreiben** aus 4 Verbindungen × 15 Einträge ergibt eine lückenlose Kette 1…60.
* **Konto:** nur `mode='paper'`, `max_portfolio_risk ≤ 0,02` (entspricht der Python-Obergrenze), Modus/Limit/Startkapital nach dem Anlegen unveränderlich; die Anwendungsrolle darf diese Spalten nicht ändern.
* **Positionen:** höchstens eine offene je Markt (Unique-Index), geschlossene Positionen/Orders/Handelsergebnisse unveränderlich, kein `DELETE`/`TRUNCATE`/DDL für die Anwendungsrolle.
* **AKI-Protokollierung** (`test_pg_executor.py`, `test_pg_multiprocess.py`): Handel und Protokolleintrag werden **in derselben Transaktion** gespeichert; blockierte Orders stehen mit Gründen und Setup im Protokoll; Executor- und AKI-Einträge
  aus verschiedenen Prozessen liegen in **einer** gültigen Kette.

### 3.4 2-%-Teamrisikolimit über mehrere Prozesse (`test_pg_multiprocess.py`, 13 Tests; `test_pg_executor.py`, 11 Tests)
Absicherung in **drei unabhängigen Ebenen**: (1) Zeilensperre auf dem Konto (`SELECT … FOR UPDATE`) zu Beginn jeder Transaktion; (2) Constraint-Trigger, der beim COMMIT das Restrisiko aller offenen Positionen prüft;
(3) Primärschlüssel/Unique-Index gegen doppelte Positions- und Order-Nummern. Ergebnisse mit **echten, getrennten Betriebssystem-Prozessen** (alle starten zur selben Uhrzeit, die Risikoprüfung wird künstlich verlangsamt, damit Wettläufe auftreten würden):

| Szenario | Ergebnis |
|---|---|
| 12 Prozesse, 12 verschiedene Märkte, je 0,45 % Risiko (3 Wiederholungen) | **genau 4** gefüllt, 8 abgelehnt, Restrisiko ≤ 2 % |
| 8 Prozesse, **gleiche Order-ID** (3 Wiederholungen) | genau 1 Ausführung, 7× `DUPLICATE_ORDER_ID` |
| 8 Prozesse, **gleicher Markt**, verschiedene IDs (3 Wiederholungen) | genau 1 Position, 7× `DUPLICATE_POSITION` |
| 4 Prozesse × 5 Orders | genau 4 gefüllt, 20 Orders erfasst, Kette gültig |
| 8 komplette Pipelines (RIN + SORA + Executor + AKI) in 8 Prozessen | 1–5 Positionen (Limit 5), Restrisiko ≤ 2 %, jede Blockade mit Grund, 8 Vorschläge protokolliert, Kette gültig |
| Prozess wird mit `SIGKILL` **mitten in der Transaktion** beendet | **keine** Spuren (keine Position, Order, Protokolleinträge; Konto unverändert); Sperre sofort frei; dieselbe Order-ID gelingt danach |
| Prozess committet und endet | Position bleibt erhalten |
| Zwei offene Transaktionen **ohne jede Client-Sperre** | die zweite scheitert beim COMMIT am Datenbank-Trigger |
| Datenbankfehler bei der Ausführung innerhalb der Pipeline | Setup wird als `EXECUTION_ERROR` blockiert, nichts gespeichert |

Zusätzlich: Der DB-Executor liefert **dieselben Ergebnisse wie die Speicher-Engine** (Parität: Status, Gründe, Positionsnummern, Gewinne, Gebühren, Funding, Guthaben, Margin); Dezimalwerte bleiben beim Speichern exakt.

### 3.5 Neustart – bleiben offene Paper-Trades erhalten?  **Ja.**
| Art des Neustarts | Beleg | Ergebnis |
|---|---|---|
| Neues Python-Objekt/neue Verbindung (Test) | Positionen, Guthaben, Margin, Hoechststand, offenes Risiko identisch | bestanden |
| Neuer **Betriebssystem-Prozess** liest den Zustand | identisch | bestanden |
| Lauf **mit** Neustart in der Mitte vs. ununterbrochener Lauf (Speicher-Kontrolle) | Gewinne, Gebühren, Funding, Ausstiegskurse, Guthaben identisch | bestanden |
| Order-IDs nach Neustart | bleiben gesperrt (`DUPLICATE_ORDER_ID`) | bestanden |
| Kurssprung-Filter nach Neustart | merkt sich den letzten gültigen Kurs | bestanden |
| Neustart mit „falschem“ Startkapital | bestehendes Konto wird **nicht** überschrieben | bestanden |
| Hash-Kette über Neustarts | `seq` und `prev_hash` setzen lückenlos fort | bestanden |
| `docker-compose restart db` | PostgreSQL-Startzeit änderte sich (19:22 → 19:30) | Zustand identisch |
| `stop` + `start` | Startzeit geändert, Status zwischendurch `exited` | identisch |
| `down` + `up` (Container gelöscht, Volume bleibt) | **neue Container-ID** | identisch |
| **Gesamte Docker-VM** (`colima stop` / `start`) | DB-Container kam durch `restart: unless-stopped` **von selbst** zurück | identisch |
| **Harter Absturz** (`docker kill`, Exit 137) | PostgreSQL-Log: „not properly shut down; automatic recovery“ | identisch |

*Nicht* getestet: Neustart des gesamten Macs, vollständiger Datenträger, `docker-compose down -v` (löscht die Daten absichtlich).

## 4. Gegenproben (absichtlich eingebaute Fehler – alle entdeckt)

* **Phase 1 (5 Fehler):** 2-%-Prüfung entfernt (3 Tests schlugen fehl) · Veraltet-Prüfung aus (5) · Hash ignoriert Inhalt (3) · Take-Profit vor Stop geprüft (1) · Doppel-Order erlaubt (3).
* **Phase 2 (12 Fehler):** Executor ohne Sperre (10) · Pipeline ohne Sperre (5) · SORA ignoriert bestehendes Risiko (6) · SORA rundet auf statt ab (20) · SORA ohne Liquidationsfilter (2) ·
  SORA ohne Slippage im Einstiegskurs (2) · RIN erlaubt Spot-Short (4) · RIN ohne Setup-Alterprüfung (1) · RIN lässt alle Handelspaare zu (16) · RIN ohne Netto-Chance-Risiko (5) ·
  Einstiegsgebühr doppelt als Risiko gezählt (3) · Validator ohne NaN-Schutz (3). Alle entdeckt.
* **Phase 3 (10 Fehler, jeweils nur mit Datenbank-Tests geprüft):**

| Eingebauter Fehler | Entdeckt durch |
|---|---|
| A: keine Konto-Sperre im Python-Code | Mehrprozess-Tests (Datenbank-Trigger fängt den Wettlauf ab → Worker melden Fehler) |
| B: Constraint-Trigger (2 %) entfernt | Schema-Test (Wettlauf zweier offener Transaktionen) |
| C: **beides** entfernt | Mehrprozess-Tests – hier greift die dritte Ebene: der Primärschlüssel verhindert doppelte Positions-Nummern |
| D: Änderungssperre auf `event_log` entfernt | Schema-Test |
| E: Order-IDs nach Neustart vergessen | Neustart-/Executor-Tests |
| F: Höchststand nicht gespeichert | Neustart-Test |
| G: Protokoll ohne Advisory-Sperre | Schema-Test (gleichzeitiges Schreiben) |
| H: kein Rollback (Autocommit) | Executor-Test (Absturz vor Commit) |
| I: Hash-Prüfung der Datenbank entfernt | Schema-Test (gefälschter Hash) |
| J: offene Positionen werden nach Neustart nicht geladen | Neustart-Test |

Hinweis zu C: Ohne Sperre **und** ohne Trigger würde das Limit rechnerisch überschritten, wird aber bereits vom Primärschlüssel verhindert (zwei Prozesse vergeben dieselbe Positions-Nummer). Eine Verletzung des 2-%-Limits *selbst* wurde in keiner Variante beobachtet.

## 5. Bei der Arbeit gefundene Probleme

**Echte Mängel (behoben, mit Tests):**
1. **Wartekreis im Datenbank-Trigger.** Der Constraint-Trigger verlangte beim COMMIT `FOR UPDATE` auf der Konto-Zeile; das kollidiert mit der Fremdschlüssel-Sperre (`FOR KEY SHARE`) einer zweiten offenen Transaktion → beide hätten sich gegenseitig blockiert (ein Test hing). Behoben mit `FOR NO KEY UPDATE`; die Python-Transaktion nutzt weiterhin `FOR UPDATE` zu Beginn.
2. **Verkürztes Protokoll nicht erkennbar** (Lücke aus Phase 1): In der Datenbank-Variante gleicht `verify()` jetzt das Kettenende mit dem Zeiger ab.
3. `append_raw` ging von einem bestimmten Zeilenformat des Datenbank-Cursors aus (Fehler beim ersten Lauf); jetzt unabhängig davon.
4. Die Pipeline fängt Fehler der Ausführung jetzt ab und blockiert fail-closed (`EXECUTION_ERROR`), statt abzustürzen.

**Fehler in meiner Test-/Prüftechnik (nicht im Produktcode) – offen berichtet:**
* `multiprocessing.Barrier` ließ sich unter macOS nicht an neu gestartete Prozesse übergeben (Tests hingen 3 Minuten) → Umstellung auf echte `subprocess`-Prozesse mit gemeinsamer Startzeit.
* **Eine erste Neustart-Prüfung war wertlos:** In zsh wurde `$DC stop db` nicht ausgeführt und die Fehlermeldung von mir unterdrückt; das „Ergebnis: identisch“ bewies daher nichts. Ich habe es **verworfen** und mit Belegen (PostgreSQL-Startzeit, Container-ID) neu gemacht – nur diese Fassung gilt.
* Das erste Gegenprobe-Skript lief wegen desselben zsh-Problems ohne Tests; neu als Python-Skript geschrieben. Zwei Gegenproben waren zunächst ungültiges SQL (Entdeckung aus falschem Grund) und wurden korrigiert.
* Ein Test erwartete einen Fehler an der falschen Stelle (Hook feuerte schon bei SORAs Portfolio-Abfrage – ebenfalls fail-closed).

## 6. Verbleibende Fehler, Grenzen und nicht Getestetes

| Punkt | Einordnung |
|---|---|
| **Hash-Kette/Trigger schützen nicht vor einem Datenbank-Superuser**, der Trigger abschaltet *und* Kette und Zeiger konsistent neu schreibt | Eingriffe werden nur in den getesteten Fällen sichtbar. Superuser-Passwort nur in der lokalen Datei; Anwendung nutzt ihn nie. Später: Kettenende extern ablegen. |
| **Eine Sperre pro Konto** = alle Aktionen eines Kontos laufen nacheinander; auch `portfolio_state()` nimmt sie kurz | Bewusst (Sicherheit vor Tempo). Bei > 10 s Wartezeit wird abgebrochen → fail-closed. **Durchsatz nicht gemessen.** |
| Bei Konkurrenz kann SORAs Vorschlag vom Risikomanager **abgelehnt** statt automatisch verkleinert werden | sicher, aber nicht optimal; Verbesserung später möglich |
| Nur **ein Konto** und **eine** Datenbank getestet; `market_state` (Kurse) gilt für alle Konten gemeinsam | reicht für jetzt |
| **Kein Backup, keine Replikation, kein TLS**; Port nur auf `127.0.0.1` | Pflicht vor dem VPS-Betrieb |
| Docker nur mit **Colima auf Apple Silicon** getestet; nicht mit Docker Desktop, nicht auf Linux/amd64 (Hetzner) | vor dem VPS-Betrieb dort testen |
| Python **3.13/3.14** und GIL-freies Python nicht getestet; Kern bleibt 3.9-kompatibel, `persistence` nur unter 3.12 geprüft | |
| **Lasttest / Dauerbetrieb** (24/7, viele Tausend Einträge, Speicher, `VACUUM`) nicht durchgeführt | vor dem VPS-Betrieb |
| Neustart des **gesamten Macs**, volle Festplatte, Netzwerkausfall, Datenbank-Failover nicht getestet | |
| `docker compose` (ohne Bindestrich) ist nicht eingerichtet (würde `~/.docker/config.json` ändern); es gilt `docker-compose` | bewusst |
| Echte Börsenregeln (Mindestmenge, Tick-/Lot-Größe, Gebührentarife) fehlen; **keine Strategie → keine Aussage über Profitabilität** | spätere Phasen |

**Keine bekannten offenen Fehler im getesteten Umfang.** „Keine bekannten“ heißt nicht „keine“: Alles in der Tabelle oben ist ungeprüft oder bewusst vereinfacht.

## 7. Reproduzieren

Siehe [README.md](../README.md) („Projekt starten“). Kurzfassung:

```bash
docker-compose --env-file ~/trading-secrets.env up -d --build db
docker-compose --env-file ~/trading-secrets.env run --rm app pytest -q -rs       # alle Tests im Container (Python 3.12)
~/venvs/trading312/bin/ruff check src tests scripts && ~/venvs/trading312/bin/mypy --strict src
```
