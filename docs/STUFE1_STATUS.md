# Stufe 1 – Fortschritt

Diese Datei wird nach jedem abgeschlossenen Abschnitt aktualisiert. Sie ist so geschrieben, dass die Arbeit
jederzeit ohne Informationsverlust fortgesetzt werden kann. Jeder Abschnitt endet mit einem Git-Commit.

Rückweg zum Stand vor Stufe 1: Git-Commit „Stand vor Stufe 1“ (`git log`), zusätzlich
`backups/projekt_2026-10-13_vor-stufe1.tar.gz` und `backups/datenbank_trading_2026-10-13_vor-stufe1.sql`.

## Abschnitt A – Kern, Agenten, Pipeline: **fertig**

Tatsächlich ausgeführt (Python 3.12.14, ohne Datenbank): **415 Tests bestanden**, ruff ohne Befund.
Die vier PostgreSQL-Testdateien wurden in diesem Abschnitt NICHT ausgeführt (Persistenzschicht noch alt).

Umgesetzt:
- H1 Instrument-Schlüssel (`domain/instrument.py`): Börse + Markttyp + Symbol in Kerze, Snapshot, Setup, Position, Trade,
  Bewertungskurs, Validator-Zustand, Handelsuniversum.
- H3 Kerzenprüfung (`marketdata/candles.py`), Executor nimmt nur abgeschlossene, gültige Kerzen in zeitlicher Reihenfolge;
  doppelte/ältere/fehlerhafte Kerzen werden protokolliert und verworfen; Lücken und extreme Kerzen werden protokolliert.
- M10 Funding nur über `FundingEvent` (Satz und Kurs aus den Daten), je Position höchstens einmal.
- M11 `close_position` (Signal/Eingriff), Pipeline: `propose`/`process_setup`, `propose_exits`/`process_exit`, MOMO `exit_signals`.
- M4/H5 Eine Risikorechnung (`safety/order_risk.py`): Zustand NACH der Order, alle Grenzen gleichzeitig
  (0,5 % je Trade, 2 % Gesamtrisiko, 100 % Brutto-Exposure, 5 % Verlust bei 10 % Schock, Margin, Liquidation, Notbremsen,
  veraltete Bewertung → Ablehnung). SORA nutzt dieselbe Funktion für die Endkontrolle.
- Zusätzlich aus Stufe 2 vorgezogen, weil im selben Code: H2 (Teil-Kerze konservativ), H4 (Tageswechsel nur vorwärts),
  M5 (Operationen im Speicher-Executor atomar), M6 (Referenzkurs verfällt), volle Margin bei Liquidation, Rundung auf 8 Stellen.

## Abschnitt B – Kapitalkurve und Performance (M13): **fertig**

`audit/performance.py` (Kapitalkurve, Drawdown aus bewerteter Equity, Sharpe/Sortino, realisiert/unrealisiert nach Kosten,
exakte Abstimmungsprüfung), `audit/stats.py` erweitert, AKI: `record_equity`, `report`.
Ausgeführt: 431 Tests ohne Datenbank bestanden.

## Abschnitt C – PostgreSQL, Migrationen, Docker: **fertig**

Tatsächlich ausgeführt: **520 Tests bestanden, 0 übersprungen** – im Docker-Dienst `tests` (Python 3.12.15) und auf dem Mac
(Python 3.12.14), jeweils gegen PostgreSQL 16.15. mypy `--strict` (48 Dateien) und ruff ohne Befund.

- Migrationen: `db/migrations/001_initial.sql` (bisheriges Schema), `002_instruments_and_exposure.sql` (rein additiv);
  Läufer `trading.persistence.migrate` (Versions-Tabelle, Prüfsummen, eine Transaktion je Migration, Advisory-Sperre).
- Echte Datenbank `trading`: vorher gesichert (`backups/datenbank_trading_2026-10-13_vor-migration-002.sql`), dann mit
  `--baseline-existing` migriert. Nachweis: `scripts/restart_check.py verify` meldet IDENTISCH (2 offene Positionen,
  Guthaben, 6 Protokolleinträge, Kettenende unverändert). Die Alt-Tabelle `market_state` blieb unberührt.
- `persistence/pg_executor.py` auf dem neuen Kern: alle Operationen (Order, Schließung, Kerze, Funding, Abfrage) über
  einen Ablauf „sperren → laden → Engine → Differenz + Protokoll speichern“. Zustand je Instrument in `instrument_state`.
- Datenbank-Gurte: Gesamtrisiko UND Brutto-Exposure beim COMMIT; REPEATABLE-READ-Umweg (Audit M1) geschlossen;
  Spaltenrechte auf `positions` eingeschränkt (Audit M2, Teil).
- Docker: `app` nur noch mit eingeschränkter Rolle (Audit H6), eigene Dienste `migrate` und `tests`,
  Laufzeit-Image ohne Tests/Werkzeuge.

Hinweis: Im Konto `restart-check` der echten Datenbank liegen zwei Alt-Positionen (je 40 Stück). Sie überschreiten die neue
Stress-Grenze (8,05 % statt 5 %). Sie bleiben bestehen; neue Trades in DIESEM Konto wären blockiert, bis sie geschlossen sind.

## Abschnitt D – Backtesting-Engine: **fertig**

Tatsächlich ausgeführt (Python 3.12.14 auf dem Mac, PostgreSQL 16.15): **599 Tests bestanden, 0 übersprungen**;
mypy `--strict` (55 Dateien) und ruff ohne Befund; Vorführung `python -m trading.backtest` läuft (2 Instrumente,
60 Tage Stundenkerzen). Im Docker-Dienst `tests` wurde dieser Stand noch NICHT ausgeführt (folgt in Abschnitt E).

- Paket `src/trading/backtest/`: `data.py` (geprüfter, unveränderlicher Datensatz mit Fingerabdruck; Schnittstelle
  `HistoricalDataSource` nur lesend, für spätere Coinbase-/Bitget-Daten; synthetische Quelle mit festem Startwert),
  `strategy.py` (Strategie sieht nur abgeschlossene Kerzen), `reference.py` (einfache Referenz-Strategie als Technik-Test),
  `engine.py` (Ablauf je Zeitpunkt: Kerzenschluss → Funding → Kapitalpunkt → Entscheidung → Ausführung zum Eröffnungskurs
  der nächsten Kerze, Ausstiege vor Einstiegen), `compare.py` (Vergleich nur bei gleichem Datensatz und gleichen Annahmen).
- Die Engine benutzt dieselbe Pipeline, denselben Risikomanager und denselben Executor wie der Paper-Betrieb – es gibt
  keine zweite, „vereinfachte“ Rechnung für Backtests.
- Tests: `tests/test_backtest_data.py` (39), `tests/test_backtest_engine.py` (37, mit von Hand nachgerechneten Beträgen),
  `tests/test_backtest_pg.py` (3: Backtest über PostgreSQL liefert bit-identische Trades, Kapitalkurve und Hash-Kette).
- Gegenprobe: 28 absichtlich eingebaute Fehler (u. a. Blick in die Zukunft, falscher Ausführungskurs, vergessenes Funding,
  hingenommenes defektes Protokoll) – **alle 28 von den Tests bemerkt**. Drei wurden zunächst NICHT bemerkt; dafür kamen
  drei zusätzliche Tests hinzu.
- Während der Arbeit korrigierte Test-Erwartungen (Fehler im Test, nicht im Programm): Handrechnung 7,817 × 5,117 =
  39,999589 (ich hatte 39,999639 notiert); Erwartung zur Lücken-Erkennung bei vertauschten Kerzen; bei extrem hohen Kosten
  blockiert RIN korrekt alle Trades („Stop zu eng“) – daraus wurde ein eigener Test.
- Bekannte Eigenschaft: Die Datenbank führt je Instrument EINE Marktzeitleiste. Ein zweiter Backtest über denselben Zeitraum
  in derselben Datenbank wird laut abgebrochen (getestet). Backtests laufen deshalb standardmäßig im Speicher.

## Abschnitt E – Testlücken, Gegenprobe, Gesamtverifikation, Dokumentation: **fertig**

Tatsächlich ausgeführt am 2026-10-14:
- **606 von 606 Tests bestanden, 0 übersprungen** – auf dem Mac (Python 3.12.14) und im Docker-Dienst `tests` (Python 3.12.15),
  jeweils gegen PostgreSQL 16.15. Ohne Datenbank unter Python 3.9.6: 512 bestanden, 94 übersprungen.
- Mehrprozess- und Gleichzeitigkeitstests dreimal wiederholt: je 60 von 60.
- mypy `--strict` (55 Dateien) und ruff (`src`, `tests`, `scripts`) ohne Befund.
- **Gegenprobe `scripts/mutation_check.py`: 193 eingebaute Fehler, 193 entdeckt** (135 Kern, 28 Backtest, 30 Datenbank).
  Im ersten Durchlauf blieben 14 unbemerkt → 13 Lücken mit Tests geschlossen, 1 Stelle war toter Code und wurde entfernt.
- Docker: `migrate` (beide Migrationen angewandt), `app` (Vorführung), Backtest im Container – mit denselben Ergebnis-Fingerabdrücken wie auf dem Mac.
- Echte Datenbank `trading` unverändert (`scripts/restart_check.py verify`: IDENTISCH); keine Test-Datenbank übrig; kein `__pycache__`, keine `.env` im Projekt.

Dokumentation aktualisiert: `README.md`, `AGENTS.md`, `docs/TESTBERICHT.md` (neu; der alte Bericht liegt in `docs/TESTBERICHT_PHASE1-3.md`),
`docs/AUDIT.md` (Abschnitt „Stand nach Stufe 1“), `docs/BACKTESTING.md` (neu).

## Stufe 1 ist abgeschlossen

Offen bleiben die im Audit als Stufe 2 und 3 eingeplanten Punkte (siehe `docs/AUDIT.md`, „Stand nach Stufe 1“):
M3 (zentraler PAPER-Schutz), M7, M8, M2-Rest, H7, M9, M12, M14 sowie Börsenregeln je Instrument, echte historische Daten und echte Strategien.

## Arbeitsumgebung (für eine spätere Fortsetzung)

- Tests: siehe README „Tests ausführen“. Die Passwort-Datei liegt außerhalb des Projektordners (nie eine `.env` ins Projekt legen – OneDrive).
- Compose wird als `docker-compose --env-file <Passwort-Datei>` aufgerufen; Dienste: `db`, `migrate`, `app`, `tests`.
- Jeder Abschnitt endet mit einem Git-Commit (`git log --oneline`). Sicherungen vor Stufe 1 liegen in `backups/`.
