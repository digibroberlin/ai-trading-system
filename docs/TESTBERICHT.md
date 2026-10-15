# Testbericht – Stufe 1 (integriertes Paper-Trading-Grundsystem mit Backtesting)

Stand: 2026-10-14 · Daten: ausschließlich synthetisch · Modus: ausschließlich PAPER · keine Börsenverbindung

Alle Zahlen in diesem Bericht stammen aus tatsächlich ausgeführten Läufen am 2026-10-14. Was nicht geprüft wurde, steht in Abschnitt 6.
Der Bericht der früheren Phasen liegt unverändert in [TESTBERICHT_PHASE1-3.md](TESTBERICHT_PHASE1-3.md).

## 1. Auf einen Blick

| Prüfung | Ergebnis | Umgebung |
|---|---|---|
| **Alle Tests** | **606 von 606 bestanden**, 0 übersprungen | Mac, Python **3.12.14**, PostgreSQL **16.15** (Docker/Colima) |
| **Alle Tests im Container** | **606 von 606 bestanden**, 0 übersprungen | Docker-Dienst `tests`, Python **3.12.15** (Linux/arm64) gegen PostgreSQL 16.15 |
| Tests ohne Datenbank | 512 bestanden, **94 übersprungen** (sichtbar als „skipped“) | Python 3.9.6 (zeigt zugleich: der Kern bleibt 3.9-kompatibel) |
| Mehrprozess- und Gleichzeitigkeitstests, 3× wiederholt | 3 von 3 Läufen: je 60 von 60 | Mac, Python 3.12.14 |
| **Gegenprobe** (absichtlich eingebaute Fehler) | **193 von 193 entdeckt** | `scripts/mutation_check.py`, siehe Abschnitt 4 |
| mypy `--strict` (55 Quelldateien), ruff (`src`, `tests`, `scripts`) | keine Befunde | Python 3.12.14 |
| Docker-Dienste `migrate`, `app` (Vorführung), Backtest im Container | laufen; beide Migrationen „angewandt“ | Docker/Colima |
| Backtest auf dem Mac und im Linux-Container | **identische Ergebnis-Fingerabdrücke** | Python 3.12.14 / 3.12.15 |
| Echte Datenbank `trading` nach allen Läufen | **unverändert** (`restart_check.py verify`: IDENTISCH) | 2 offene Positionen, 6 Protokolleinträge, Kettenende gleich |
| Aufräumen | keine Test-Datenbank übrig, kein `__pycache__`, keine `.env` im Projekt | |

Tests nach Datei (Summe 606): `test_paper_executor` 87 · `test_rin` 64 · `test_risk_manager` 51 · `test_sora` 48 · `test_pg_schema` 42 ·
`test_backtest_data` 39 · `test_concurrency` 38 · `test_backtest_engine` 37 · `test_limits_and_config` 35 · `test_pipeline_and_agents` 31 ·
`test_data_validator` 26 · `test_pg_multiprocess` 22 · `test_pg_executor` 17 · `test_performance` 16 · `test_event_log` 14 ·
`test_rin_sora_pipeline` 10 · `test_pg_restart` 10 · `test_stats_and_synthetic` 9 · `test_no_real_trading` 7 · `test_backtest_pg` 3.
Datenbank-Tests: 94 (die fünf `*_pg*`-Dateien). Vor Stufe 1 waren es insgesamt 354 Tests.

## 2. Was geprüft wird

### 2.1 Instrument-Schlüssel (Audit H1)
Börse + Markttyp + Symbol überall. Getestet u. a.: Eine Spot-Kerze schließt keine Futures-Position desselben Paares; Marktdaten eines
anderen Instruments werden abgelehnt (Executor, RIN, SORA); dasselbe Paar an zwei Börsen hat getrennte Kurse und Positionen;
in PostgreSQL getrennte Zeilen in `instrument_state` und höchstens eine offene Position je Instrument.

### 2.2 Marktdaten (Audit H3, M10, H2, M6)
Jede Kerze wird vollständig geprüft (Zeitzone, Zeitraster, endliche positive Zahlen, Hoch/Tief passend zu Eröffnung/Schluss, Volumen ≥ 0).
Der Executor verarbeitet nur abgeschlossene Kerzen in zeitlicher Reihenfolge; doppelte, ältere, laufende oder fehlerhafte Kerzen werden
ohne Wirkung verworfen und protokolliert, Lücken und auffällige Kerzen werden protokolliert. Funding kommt nur aus ausdrücklichen
Ereignissen und wirkt je Position höchstens einmal – auch wenn acht Prozesse dasselbe Ereignis gleichzeitig einreichen. Eine Kerze, die vor
dem Einstieg begann, zählt nur für den Stop.

### 2.3 Risikolimits (Ihre Festlegungen zu M4 und H5)
Eine einzige Rechenfunktion (`safety/order_risk.py`) für Risikomanager und SORA. Geprüft wird der Zustand **nach** der Order gegen die
Netto-Equity **nach** den dabei anfallenden Kosten; alle Grenzen gelten gleichzeitig: 0,5 % je Trade, 2 % Gesamtrisiko, 100 % Brutto-Exposure,
5 % Verlust bei 10 % Kursschock (bei Futures mit Margin, Hebel und Liquidation), Margin, Liquidationsabstand, Positionszahl, Notbremsen.
Getestet: jede Grenze einzeln an ihrem Grenzwert, jede Kombination in Zufallsläufen (keine Order, nach der eine Kennzahl über ihrer Grenze
liegt), fehlende/nicht endliche Zahlen und veraltete Bewertungskurse führen zur Ablehnung, Limits lassen sich nur strenger einstellen.
**Gleichzeitige Anfragen:** 12 echte Prozesse auf 12 Instrumenten – nie mehr als 2 % Gesamtrisiko; dieselbe Order-ID wird genau einmal
ausgeführt; derselbe Markt erhält genau eine Position; ein mitten in der Transaktion beendeter Prozess hinterlässt keine Spur.
Zweiter Gurt in der Datenbank (Gesamtrisiko und Exposure beim COMMIT), auch unter `REPEATABLE READ`/`SERIALIZABLE`.

### 2.4 Positionen schließen (Audit M11)
Stop-Loss, Take-Profit, Liquidation, Ausstiegssignal, Eingriff. Kurslücke über den Stop: schlechterer Eröffnungskurs. Stop und Ziel in
derselben Kerze: Stop. Ausstieg auf Signal zum Geld-/Briefkurs mit Slippage und Taker-Gebühr; ohne gültige aktuelle Marktdaten wird nicht
geschlossen. Liquidation kostet die gesamte Margin. Von Hand nachgerechnete Beträge für Long und Short, Spot und Futures.

### 2.5 Kapitalkurve und Kennzahlen (Audit M13)
Laufend bewertete Kapitalkurve, realisiertes und unrealisiertes Ergebnis, Gewinnrate, Profit-Faktor, durchschnittlicher Gewinn/Verlust,
maximaler Drawdown, Sharpe/Sortino – alles netto. Der Bericht bricht ab, wenn Endkapital ≠ Start + realisiert + unrealisiert.

### 2.6 Backtesting-Engine
Siehe [BACKTESTING.md](BACKTESTING.md). Getestet: kein Blick in die Zukunft (drei unabhängige Nachweise), Ausführung zum nächsten
Eröffnungskurs, Kosten von Hand nachgerechnet, Funding, Limits bei jeder Ausführung, vollständiges Protokoll, Wiederholbarkeit (auch in
getrennten Prozessen und über PostgreSQL bit-identisch zum Lauf im Speicher), Vergleich nur bei gleichen Voraussetzungen, abgestürzte Strategie
oder beschädigtes Protokoll macht den Lauf ungültig.

### 2.7 PostgreSQL, Migrationen, Neustart, Docker
Migrationsläufer (Reihenfolge, Prüfsummen, eine Transaktion je Schritt, bestehende Datenbank nur mit ausdrücklicher Bestätigung übernehmen);
Übergang von Schema 001 auf 002 mit Bestandsdaten; Trigger und Rechte einzeln; Neustart mit offenen Positionen, Funding-Fortschritt,
Marktzustand, Handelstag und Notbremsen; Container `app` ohne Administrator-Zugang.

## 3. Geänderte Tests – und warum

Kein Test wurde abgeschwächt, um einen Programmfehler zu verdecken. Geändert wurden Tests in zwei Fällen:

**a) Die Anforderungen haben sich geändert** (Ihre Festlegungen zu M4/H5, neue Datenregeln):
* Szenarien mit mehreren Positionen nutzen jetzt kleine Positionen mit weitem Stop (9 Stück zu 100, Stop 95). Mit den früheren Größen
  hätte das neue Stress-Limit zuerst gegriffen, und die 2-%-Grenze wäre gar nicht mehr geprüft worden.
* Funding-Tests verwenden `FundingEvent` statt der alten festen Uhrzeiten; Kerzen müssen abgeschlossen sein.
* Ein Test („Spot wird vor dem 2-%-Limit durch das Guthaben begrenzt“) entfiel: Das Szenario ist unter den neuen Grenzen nicht mehr
  erreichbar. Die Margin-Regel ist weiter direkt am Risikomanager getestet, einschließlich des Grenzfalls mit Gebühr.

**b) Die Erwartung im Test war falsch** (das Programm rechnete richtig):
* Handrechnung 7,817 × 5,117 = 39,999589 (ich hatte 39,999639 notiert) – zwei Stellen.
* Vertauschte Kerzen: Der Prüfer meldet „Lücke, falsche Reihenfolge“; ich hatte eine zweite Lücke erwartet, die es nicht gibt.
* Bei extrem hohen Kosten blockiert RIN alle Trades („Stop zu eng im Verhältnis zu den Kosten“). Mein Test hatte trotzdem Trades erwartet;
  daraus wurde ein eigener Test für genau dieses Verhalten.
* Frühere Fälle in Stufe 1: Zahl aus Fließkommawert gebildet, Kerze nicht im Zeitraster, zu schnelle Test-Uhr (Bewertung galt als veraltet).

## 4. Gegenprobe: Bemerken die Tests eingebaute Fehler?

`scripts/mutation_check.py` baut je einen bekannten Fehler in eine Kopie des Projekts ein und lässt die Tests laufen.

| Gruppe | Einträge | Entdeckt | Beispiele |
|---|---|---|---|
| Kern | 135 | 135 | Limit abgeschaltet oder gelockert, Stress-Schock in die falsche Richtung, Funding doppelt buchbar, Stop zum besseren Kurs gefüllt, Drawdown-Bremse wirkungslos, RIN ohne Spread in den Kosten, abgestürzter Agent zählt als Zustimmung |
| Backtest | 28 | 28 | Ausführung zum Schlusskurs der beginnenden Kerze, Strategie sieht die Zukunft, Funding vergessen, defektes Protokoll hingenommen |
| Datenbank | 30 | 30 | Konto-Sperre entfernt, Datenbank-Gurt entfernt, kein Rollback, Handelstag nicht gespeichert, Protokoll löschbar, veränderte Migration unbemerkt |

**Alle 14 Fehler, die das Audit als „übersehen“ gemeldet hatte, werden jetzt bemerkt.**

Ehrlich dazu: Im ersten Durchlauf der neuen, größeren Gegenprobe wurden **14 Einträge nicht bemerkt** (andere als die 14 aus dem Audit).
Daraufhin entstanden zusätzliche Tests:
* Backtest (3): Ergebnis-Fingerabdruck deckt die Ergebnisse selbst ab · beschädigtes Protokoll macht den Lauf ungültig · Reihenfolge der Eingabedaten egal.
* Kern (6): Fehler nach der Ausführung hinterlässt keinen Protokolleintrag · Short-Stop mit Slippage zu Ungunsten · letzter bekannter Kurs nach dem
  Schließen · SORA gibt nie frei, was die gemeinsame Risikorechnung ablehnt · Ausfall der Ausführung blockiert nur das eine Setup · Nullsummen-Trade
  unterbricht eine Verlustserie.
* Datenbank (4): Handelstag und Tagesstart-Kapital überstehen Neustarts (zwei Einträge; **sicherheitsrelevant**: sonst hätte ein Neustart die
  Tagesverlust-Notbremse zurücksetzen können – das Programm war richtig, aber kein Test hätte einen Fehler dort bemerkt) · Datenbank lehnt in sich
  stimmige, aber nicht anschließende Protokollzeilen ab (zwei Einträge).
* Ein Eintrag (Überlappung von Kerzen) zielte auf Code, der nachweislich nie erreicht werden kann; dieser tote Code wurde entfernt.

Grenze der Methode: Die Gegenprobe zeigt nur, dass **diese 193** Fehler bemerkt würden – nicht, dass jeder denkbare Fehler bemerkt wird.

## 5. Bei der Arbeit gefundene Probleme im Programm

* **Schleifenvariable in der Backtest-Engine** (von der Stilprüfung gemeldet): Eine Hilfsfunktion griff auf die Variable des laufenden
  Zeitpunkts zu. Kein Fehlverhalten, aber fehleranfällig – jetzt wird der Zeitpunkt ausdrücklich übergeben.
* **Toter Code im Kerzen-Prüfer** (Überlappung) entfernt, siehe oben.
* **Werkzeug-Problem, nicht im Programm:** Ein abgebrochener Lauf der Gegenprobe ließ einen temporären Ordner zurück; er wurde entfernt.
  Das Werkzeug räumt im Normalfall selbst auf.

## 6. Nicht geprüft, Grenzen, offene Punkte

| Punkt | Einordnung |
|---|---|
| **Echte Marktdaten** | Es gibt keinen Adapter. Alle Ergebnisse beruhen auf erfundenen Daten und sagen nichts über Gewinne. |
| **Strategien** | Keine echte Handelslogik; KIBO und YUZU sind neutrale Platzhalter. |
| **Stress-Limit ohne Datenbank-Gurt** | Nur in der Anwendung gerechnet (unter der Konto-Sperre). |
| **Stress-Limit ist die engste Grenze** | Begrenzt die ungesicherte Gesamtgröße auf knapp 50 % des Kapitals. |
| **Unter Konkurrenz Ablehnung statt Verkleinerung** (Audit M8) | Sicher, aber nicht optimal. |
| **Wiederholte Order-ID** liefert „Duplikat“ statt des ursprünglichen Ergebnisses (Audit M7) | offen |
| **PAPER-Schutz nicht an einem zentralen Startpunkt** (Audit M3) | offen; ein Live-Modus existiert im Code nicht |
| **Anwendungsrolle darf Konten anlegen und den Kontostand schreiben** (Audit M2, Rest) | offen |
| **Wiederverbindung zur Datenbank, hängende Transaktionen** (Audit H7) | offen |
| **Kein regelmäßiges Backup, keine festgeschriebenen Paketversionen** (Audit M14, M12) | offen |
| **Börsenregeln je Instrument** (Mindestgröße, Preis- und Mengenschritte) | fehlen |
| Docker nur mit **Colima auf Apple Silicon**; nicht Docker Desktop, nicht Linux/amd64 (Hetzner) | vor dem Server-Betrieb testen |
| Python **3.13/3.14** nicht getestet; `trading.persistence` nur unter 3.12 | |
| **Lasttest / Dauerbetrieb**, Neustart des gesamten Macs, volle Festplatte, Netzwerkausfall | nicht durchgeführt |
| Neustart-Arten des Containers (restart, stop/start, down/up, VM-Neustart, `docker kill`) | in Phase 3 geprüft, in Stufe 1 **nicht erneut**; die Neustart-Tests (neuer Prozess, neue Verbindung) liefen |
| Alt-Positionen im Konto `restart-check` | überschreiten die neue Stress-Grenze (8,05 %); neue Trades dort blockiert |

**Keine bekannten offenen Fehler im getesteten Umfang.** „Keine bekannten“ heißt nicht „keine“.

## 7. Reproduzieren

```bash
docker-compose --env-file ~/trading-secrets.env up -d --build db
docker-compose --env-file ~/trading-secrets.env run --rm tests                    # alle Tests im Container (Python 3.12)
set -a; source ~/trading-secrets.env; set +a
export TEST_ADMIN_URL="postgresql://trading:${POSTGRES_PASSWORD}@127.0.0.1:5432/postgres" TEST_APP_PASSWORD="$APP_DB_PASSWORD"
~/venvs/trading312/bin/pytest -q -rs                                              # alle Tests auf dem Mac
~/venvs/trading312/bin/ruff check src tests scripts && ~/venvs/trading312/bin/mypy --strict src
~/venvs/trading312/bin/python scripts/mutation_check.py                           # Gegenprobe (ca. 15 Minuten)
```

## Nachtrag 2026-10-15 – AP-05 Abhängigkeiten (erste Prüfung in der Cloud)

Umgebung: Cloud-Sitzung (Linux/amd64), Python **3.12.3**, PostgreSQL **16** (Wegwerf-Datenbank über `scripts/cloud_test_db.sh`).

| Prüfung | Ergebnis |
|---|---|
| Preflight vor der Änderung (Stand `develop`, pytest 8.4.2) | STABIL – 633/633 Tests, 0 übersprungen |
| Preflight nach der Änderung (Werkzeuge aus `requirements/dev.lock`, pytest 9.0.3) | STABIL – **646/646** Tests, 0 übersprungen (+13 in `test_dependency_locks.py`) |
| Installation aus allen vier Sperrdateien mit `--require-hashes` in frischen Umgebungen | erfolgreich (dev, runtime, audit unter 3.12.3; py39 unter 3.9.25) |
| Laufzeit-Umgebung wie im Image (nur `runtime.lock`, `PYTHONPATH=src`) | Vorführung `trading.demo` läuft, Migrationsordner wird gefunden |
| Kern-Tests unter Python 3.9.25 ohne psycopg | 552 bestanden, 5 Datenbank-Dateien **sichtbar übersprungen** (vorher: Abbruch beim Einsammeln, siehe unten) |
| `scripts/dependency_audit.py` (pip-audit 2.10.1) | keine unbegründeten Lücken; eine begründete Ausnahme in `py39.lock` (D-010) |

**Behobener Fehler:** Die fünf Datenbank-Testdateien importierten `psycopg` direkt. Ohne psycopg (Python 3.9, GitHub-Job
„Kern ohne Datenbank“) brach pytest deshalb mit 5 Fehlern ab, statt diese Tests als „skipped“ zu melden. Jetzt
`pytest.importorskip("psycopg")`. Unter 3.12 mit Datenbank ändert sich nichts (0 übersprungen).

**Nicht geprüft:** Docker-Image-Bau in der Cloud (Docker Hub antwortete „429 Too Many Requests“); Gegenprobe nicht erneut
ausgeführt (keine Sicherheitslogik geändert); GitHub-Actions-Ergebnis (aus der Cloud-Sitzung nicht einsehbar).

## Nachtrag 2026-10-15 – AP-06 Zentraler PAPER-Schutz (Audit M3)

* **Was neu ist:** Schon `import trading` prüft Modus und Umgebung (jeder Prozess, auch Migration, Prüfskripte, Test-Worker).
  Erkannt werden Börsen-Schlüssel in jeder Schreibweise, `CDP_*` (Coinbase Developer Platform), weitere Börsen und CCXT,
  allgemeine Namen wie `API_KEY`/`PASSPHRASE`, sowie zwei typische Inhalte (CDP-Schlüsselname, EC-Privatschlüssel) unter jedem Namen.
* **Tests:** `tests/test_paper_guard.py` (neu) – u. a. startet jedes Programm mit `if __name__ == "__main__"` unter `src/trading`
  sowie `scripts/restart_check.py` und `tests/pg_workers.py` als **eigenen Prozess** mit verbotener Umgebung und erwartet den Abbruch.
  Ein Gegentest stellt sicher, dass Zugänge der Cloud-Umgebung (`AWS_SECRET_ACCESS_KEY`, `GH_TOKEN` …) den Start nicht verhindern.
* **Gegenprobe:** neue Einträge E04–E10 – **9 von 9** (mit E02/E03) entdeckt, jeweils vom passenden Test.
* **Gefundener Fehler in der Gegenprobe selbst:** Die Arbeitskopie enthielt seit AP-05 den Ordner `requirements/` nicht; der neue
  Sperrdatei-Test schlug dort immer fehl, und **jeder** eingebaute Fehler hätte als „entdeckt“ gegolten. Behoben (Kopie vollständig)
  und abgesichert: Die Gegenprobe läuft jetzt zuerst eine **Grundprobe** ohne eingebauten Fehler und bricht ab, wenn die schon scheitert.
