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
| **Unter Konkurrenz Ablehnung statt Verkleinerung** (Audit M8) | **behoben** (AP-09, Nachtrag unten) |
| **Wiederholte Order-ID** liefert „Duplikat“ statt des ursprünglichen Ergebnisses (Audit M7) | **behoben** (AP-08, Nachtrag unten) |
| **PAPER-Schutz nicht an einem zentralen Startpunkt** (Audit M3) | offen; ein Live-Modus existiert im Code nicht |
| **Anwendungsrolle darf Konten anlegen und den Kontostand schreiben** (Audit M2, Rest) | **behoben** (AP-10, Nachtrag unten); Beträge rechnet weiter die Anwendung |
| **Wiederverbindung zur Datenbank, hängende Transaktionen** (Audit H7) | **behoben** (AP-07, AP-11, Nachtrag unten); Alarm fehlt noch (AP-25) |
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

## Nachtrag 2026-10-15 – AP-07 Globaler Not-Aus (Kill Switch)

Umgebung: Cloud-Sitzung (Linux/amd64), Python 3.12.3, PostgreSQL 16 (Wegwerf-Datenbank). Alle Daten synthetisch.

* **Neue Tests:** `tests/test_kill_switch.py` (28, ohne Datenbank) und `tests/test_pg_kill_switch.py` (27, mit Datenbank, darunter
  ein Mehrprozess-Test mit 8 echten Prozessen auf 6 Konten, 3× wiederholt, und ein Test mit einer 15 s hängenden Transaktion).
  Angepasst (strenger, nicht schwächer): Tagesverlust-Bremse löst sich nicht mehr um Mitternacht, sondern erst nach Freigabe;
  die Notbremsen werden am Zustand nach der Order gemessen (Grenzfälle mit 2,40 Gebühr neu berechnet).
* **Preflight:** STABIL – **798/798** Tests, 0 übersprungen (mypy, ruff, Geheimnis-Scan ohne Befund).
* **Ein Ausreißer, nicht erklärt:** Ein Preflight-Lauf während der Arbeit meldete „796 bestanden, 2 fehlgeschlagen“. In sechs
  weiteren Komplettläufen und vier Läufen der zeitkritischen Testdateien trat das nicht wieder auf. Welche Tests es waren, ist
  nicht bekannt, weil `preflight.py` bis dahin nur die Summe ausgab – das Skript nennt fehlgeschlagene Tests jetzt mit Namen.
  Verdacht: zeitabhängige Mehrprozess-/Thread-Tests unter Last. Bleibt beobachtet.
* **Unabhängige Prüfung (5 Prüf-Agenten, jeder Befund von einem zweiten Agenten gegengeprüft):** 16 bestätigte Befunde, keiner
  „hoch“, alle behoben und mit Tests abgesichert – u. a.: Verlust der Mitternachts-Kerze fiel aus der Tagesbremse; Notbremse vor
  statt nach der Order gemessen; geteilter Not-Aus konnte zwischen Prüfung und Fill ausgelöst werden; eine hängende Transaktion
  blockierte den Not-Aus von außen unbegrenzt; Freigabe konnte in der Datenbank Name/Zeit einer früheren Freigabe erben;
  Schlüssel-Erkennung übersah camelCase und Börsennamen in der Mitte (`MY_BINANCE_API_KEY`, `FREQTRADE__EXCHANGE__KEY`);
  Docker-Testimage enthielt `Dockerfile`/`.github` nicht (2 Tests wären dort rot); Lückenprüfung übersprang Python-3.9-Pakete;
  mehrere Tests prüften schwächer als behauptet (Trigger-Tests nur über fehlende Rechte, Mehrprozess-Test konnte trivial bestehen).
* **Nicht geprüft:** Docker-Image-Bau (Docker Hub 429 in der Cloud); GitHub-Actions-Lauf der neuen Stände (aus dieser Sitzung
  nicht einsehbar – die Routine kann ihn lesen); vollständige Gegenprobe aller 236 Einträge (nur die AP-06/07-Einträge, siehe unten).
* **Gegenprobe der AP-06/07-Einträge (Cloud-Routine, 2026-10-16 02:21–02:58 UTC, mit Datenbank):** **46 von 46 entdeckt**
  (E04–E12, N01–N17, G30–G47 sowie die angepassten P19/P26). Dabei fielen vier Einträge auf, deren Textstelle durch AP-07
  nicht mehr im Code stand (E05, P19, P26, N06) – die volle Gegenprobe wäre an ihnen abgebrochen. Angepasst, ohne die Absicht
  der Prüfung zu ändern. Neu: `mutation_check.py` prüft vor dem Lauf, dass **jede** Textstelle genau einmal vorkommt, und
  bricht sonst sofort mit einer Liste ab. Die vollständige Gegenprobe aller 236 Einträge ist für AP-07 **nicht** gelaufen
  (folgt spätestens vor `stable/004`).

## Nachtrag 2026-10-16 – AP-08 Wiederholte Order liefert das ursprüngliche Ergebnis (Audit M7)

Umgebung: Cloud-Routine (Linux/amd64), Python 3.12.3, PostgreSQL 16 (Wegwerf-Datenbank). Alle Daten synthetisch.

* **Verhalten (D-012):** Gleiche Order-ID mit gleichem Inhalt → ursprüngliches Ergebnis (`replayed=True`, Protokoll
  `ORDER_REPLAYED`), nichts wird gehandelt. Gleiche ID mit anderem Inhalt → weiter „Duplikat“. Migration 004 speichert
  Fingerabdruck und Position in `orders`.
* **Tests:** neu bzw. geändert in `test_paper_executor` (Wiederholung nach Fill und nach Ablehnung, anderer Inhalt, Not-Aus,
  Einträge ohne Ergebnis, Fingerabdruck), `test_concurrency` (16 Threads: 1 Fill + 15 Wiederholungen mit derselben Position),
  `test_pipeline_and_agents`, `test_pg_executor` (Speicher- und DB-Executor liefern identische Ergebnisse inkl. Wiederholung),
  `test_pg_restart` (Wiederholung nach Neustart, auch nach Funding; Alt-Einträge ohne Fingerabdruck bleiben blockiert),
  `test_pg_multiprocess` (8 Prozesse, dieselbe ID: 1 Fill + 7 Wiederholungen, je 3×), `test_pg_schema` (neue Constraints).
  Die bisherigen Erwartungen „Wiederholung = Duplikat“ wurden durch die strengeren neuen ersetzt (gleiche Position, genau ein Fill).
* **Preflight:** STABIL – **806/806** Tests, 0 übersprungen. Python 3.9: 683 bestanden, 6 Datenbank-Module übersprungen (erwartet).
* **Gegenprobe der AP-08-Einträge:** 15/15 entdeckt. Die vollständige Gegenprobe aller Einträge steht vor `stable/004` aus.
* **Nicht geprüft:** Docker-Image-Bau; GitHub-Actions-Lauf des neuen Stands (folgt nach dem Push).

## Nachtrag 2026-10-16 – AP-09 Größe unter Konkurrenz innerhalb der Sperre (Audit M8)

Umgebung: Cloud-Routine (Linux/amd64), Python 3.12.3, PostgreSQL 16 (Wegwerf-Datenbank). Alle Daten synthetisch.

* **Verhalten (D-013):** SORA rechnet unter der Sperre des Executors (`submit_sized`), bei PostgreSQL in der Transaktion mit
  gesperrter Konto-Zeile. Ein inzwischen kleiner gewordenes Restbudget ergibt eine kleinere Position statt einer Ablehnung.
* **Neue Tests:** `test_concurrency` (8 getrennte Pipelines mit absichtlich langsamer SORA auf einem Executor: genau 5 Positionen,
  vier mit 0,45 %, die fünfte verkleinert auf 0,1–0,25 %, Rest „zu viele Positionen“ bei SORA, keine Ablehnung beim
  Risikomanager; je 5×; parallel = nacheinander), `test_pg_multiprocess` (dasselbe mit 8 echten Prozessen, je 3×),
  `test_pg_executor` (Rechner sieht den gesperrten Zustand inkl. fremder Position; Order wird gespeichert und wiederholbar;
  „nein“ hinterlässt nichts; Fehler oder fremde ID setzen alles zurück), `test_paper_executor`, `test_pipeline_and_agents`
  (abstürzende SORA blockiert, Sperre frei, Order-ID unverbraucht).
* **Angepasste Tests:** zwei Tests, die eine abstürzende Ausführung nachstellen, ersetzen jetzt `submit_sized` statt `submit`
  bzw. erwarten nur noch **einen** Datenbank-Aufruf (vorher: Portfolio-Abfrage + Ausführung getrennt). Die Erwartung
  „blockiert mit EXECUTION_ERROR, nichts gehandelt“ ist unverändert.
* **Gegenprobe:** neue Einträge Q01–Q03 und G53, angepasst I04 und G06 (Textstellen hatten sich geändert); zusammen mit I06
  **7/7 entdeckt**. Zusätzlich von Hand belegt: Mit der alten Logik (SORA rechnet mit vorher gelesenem Zustand) scheitert der
  neue Mehrprozess-Test in 3 von 3 Durchläufen.
* **Preflight:** STABIL – **821/821** Tests, 0 übersprungen. Python 3.9: 691 bestanden, 6 Datenbank-Module übersprungen (erwartet).
* **Nicht geprüft:** volle Gegenprobe aller Einträge (steht vor `stable/004` aus); Docker-Image-Bau.

## Nachtrag 2026-10-16 – AP-10 Rechte der Anwendungsrolle am Konto (Audit M2, Rest)

Umgebung: Cloud-Routine (Linux/amd64), Python 3.12.3, PostgreSQL 16 (Wegwerf-Datenbank). Alle Daten synthetisch.

* **Verhalten (D-014):** Migration 005 entzieht `trading_app` das Anlegen von Konten; Konten legt nur ein Administrator an
  (`python -m trading.persistence.accounts create KONTO STARTKAPITAL`, mit Protokolleintrag `ACCOUNT_CREATED`). Der
  PostgreSQL-Executor legt kein Konto mehr an und startet ohne Konto nicht. Jede Kontostand-Änderung einer Rolle ohne
  Administratorrechte wird geprüft (ein Versionsschritt, Tag und Positionszähler nur vorwärts, Tagesstart-Equity fest,
  Höchststand fällt nie und liegt nie unter der Equity).
* **Neue Tests:** `test_pg_accounts` (37): Anwendung kann kein Konto anlegen; Executor ohne Konto startet nicht und legt keines an;
  Kontoanlage als Administrator mit Protokolleintrag, wiederholbar ohne Änderung; ungültige Namen und Startkapital (0, negativ,
  NaN, Unendlich, float, int, None) werden abgelehnt; je unplausible Änderung eine Ablehnung, plausible Änderungen erlaubt;
  Administrator darf die Bremsen neu setzen; Prüfung gilt auch für andere Rollen ohne Administratorrechte; Kommandozeile.
* **Angepasste Tests:** Der Executor hat keinen Parameter „Startkapital“ mehr. Die Test-Hilfen legen das Konto vorher als
  Administrator an (`TestDatabase.create_account`, ohne Protokolleintrag – reine Vorbereitung). Der Test „Neustart mit anderem
  Startkapital setzt das Konto nicht zurück“ prüft jetzt dasselbe über eine erneute Kontoanlage. Keine Erwartung wurde gelockert.
* **Gegenprobe:** neue Einträge G54–G65: **12/12 entdeckt**. Die Datenbank-Testliste der Gegenprobe beginnt jetzt mit den
  schnellen Dateien (gleiche Tests, nur schnelleres Abbrechen).
* **Preflight:** STABIL – **861/861** Tests, 0 übersprungen; ruff und mypy `--strict` ohne Befunde. Python 3.9 (ohne Datenbank):
  694 bestanden, 7 Datenbank-Module übersprungen (erwartet).
* **Nicht geprüft:** volle Gegenprobe aller Einträge (steht vor `stable/004` aus); Docker-Image-Bau; `scripts/restart_check.py`
  gegen die echte lokale Datenbank (Mac) – das Konto `restart-check` existiert dort bereits.

## Nachtrag 2026-10-16 – AP-11 Wiederverbindung zur Datenbank (Audit H7)

Umgebung: Cloud-Routine (Linux/amd64), Python 3.12.3, PostgreSQL 16 (Wegwerf-Datenbank). Alle Daten synthetisch.

* **Verhalten (D-015):** `persistence/db.py` → `ConnectionKeeper` baut eine abgerissene Verbindung vor der nächsten Transaktion
  neu auf (höchstens 4 Versuche, Wartezeiten 0,5 / 1 / 2 s, je Versuch höchstens 5 s), sonst `DatabaseUnavailableError`.
  Genutzt von PostgreSQL-Executor, -Protokoll und -Not-Aus. Aktionen werden nur wiederholt, wenn die Verbindung schon **vor** der
  Entscheidung abgerissen war; reine Abfragen und das Auslösen des Not-Aus immer einmal.
* **Neue Tests:** `test_db_keeper` (7, ohne Datenbank): gesunde Verbindung wird weiterverwendet; kaputte/geschlossene wird
  ersetzt; Wartezeiten zwischen Versuchen; Aufgabe nach 4 Versuchen; beim Start kein Warten; bewusst geschlossen = nie neu.
  `test_pg_reconnect` (9, mit Datenbank, Sitzung vom Server beendet über `pg_terminate_backend`): Executor erholt sich ohne
  Neustart (Order, alle Abfragen, Not-Aus-Zustand); Abbruch beim COMMIT → Fehler, nichts gespeichert, dieselbe Order-ID danach
  genau einmal ausgeführt; dauerhaft unerreichbar → begrenzte Versuche, kein Handel, Not-Aus-Zustand „gestoppt“; geschlossener
  Executor verbindet nicht neu; Protokoll wiederholt einen unsicheren Eintrag nicht, Kette bleibt intakt; Not-Aus von außen
  gelingt nach Abbruch; Fehler bei intakter Verbindung (Sperr-Zeitlimit) wird nicht wiederholt.
* **Angepasste Gegenprobe-Einträge:** G08, G35, G47, G62 (Textstellen an den neuen Code angepasst, Inhalt unverändert).
* **Gegenprobe:** neue Einträge G66–G73 und die angepassten G08, G35, G47, G62: **12/12 entdeckt**.
* **Preflight:** STABIL – **877/877** Tests, 0 übersprungen; ruff und mypy `--strict` ohne Befunde. Python 3.9 (ohne Datenbank):
  694 bestanden, 9 Datenbank-Module übersprungen (erwartet).
* **Nicht geprüft:** echter Datenbank-Neustart und echte Netzwerkausfälle (simuliert durch vom Server beendete Sitzungen);
  volle Gegenprobe aller Einträge (steht vor `stable/004` aus); Docker-Image-Bau.

## Nachtrag 2026-10-16 – AP-12 Pipeline-Reihenfolge nach Vorgabe des Eigners (D-017)

Umgebung: Cloud-Routine (Linux/amd64), Python 3.12.3, PostgreSQL 16 (Wegwerf-Datenbank). Alle Daten synthetisch.

* **Verhalten:** MOMO → RIN → SORA (Vorprüfung, Protokoll `SIZING_PRECHECK`) → KIBO → YUZU → SORA (endgültig unter der
  Sperre des Executors, `SIZING_PROPOSED`) → Executor → AKI. Die Vorprüfung ist nur ein Filter; die Order-Größe entsteht unter
  der Sperre. Unlesbarer Portfolio-Zustand vor der Vorprüfung blockiert (fail closed).
* **Geänderter Test:** `test_happy_path_executes_and_documents_every_step` – erwartete Ereignisfolge an die neue Reihenfolge
  angepasst (zusätzlich `SIZING_PRECHECK`, Reihenfolge der Meinungen RIN, KIBO, YUZU geprüft). Inhaltlich strenger als vorher.
* **Neue Tests (8):** Reihenfolge der Agenten-Aufrufe; RIN-Veto stoppt vor SORA; Ablehnung und Absturz der Vorprüfung stoppen
  vor KIBO/YUZU; unlesbarer Zustand blockiert; Zustimmung der Vorprüfung ist keine Erlaubnis (SORA unter der Sperre lehnt ab →
  kein Handel); Absturz unter der Sperre gibt die Order-ID frei; Order-Größe stammt aus der endgültigen Rechnung. Dazu in
  `test_concurrency` ein **deterministischer** Test für Audit M8: Andere handeln genau zwischen Vorprüfung und Sperre → die
  Größe wird ins Restbudget verkleinert, der Risikomanager muss nichts ablehnen.
* **Gefundenes Testproblem:** Der Gegenprobe-Eintrag Q01 (SORA rechnet mit veraltetem Zustand) wurde vorher nur über das
  zufällige Zusammentreffen von Threads entdeckt; durch die Vorprüfung verschob sich das Zeitverhalten, Q01 blieb unentdeckt.
  Der neue deterministische Test schließt die Lücke.
* **Gegenprobe:** neue Einträge W01–W06, angepasst I01, I02, I03, I06 (Textstellen); zusammen mit I09, O07, Q01, Q03:
  **14/14 entdeckt**.
* **Preflight:** STABIL – **886/886** Tests, 0 übersprungen; ruff und mypy `--strict` ohne Befunde. Python 3.9 (ohne Datenbank):
  703 bestanden, 9 Datenbank-Module übersprungen (erwartet).
* **Nicht geprüft:** volle Gegenprobe aller Einträge (steht vor `stable/004` aus). KIBO/YUZU sehen die SORA-Größe noch nicht.

## Nachtrag 2026-10-16 – AP-31/32/33 KI-Kostenoptimierung und Nettogewinn nach allen Kosten (D-016)

Umgebung: interaktive Cloud-Sitzung (Linux/amd64), Python 3.12.3, PostgreSQL 16 (Wegwerf-Datenbank). Alle Daten synthetisch;
**kein** echter KI-Anbieter, nur eine Attrappe – es wurde kein einziger bezahlter Aufruf gemacht.

* **Neue Module:** `ai/pricing.py` (datierte Preistabelle, Kosten je Aufruf, Obergrenze), `ai/budget.py` (harte Budgets je
  Aufruf/Tag/Monat, Standard 0, Reservierung vor dem Aufruf), `ai/activation.py` (ereignisbasierte Aktivierung mit
  Abkühlzeit), `ai/orchestrator.py` (Regeln → Cache/Doppel-Erkennung → Aktivierung → Stufen billig→teuer mit Wertschranke),
  `audit/costs.py` (Betriebskosten, KI-Kosten je Agent/Modell/Analyse/Signal/Tag/Monat, Kosten je Trade, Nettoergebnis nach
  allen Kosten), `audit/ai_value.py` (gepaarter Bootstrap-Vergleich zweier Methoden), `persistence/ai_budget.py` +
  Migration 006 (Budget als Datenbank-Gurt).
* **Tests:** `test_ai_orchestration` (68), `test_operating_costs` (15), `test_pg_ai_budget` (27, mit Datenbank),
  Geheimnis-Scan-Fälle für Token-Zähler und echte Token-Variablen.
* **Unabhängige Prüfung** (8 Prüf-Agenten mit Gegenprüfung): **25 bestätigte Befunde**, alle behoben und mit Tests belegt.
  Die wichtigsten: (1) Meldete der Anbieter keinen Verbrauch, wurden 0 USD gebucht – das Budget war damit wirkungslos; jetzt
  volle Reservierung + Sperre. (2) Die Wertschranke galt je Stufe statt je Analyse (bis ~15 % statt 10 %). (3) Eine sehr
  unsichere KI-Antwort konnte eine sicherere Regel-Entscheidung ersetzen. (4) Der Datenbank-Gurt vertraute dem Tag der
  Anwendung – mit falscher Uhr ließen sich Tages- und Monatsgrenze umgehen; jetzt setzt die Datenbank Tag und Zeitpunkt.
  (5) Neue Grenzen ließen sich mit der alten Freigabe setzen; jetzt sind Name und neuer Zeitpunkt Pflicht (auch fürs
  Entsperren). (6) Die Ausnahme im Geheimnis-Scan war zu breit (jeder Name mit „tokens“); jetzt nur feste Zähler-Namen.
  Weitere: Modell-Abweichung des Anbieters, Ausgabe über `max_tokens`, Fehler in Protokoll/Abrechnung, Sperre während des
  Netzwerkaufrufs, unbegrenzter Cache, Kostenzuordnung über Zeitraumgrenzen, Sommerzeit bei Betriebskosten, schwache Tests.
* **Zweite unabhängige Prüfung** (der Korrekturen selbst, 4 Prüfer + Gegenprüfung): 6 bestätigte Befunde, alle behoben.
  Schwer: (1) Die Korrektur „keine Sperre während des Anbieteraufrufs“ ließ mehrere Threads gleichzeitig über **eine**
  Datenbank-Verbindung buchen – Reservierungen und Sperren gingen verloren (nachgestellt: 45 % über dem Tagesbudget). Jetzt
  Sperre um jede Budget-Transaktion; Tests mit einem gemeinsamen Budget aus 8 Threads und einem erzwungenen Wettlauf.
  (2) Unter REPEATABLE READ zählte der Datenbank-Gurt einen veralteten Stand (zwei Reservierungen über der Tagesgrenze);
  jetzt schreibende Serialisierung, Test über alle drei Isolationsstufen. Mittel/leicht: Cache-Treffer konnten sicherere
  Regeln ersetzen; AKI verlor den Messpunkt bei Protokollfehler; fünf eingebaute Fehler im Gurt blieben unentdeckt (jetzt
  getestet). Danach selbst geprüft und abgesichert (ohne weiteren Prüf-Lauf, auf Wunsch des Eigners): Zeitraum ohne
  Zeitzone wird abgelehnt, voll umgelegte KI-Kosten je Trade nie kleiner als die direkten und inkl. Kostenbuch, „nur
  Ausgabe“ bzw. 0 Ausgabe-Tokens bei Text gelten als fehlende Verbrauchsmeldung, nicht schreibbare Sperre stoppt
  wenigstens den eigenen Prozess. Bewusst offen (optional, README „Bekannte Grenzen“): Wartezeit bei hängendem Anbieter,
  zu kleine Verbrauchsmeldungen, Reichweite des Geheimnis-Scans, Strenge zweier Nebenläufigkeitstests.
* **Vorfall:** Ein Zwischenstand wurde hochgeladen, obwohl der Geheimnis-Scan einen (falschen) Fund meldete – die
  Befehlskette hatte den Fehlercode verschluckt. Es war kein Geheimnis betroffen (Token-**Anzahlen**). Seitdem wird nur noch
  mit `if secret_scan; then push` hochgeladen.
* **Gegenprobe:** alle Kosten-Einträge auf dem Endstand **69/69 entdeckt** (Z01–Z49, G74–G93; mit Datenbank).
* **Preflight:** STABIL – **1363/1363** Tests, 0 übersprungen (mit PostgreSQL 16); ruff und mypy `--strict` ohne Befunde.
  Python 3.9 (ohne Datenbank): 1128 bestanden, 10 Datenbank-Module übersprungen (erwartet; Stand vor den letzten Tests).
* **Nicht geprüft:** echter KI-Anbieter (bewusst, Budget 0, B-004); ob KI-Analysen ihr Geld wert sind (AP-33 mit echten Daten
  in AP-20); Monatswechsel im Datenbank-Budget nur mit vorab eingefügten Einträgen (die Datenbank-Uhr lässt sich nicht
  verstellen); volle Gegenprobe aller Einträge (erst vor `stable/005`); Docker-Image-Bau.

## Nachtrag 2026-10-16 – AP-16 Strategie-Bibliothek (D-018)

Umgebung: interaktive Cloud-Sitzung (Linux/amd64), Python 3.12.3, PostgreSQL 16 (Wegwerf-Datenbank). Nur erfundene Daten.

* **Neu:** `strategies/indicators.py` (SMA, EMA, Standardabweichung, wahre Spanne/ATR, RSI nach Wilder, Veränderungsrate,
  Donchian, Bollinger, Bandbreite, Kaufman-Effizienz), `strategies/library.py` (sechs Strategien auf gemeinsamem Rahmen),
  `strategies/benchmarks.py` (Nicht-Handeln, Kaufen-und-Halten), Register `build_strategy`/`default_strategies`;
  Vorführung `python -m trading.backtest` vergleicht jetzt alle Strategien mit Kaufen-und-Halten.
* **Tests:** `test_strategies` (45): Indikatoren gegen Handrechnungen; Signale je Strategie an gebauten Kursverläufen
  (frisches Kreuzen, Kanal ohne aktuelle Kerze, Ziel = Bollinger-Mitte, Squeeze nur nach Ruhephase, Haltedauer beim
  Scalping); je Strategie ein langer synthetischer Lauf (Stop/Ziel auf der richtigen Seite, kein Short auf Spot, kein
  zweiter Einstieg); je Strategie die volle Pipeline mit Schnitt-Vergleich (kein Blick in die Zukunft) und Bitgleichheit;
  Kaufen-und-Halten mit Spread, Slippage, Gebühren, Funding und Aufteilung.
* **Gegenprobe:** ST01–ST09 **9/9 entdeckt**.
* **Beobachtung (kein Ergebnis!):** Auf 60 Tagen Zufallsdaten liegen die Strategien zwischen −4,8 % und +4,2 %,
  Kaufen-und-Halten −0,3 % – auf Zufallsdaten ist das Rauschen, keine Aussage über einen Vorteil.
* **Preflight:** STABIL – **1417/1417** Tests, 0 übersprungen (mit PostgreSQL 16); Python 3.9 (ohne Datenbank): 1207
  bestanden, 10 Datenbank-Module übersprungen (erwartet). Angepasst: Der Test der Vorführung erwartet jetzt alle Strategien
  und Kaufen-und-Halten statt der zwei alten SMA-Varianten.
* **Nicht geprüft:** echte Marktdaten (AP-14/20); Optimierung und Überanpassung (AP-18); Börsen-Schritte (AP-13).

## Nachtrag 2026-10-16 – AP-17 Marktphasen und regelbasierte KIBO/YUZU (D-019)

Umgebung: interaktive Cloud-Sitzung (Linux/amd64), Python 3.12.3, PostgreSQL 16. Nur erfundene Daten, keine echte KI.

* **Neu:** `analysis/regime.py` (Trend/Seitwärts, Volatilitätsrang), `agents/kibo_rules.py`, `agents/yuzu_rules.py`,
  `agents/ai_review.py` (KI-Zweitmeinung nur über den Orchestrator); neue Ablehnungsgründe STOP_INSIDE_NOISE,
  AGAINST_TREND, TECHNICALS_WEAK, VOLATILITY_TOO_HIGH, FUNDING_UNFAVORABLE, LIQUIDITY_TOO_LOW, CONDITIONS_UNFAVORABLE.
  Indikatoren nach `trading.analysis` verschoben (keine Abhängigkeit Agenten ↔ Strategien). Backtests nutzen standardmäßig
  die echten KIBO/YUZU.
* **Tests:** `test_kibo_yuzu` (23): Phasen (Auf-/Abwärtstrend, seitwärts, ruhig/unruhig, zu wenig Daten); KIBO (kein
  Urteil ohne Daten, mit/gegen den Trend, Stop im Rauschen, RSI/Volumen, schwache Bewertung); YUZU (Volatilität,
  Funding für Long/Short, überfüllte Seite, Spread, Volumeneinbruch, Scalping, Stimmung, unbrauchbare Zusatzdaten);
  KI-Zweitmeinung mit Attrappe (unsicher → Orchestrator, Budget 0 oder Nutzen 0 → nichts bezahlt, harte Ablehnung → nie
  KI, Antwort ohne `approve` → Regeln); voller Backtest mit allen sechs Agenten, bitgleich. Angepasst: ein Backtest-Test
  erwartete die alten Platzhalter; neu ein Test, dass Platzhalter nur auf Wunsch laufen und ausgewiesen werden.
* **Gegenprobe:** KY01–KY10 **10/10 entdeckt**.
* **Preflight:** STABIL – **1451/1451** Tests, 0 übersprungen; Python 3.9: 1241 bestanden, 10 Datenbank-Module übersprungen.
* **Nicht geprüft:** Nutzen der Filter auf echten Daten (AP-18/20); echte KI (B-004).

## Nachtrag 2026-10-16 – AP-23/24 Dashboard (D-020)

Umgebung: interaktive Cloud-Sitzung (Linux/amd64), Python 3.12.3, PostgreSQL 16, Chromium (headless) für die Sichtprüfung.

* **Neu:** `dashboard/state.py` (Momentaufnahme aus Backtest oder Paper-Konto), `dashboard/api.py` (FastAPI, nur lesend,
  WebSocket), `dashboard/static/` (Oberfläche ohne fremde Server), `python -m trading.dashboard`. Neue Abhängigkeiten mit
  Prüfsummen (FastAPI 0.141.1, uvicorn 0.54.0, wsproto 1.3.2, Starlette 1.7.0, Pydantic 2.13.5; httpx 0.28.1 nur für
  Tests); `scripts/dependency_audit.py`: keine unbegründeten Lücken.
* **Tests:** `test_dashboard` (16): ausgedünnte Kurve behält Start/Ende/Tiefpunkt; Drawdown aus allen Punkten;
  Backtest-Momentaufnahme vollständig und reines JSON, Agenten-Zählung (MOMO nur Vorschläge, SORA Vorprüfung + Größe),
  Prüfsumme ändert sich nur mit dem Inhalt; Paper-Konto mit Position, Not-Aus und KI-Budget; API (Zustand, Abschnitte,
  unbekannter Abschnitt 404, POST/PUT/PATCH/DELETE 405, Sicherheits-Header, keine fremden Dateien, keine API-Doku),
  WebSocket sendet nur bei Änderung, LIVE nicht startbar, Dashboard-Code ruft nichts Änderndes auf, Startbefehl nur PAPER.
  Neu in `test_no_real_trading`: Webserver-Bibliotheken nur im Dashboard.
* **Sichtprüfung:** Server lokal gestartet, Seite in Chromium geöffnet (Bildschirmfoto `docs/img/dashboard.png`): alle
  Bereiche gefüllt, WebSocket „live“. Dabei gefunden und behoben: verschachtelte Listen wurden als Text ausgegeben
  („[object …]“), uvicorn ohne WebSocket-Bibliothek (wsproto ergänzt), irreführende Agenten-Zählung bei MOMO/SORA,
  Stil-Attribute gegen die eigene Sicherheitsrichtlinie.
* **Gegenprobe:** DA01–DA06 **6/6 entdeckt** (DA06 erst nach einem strengeren WebSocket-Test).
* **Preflight:** STABIL – **1477/1477** Tests, 0 übersprungen (mit PostgreSQL 16). Python 3.9 (ohne Datenbank): 1251 bestanden,
  11 Module übersprungen (10 Datenbank + Dashboard; FastAPI braucht ≥ 3.10 – erwartet).
* **Nicht geprüft:** Anmeldung/Server-Betrieb (AP-28); DEMO mit laufender Engine (AP-21).

## Nachtrag 2026-10-16 – AP-21 Laufzeit-Engine (D-021)

Umgebung: interaktive Cloud-Sitzung (Linux/amd64), Python 3.12.3, PostgreSQL 16. Nur simulierte Uhr und erfundene Daten.

* **Neu:** `runtime/engine.py` (RuntimeEngine, DecisionMomo, `simulate`, Start-Sperre), Ereignis `REGIME_CHANGE`,
  `AkiAgent.update_equity`/`record_regime_change`, Executor-Schnittstelle um `halt`/`halt_status` ergänzt,
  Dashboard-Option `--simulation` (DEMO).
* **Tests:** `test_runtime_engine` (17): aus ohne Freigabe (auch bei LIVE-Modus oder Börsen-Schlüssel); über 3000
  Ereignisse durch alle Agenten mit stimmiger Buchhaltung; Reproduzierbarkeit; Neustart mit Aufwärmen = ununterbrochener
  Lauf; dieselbe Kerze nach Neustart eröffnet keine zweite Position; Kennungen aus der Entscheidungszeit; drei Fehler →
  Not-Aus; fehlerhafte/doppelte/offene Kerzen verworfen; Futures ohne Funding blockiert; KI-Kosten je Ereignis nur bei
  Setups, ohne Budget nichts bezahlt; Phasenwechsel als Ereignis und einziger Anlass der Phasen-Prüfung; Dashboard aus der
  laufenden Engine. Beim Entwickeln gefunden: doppelte Kurvenpunkte bei gleichzeitigen Kerzen, Signal-Ausstiege fehlten in
  AKI, Kurvenpunkt vor den Orders (alles behoben).
* **Gegenprobe:** RT01–RT08 **8/8 entdeckt** (RT08 erst nach strengerem Aufwärm-Test).
* **Preflight:** STABIL – **1502/1502** Tests, 0 übersprungen; Python 3.9: 1274 bestanden, 12 übersprungen (Datenbank,
  FastAPI – erwartet).
* **Nicht geprüft:** echter Betrieb (bewusst gesperrt), echte Marktdaten (AP-14/15).

## Nachtrag 2026-10-16 – AP-18/19 Forschungsrahmen und Bewertung (D-022)

Umgebung: interaktive Cloud-Sitzung (Linux/amd64), Python 3.12.3, PostgreSQL 16. Nur erfundene Daten.

* **Neu:** `research/splits.py`, `research/registry.py`, `research/experiment.py`, `research/evaluation.py`,
  `MarketDataset.window`, Vorführung `python -m trading.research`.
* **Tests:** `test_research` (15): Abschnitte nacheinander mit Sperrabstand; Walk-forward ohne Überlappung; Fenster ohne
  Daten/Funding aus der Zukunft; Register zählt jeden Versuch, Test nur nach Validierung und nur einmal, Zustand überlebt
  einen Neustart, Freigabe nur nach bestandenem Test mit Namen; Schwelle steigt mit der Zahl der Versuche; Kosten-Stress;
  Zufallsdaten werden mit Gründen verworfen, ein echter (künstlicher) Trend besteht Validierung und Test; Gewinn, der
  höhere Kosten nicht übersteht, wird verworfen; Bewertung nach allen Kosten und je Marktphase (nur Vergangenheit);
  Walk-forward-Stabilität; Nutzen von KIBO/YUZU bei 4 Fenstern = „zu wenig Daten“; Vorführung nur PAPER.
* **Beobachtung (kein Ergebnis!):** Auf 120 Tagen Zufallsdaten werden alle sieben Strategien verworfen – auch
  `sma_cross` mit +144 $ auf den Validierungsdaten, weil das unter der Zufallsschwelle liegt. So soll es sein.
* **Gegenprobe:** RS01–RS09 **9/9 entdeckt** (RS05/RS08 erst nach zwei gezielten Tests).
* **Preflight:** STABIL – **1529/1529** Tests, 0 übersprungen; Python 3.9: 1301 bestanden, 12 übersprungen (erwartet).
* **Nicht geprüft:** echte Daten (AP-20).

## Nachtrag 2026-10-16 – AP-13 Börsenregeln je Instrument (D-023)

Umgebung: interaktive Cloud-Sitzung (Linux/amd64), Python 3.12.3, PostgreSQL 16. Nur erfundene Daten.

* **Neu:** `domain/exchange_rules.py`, `execution/fees.py`, Prüfungen in `safety/order_risk.py` und
  `safety/risk_manager.py`, SORA mit Regeln, `on_ticks` für Strategie-Vorschläge, `BacktestConfig.rules`,
  Laufzeit-Engine mit Regeln; neue Ablehnungsgründe EXCHANGE_RULES_MISSING, QUANTITY_NOT_ON_STEP, NOTIONAL_TOO_SMALL,
  PRICE_NOT_ON_TICK.
* **Tests:** `test_exchange_rules` (14): Rundung zu Ungunsten, ungültige Regeln, Vorrang ausdrücklicher Regeln,
  Gebührenstufen (auch Fehlbedienungen), Risikomanager lehnt Schritt-/Mindest-/Hebelverstöße und fehlende Regeln ab,
  SORA bleibt auf Schritt und Börsen-Hebel (auch bei knapper Margin) und sein Vorschlag besteht beim Risikomanager,
  Backtest mit Regeln erzeugt nur regelkonforme Orders, ohne Regeln für ein Instrument wird nichts gehandelt.
* **Kritischer Fund im Prüfwerkzeug (behoben):** siehe D-023 – die Gegenprobe zählte die Prüfung ihrer eigenen
  Textstellen als Entdeckung. Jetzt ausgeschlossen und durch einen Test abgesichert; K24 echt entdeckt
  (`test_risk_manager`). Betroffen wären nur volle Läufe seit ~17:20 UTC gewesen – es gab keinen.
* **Gegenprobe:** K24, BR01–BR09 **10/10 entdeckt** (BR09 erst nach einem Test mit knapper Margin).
* **Preflight:** STABIL – **1553/1553** Tests, 0 übersprungen; Python 3.9: 1325 bestanden, 12 übersprungen (erwartet:
  Dashboard braucht ≥ 3.10).
* **Nicht geprüft:** echte Börsenregeln (AP-14).

## Nachtrag 2026-10-16 – AP-22 KI-Anbieter-Anschluss (D-024)

Umgebung: interaktive Cloud-Sitzung (Linux/amd64), Python 3.12.3, PostgreSQL 16. Kein echter KI-Aufruf, kein Schlüssel.

* **Neu:** `ai/anthropic_provider.py` (Claude-Anschluss hinter dem Orchestrator, Einsatzprüfung `live_registry`).
* **Tests:** `test_ai_provider` (17, nur Attrappe der SDK-Antwortform): Anfrage wird genau ein SDK-Aufruf mit cachebarer
  Anweisung und Zeitlimit; Verbrauch inkl. Cache-Tokens aus der Antwort; nur Text-Blöcke zählen; abgeschnittene oder
  verweigerte Antworten werden nie genutzt; Fehler werden nicht verschluckt; mit dem Orchestrator: Messung, Eskalation
  nur bei Unsicherheit, Regel-Ersatz bei Fehlern, Budget-Sperre bei fehlendem/falschem Verbrauch oder anderem Modell,
  ohne Budget kein Aufruf; Einsatz nur mit Freigabe, Schlüssel, aktueller Preistabelle, nur Anthropic-Stufen; SDK ohne
  automatische Wiederholungen; Anbieter nur im Orchestrator aufgerufen, SDK nur in einem Modul geladen.
* **Gegenprobe:** AN01–AN10 **10/10 entdeckt**.
* **Preflight:** STABIL – **1580/1580** Tests, 0 übersprungen; Python 3.9: 1352 bestanden, 12 übersprungen (erwartet).
* **Nicht geprüft:** echter Aufruf bei Anthropic (B-004); Preistabelle gegen die aktuelle Preisliste.

## Nachtrag 2026-10-16 – AP-14 Marktdaten-Adapter, nur lesend (D-025)

Umgebung: interaktive Cloud-Sitzung (Linux/amd64), Python 3.12.3, PostgreSQL 16. Kein echter Abruf (Netzregel, B-008).

* **Neu:** `marketdata/public_http.py` (einziger HTTP-Zugang: HTTPS-GET an zwei feste Hosts), `history.py`,
  `coinbase.py`, `bitget.py`, `daycache.py`, `ratelimit.py`, `python -m trading.marketdata`.
* **Tests:** `test_marketdata_adapters` (19) mit nachgebildeten Börsen (Antwortform und Seitenlogik nach der
  Schnittstellenbeschreibung): exakte Zahlen ohne float, Seiten ohne Lücken/Doppelte, Lücken und Widersprüche werden nie
  repariert, ungültige Anfragen erreichen die Börse nicht, Zwischenspeicher nur für abgeschlossene Tage und beschädigte
  Dateien werden neu geladen, Bitget-Symbole/-Intervalle, Funding mit Anfangssatz vor dem Beginn und Lückenprüfung,
  Ratenbegrenzung, Wiederholungen nur bei vorübergehenden Fehlern, nur erlaubte Adressen, keine Weiterleitungen,
  Kommandozeile. `test_no_real_trading`: genau eine Netz-Ausnahme, deren Aufbau geprüft wird (nur GET, kein Inhalt,
  keine Zugangs-Kopfzeilen, feste Hosts, nur im Marktdaten-Paket benutzt).
* **Gegenprobe:** MD01–MD15 **15/15 entdeckt**.
* **Preflight:** STABIL – **1618/1618** Tests, 0 übersprungen (erster Lauf: 2 ruff-Befunde in einem Test, behoben,
  Wiederholung STABIL); Python 3.9: 1390 bestanden, 12 übersprungen (erwartet).
* **Nicht geprüft:** echte Antworten von Coinbase und Bitget (B-008).

## Nachtrag 2026-10-16 – AP-15 Marktüberwachung über WebSocket (D-026)

Umgebung: interaktive Cloud-Sitzung (Linux/amd64), Python 3.12.3, PostgreSQL 16. Kein Netz (B-008).

* **Neu:** `marketdata/stream.py` (Monitor, Kerzenbau, Lücken, Herzschlag, Not-Aus), `bitget_ws.py`, `coinbase_ws.py`,
  `public_ws.py` (WebSocket an zwei feste Adressen), `python -m trading.marketdata.watch`.
* **Tests:** `test_market_stream` (15, simulierter Server nach Drehbuch): Kerzen in Reihenfolge, Lücken werden nachgeladen
  und spätere Kerzen warten, Unterbrechung → Wiederverbindung mit wachsender Wartezeit, nicht nachladbare Lücke bleibt
  Lücke, Stille → Neuaufbau, 120 s ohne Daten → Not-Aus (einmal, bleibt), Zeit aus der Zukunft/fehlerhafte Kerze → Not-Aus
  und Ersatz per REST, Coinbase-Kerzen aus Handel nur bei lückenloser Beobachtung, Lebenszeichen, Kerzen laufen durch
  die Laufzeit-Engine, Beobachtungsprogramm. `test_public_ws` (3, echtes WebSocket-Gegenstück über ein lokales
  Socket-Paar): Handshake, geteilte Nachrichten, Ping/Pong, Schließen, Stille, Größenlimit, nur erlaubte Adressen.
  `test_no_real_trading`: zweite Netz-Ausnahme sichtbar eingetragen und geprüft (TLS mit Prüfung, keine Anmeldung).
* **Gefunden und behoben:** Nach einer Wiederverbindung wurde auch eine vollständige Bitget-Kerze verworfen; eine
  Nachricht im selben Paket wie die Verbindungsbestätigung ging verloren; Standard-Zeitlimit (30 s) kollidierte mit dem
  30-s-Lebenszeichen (jetzt 45 s, vom Monitor erzwungen).
* **Gegenprobe:** WS01–WS15 **15/15 entdeckt**.
* **Preflight:** STABIL – **1655/1655** Tests, 0 übersprungen; Python 3.9: 1424 bestanden, 13 übersprungen (Dashboard und
  WebSocket-Zugang brauchen Python ≥ 3.10).
* **Nicht geprüft:** echter Strom von Bitget und Coinbase (B-008).
