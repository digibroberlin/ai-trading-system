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

## Noch offen

- Abschnitt B: Performance (Kapitalkurve, Kennzahlen) [M13]
- Abschnitt C: PostgreSQL (Migrationen, Executor, Tests, Neustart-Skript), Docker
- Abschnitt D: Backtesting-Engine
- Abschnitt E: Testlücken aus dem Audit (H9) + Gegenproben, Gesamtverifikation, Dokumentation

`src/trading/persistence/pg_executor.py`, `tests/test_pg_*.py`, `tests/pg_workers.py`, `scripts/restart_check.py`,
`db/schema.sql`, `docker-compose.yml`, `Dockerfile` sind noch auf dem ALTEN Stand und passen nicht zum neuen Kern.
