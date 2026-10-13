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

## Noch offen

- Abschnitt D: Backtesting-Engine (`src/trading/backtest/`)
- Abschnitt E: Testlücken aus dem Audit (H9) + Gegenproben, Gesamtverifikation, Dokumentation (README, AGENTS, TESTBERICHT, AUDIT-Status)
