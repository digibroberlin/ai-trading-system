# Unabhängiges Audit des Trading-Bot-Projekts

Stand des geprüften Codes: 2026-10-13 (nach Phase 3, vor Stufe 1) · Prüfer: unabhängige Architektur-Durchsicht ·
Modus: ausschließlich PAPER, ausschließlich synthetische Daten · Während des Audits wurde **keine** Projektdatei verändert
(Checksummen aller 75 Dateien vorher/nachher identisch).

> Der Umsetzungsstand der Befunde steht am Ende dieser Datei („Status nach Stufe 1“).

## Gesamturteil

Die Architektur trägt, und der Rechenkern hat den unabhängigen Prüfungen standgehalten. Es gibt **keinen kritischen** Befund,
aber **9 hohe, 14 mittlere und 10 niedrige**. Mit der Backtesting-Engine kann begonnen werden, wenn sechs Vorarbeiten („Stufe 1“)
an ihren Anfang gestellt werden.

## Was ausgeführt wurde

- **Ausgangsbasis:** 354 von 354 Tests bestanden (Python 3.12.14, PostgreSQL 16.15).
- **Eigene Prüfungen der Engine:** 12 Szenarien plus ein Zufallstest über 20 000 Schritte – keine Verletzung des 2-%-Limits
  (Höchstwert 1,99 %), keine Abweichung in der Buchhaltung.
- **Eigene Prüfungen der Datenbank:** 8 Szenarien in kurzlebigen Test-Datenbanken.
- **Teststärke:** 32 neue, absichtlich eingebaute Fehler in einer Kopie – **14 blieben unentdeckt**.

Nicht geprüft: Lasttest, Neustart des Macs, Testabdeckung in Prozent (kein Werkzeug installiert), API (existiert noch nicht).

## Urteil zu den zehn Prüfpunkten

| # | Bereich | Urteil |
|---|---|---|
| 1 | Agenten-Architektur | Tragfähig. MOMO, KIBO, YUZU sind nur Schnittstellen; Ablauf synchron; Modell für Börse/Instrument fehlt (H1). |
| 2 | Executor und Positionsgrößen | Rechnung stimmt; die Kerzen-Verarbeitung hat drei echte Fehler (H1–H3). |
| 3 | 2-%-Limit bei Parallelität | Hauptmechanismus (Konto-Sperre) hält. Zweiter Gurt in der DB hat eine Lücke (M1), Kennzahl unscharf (M4), Kurslücken unbegrenzt (H5). |
| 4 | PostgreSQL, Neustart, Protokoll | Atomar und neustartfest. Es fehlen Wiederverbindung (H7) und Migrationen (H8). |
| 5 | Docker, Python 3.12, Tests | Läuft. App-Container hat Admin-Zugang (H6); Tests haben Lücken (H9). |
| 6 | Sicherheit, Datenverlust | Keine Schlüssel im Code, parametrisierte SQL-Abfragen. Offen: H6, M2, M3, fehlende Backups (M14). |
| 7 | 24/7-Eignung | Noch nicht gegeben. Gemessen: 470 Transaktionen/s (lokal), ca. 116 000 Kerzen/s in der Engine – für Backtests ausreichend. |
| 8 | Coinbase, Bitget, Spot, Futures | Erst nach H1 und M10 erweiterbar. |
| 9 | Aussagekraft der Tests | Gut bei dem, worauf sie zielen; 14 von 32 neuen Fehlern unentdeckt. |
| 10 | Bereit für Backtesting | Ja, mit Stufe 1 als erstem Arbeitspaket. |

## Hohe Befunde

| Nr. | Problem (mit Beleg) | Datei | Lösung |
|---|---|---|---|
| H1 | Kerzen und Bewertungskurse kennen nur das Symbol. Eine Kerze wirkt auf die Spot- **und** die Futures-Position desselben Symbols (im Test schloss sie die Spot-Position). | `domain/models.py`, `execution/paper_executor.py`, `db/schema.sql` | Instrument-Schlüssel aus Börse, Markttyp und Symbol in Kerze, Snapshot, Position und Marktzustand. |
| H2 | Ein Stop im Rest der Einstiegskerze wird übersehen (Position ab 12:00:30, Tief 98 bei Stop 99: Position lief weiter). Schönt Live-Paper-Ergebnisse. | `execution/paper_executor.py` | Ausstiege auf Ticks/Sekundenkerzen prüfen oder die Einstiegskerze konservativ werten. |
| H3 | Kerzen werden ungeprüft und ohne Reihenfolge-Kontrolle verarbeitet: wiederholte ältere Kerze belastete Funding doppelt (0,40 → 0,80); NaN-Kerze wirft Exception; Ausreißer-Tief löst den Stop aus. | `execution/paper_executor.py` | Kerzen validieren, nur streng aufsteigend je Instrument annehmen, Funding-Zeitstempel nie zurücksetzen. |
| H4 | Tagesverlust-Notbremse lässt sich über die Uhr eines zweiten Prozesses zurücksetzen (A blockiert, B fragt mit anderer Uhr ab, A handelt wieder). | `execution/paper_executor.py`, `persistence/pg_executor.py` | Tag aus der Datenbank-Zeit ableiten, nur vorwärts wechseln, Tests mit verschiedenen Uhren. |
| H5 | Kein Limit für Gesamt-Exposure und Kurslücken: 5 Positionen = 124 % Exposure bei 1,05 % „geplantem“ Risiko; −12 % Lücke kostete 14,9 % des Kapitals. | `safety/limits.py`, `safety/risk_manager.py`, `agents/sora_rules.py` | Feste Obergrenzen für Brutto-Exposure und ein Stress-Limit (Verlust bei vorgegebener Kurslücke). |
| H6 | App-Container erhält den Datenbank-Administrator (Anmeldung als Superuser aus dem Container möglich); Tests/Entwicklerwerkzeuge im selben Image. | `docker-compose.yml`, `Dockerfile` | Eigener Test-Dienst, schlankes Laufzeit-Image. |
| H7 | Nach Verbindungsabbruch erholt sich der Executor nicht (alle Aufrufe scheitern bis zum Prozess-Neustart). Hängender Prozess mit Konto-Sperre stoppt die Stop-Verarbeitung (Abbruch nach 10 s). | `persistence/db.py`, `persistence/pg_executor.py`, `persistence/pg_event_log.py` | Wiederverbindung mit Wartezeit, `idle_in_transaction_session_timeout`, Überwachung/Alarm. |
| H8 | Keine Schema-Migrationen: Schema läuft nur beim allerersten Start; jede Änderung verlangt Handarbeit oder Datenlöschung. | `db/schema.sql`, `db/Dockerfile` | Nummerierte Migrationen mit Versions-Tabelle, Backup vor jeder Migration. |
| H9 | Tests übersahen 14 von 32 neuen Fehlern (u. a. Drawdown-Bremse im Executor wirkungslos, Liquidationspuffer ignoriert, Short-Liquidation nie erkannt, Funding-Zeitstempel nicht fortgeschrieben). | `tests/` | Je überlebendem Fehler ein Test, Zufallstests der Invarianten, Mutationstests als Routine. |

Die früheren Gegenproben (27 von 27 entdeckt) waren zu freundlich gewählt: Sie zielten auf Stellen, von denen bekannt war, dass sie getestet sind.

### Die 14 übersehenen Fehler (Mutationen)

1. Liquidations-Deckel (Verlust ≤ Margin) entfernt · 2. Freie Margin ignoriert unrealisierte Verluste · 3. Höchststand fällt mit (Drawdown-Bremse wirkungslos) ·
4. Short: Kurslücke über Stop wird zum Stop-Kurs gefüllt · 5. Short-Liquidation nie erkannt · 6. Kurs aus Snapshot wird nicht als Bewertung übernommen ·
7. Funding-Zeitstempel wird nicht fortgeschrieben · 8. Margin-Prüfung ohne Einstiegsgebühr · 9. Liquidationspuffer ignoriert (Faktor 1 statt 1,25) ·
10. Negatives Funding senkt das geplante Risiko · 11. Kerzen-Volumen < 0 erlaubt · 12. SORA: Liquidations-Sicherheitsfaktor 1,25 statt 1,5 ·
13. RIN: Spread nicht in den Round-Trip-Kosten · 14. Statistik: Gewinnrate zählt Nullsummen-Trades als Gewinn.

## Mittlere Befunde

| Nr. | Problem | Datei | Lösung |
|---|---|---|---|
| M1 | DB-Trigger für das 2-%-Limit unter `REPEATABLE READ` umgehbar (240 statt erlaubter 200). | `db/schema.sql` | Im Trigger die Konto-Zeile ändern oder andere Isolationsstufen ablehnen. |
| M2 | App-Rolle darf `risk_at_open`, `equity`, `stop_loss` überschreiben und beliebige Konten anlegen (zweites Konto = eigenes 2-%-Budget). | `db/schema.sql` | Spaltenrechte einschränken, Konten nur administrativ, ein Limit über alle Konten. |
| M3 | PAPER-Schutz wird nur in der Demo aufgerufen; `CDP_API_KEY_*`, `API_KEY`, Kleinschreibung werden nicht erkannt. | `config.py`, `demo.py` | Zentraler Startpunkt für jeden Prozess, breitere Erkennung. |
| M4 | Kennzahl zählt bezahlte Einstiegsgebühren nicht mit: gemessen am Kapital vor den Trades 2,105 %. | `execution/paper_executor.py`, `db/schema.sql` | Definition festlegen (Entscheidung des Eigentümers). |
| M5 | Speicher-Engine nicht atomar: bei Protokoll-Ausfall blieb eine Position ohne Protokolleintrag zurück. | `execution/paper_executor.py` | Erst protokollieren, dann Zustand ändern. |
| M6 | Kurssprung-Filter vergleicht mit dem Kurs der letzten Order; nach +25 % in 6 h dauerhaft blockiert, auch über Neustarts. | `safety/data_validator.py` | Jeden Tick einspeisen, Zeitfenster/Verfall, Rücksetz-Verfahren. |
| M7 | Wiederholte Order nach erfolgreichem Fill liefert „abgelehnt, Duplikat“ ohne Position. | `persistence/pg_executor.py` | Ursprüngliches Ergebnis speichern und zurückgeben. |
| M8 | Unter Konkurrenz Ablehnung statt Verkleinerung (2 von 8 Pipelines führten aus); Test erlaubt „1 bis 5“. | `pipeline.py`, `tests/test_pg_multiprocess.py` | Größe in der gesperrten Transaktion berechnen, exakte Erwartung. |
| M9 | `verify()`/`of_type()` laden jedes Mal das ganze Protokoll. | `persistence/pg_event_log.py` | Prüfung in Abschnitten, Kettenende extern ablegen. |
| M10 | Ohne Funding-Satz wird stillschweigend 0 gerechnet; Zeiten 00/08/16 Uhr fest verdrahtet. | `execution/costs.py`, `execution/paper_executor.py` | Funding zur Pflicht machen, Zeitplan je Instrument. |
| M11 | Keine Schließung auf Signal, kein Not-Aus, keine Teilschließung. | `execution/paper_executor.py` | `close_position` ergänzen. |
| M12 | Images und Pakete nicht festgeschrieben; Container mit freiem Internetzugang. | `Dockerfile`, `pyproject.toml` | Versionen mit Prüfsummen, Netz ohne Ausgang. |
| M13 | Für „risikobereinigt“ fehlen Kapitalkurve und Sharpe; Drawdown nur aus geschlossenen Trades. | `audit/stats.py`, `agents/aki.py` | Laufend bewertete Kapitalkurve und Kennzahlen. |
| M14 | Kein Backup; Drawdown-Bremse dauerhaft ohne Freigabe-Verfahren. | `docker-compose.yml` | Regelmäßige Sicherung mit Rückspiel-Test. |

## Niedrige Befunde

- Liquidation wird zum Liquidationskurs statt mit voller Margin gerechnet; die Deckel-Zeile war toter Code (`execution/paper_executor.py`).
- Jeder Prozess mit App-Zugang kann formal gültige, aber erfundene Protokolleinträge anhängen (`persistence/pg_event_log.py`).
- Beträge werden nicht gerundet (bis zu 28 Stellen).
- Datei-Protokoll ohne Sperre gegen zwei Schreiber (`audit/event_log.py`).
- Test-Hook `fault_hook` im Produktivcode (`persistence/pg_executor.py`).
- Passwort kurz in den Prozess-Argumenten (`db/00_roles.sh`).
- Limits im selben Prozess nur per Konvention unveränderlich (relevant, sobald KI-geschriebener Strategie-Code läuft).
- Uhr-Abweichung zur Börsenzeit führt zu „veralteten“ Daten (Zeitsynchronisation nötig).
- KI-Schnittstelle ohne Zeitlimit und Kostenbudget (`ai/provider.py`).
- Dokumentation behauptet mehr als gilt („verweigert den Start“ nur in der Demo; „Verletzung in keiner Variante“ durch M1 widerlegt).

## Wurden früher fehlgeschlagene Tests echt behoben?

| Fall | Geändert wurde | Bewertung |
|---|---|---|
| Funding 0,4 gegen 0,408 | Test | Berechtigt (Erwartung war falsch). |
| Spot-Margin vor dem 2-%-Limit | Test + neuer Test | Berechtigt. |
| RIN-Spread-Grenze | Test + neuer Test | Berechtigt. |
| Veraltete Setups im Synthetik-Lauf | Test | Berechtigt (RIN arbeitete korrekt). |
| Gesamtrisiko 2,0002 % | Code | Echte Änderung, definiert aber die Kennzahl um (M4). |
| Wartekreis im DB-Trigger | Code | Echte Behebung; Trigger hat aber die Lücke M1. |
| Fehler-Hook-Test | Test | Berechtigt. |

Kein Test wurde abgeschwächt, um einen Code-Fehler zu verdecken.

## Maßnahmenplan

**Stufe 1 (erstes Arbeitspaket der Backtesting-Engine):** H1 · H3 + M10 · M11 · M13 · H5 + M4 · H9.

**Stufe 2 (vor weiterem Mehrprozess-/Paper-Betrieb):** H4, H6, H8, M1, M2, M3, M5, M7, M8, H2, M6.

**Stufe 3 (vor 24/7-Betrieb auf dem Server):** H7, M9, M12, M14, Überwachung, Lasttest.

---

# Stand nach Stufe 1 (2026-10-14)

Dieser Abschnitt hält fest, was aus dem Audit inzwischen umgesetzt ist. Der Text oberhalb ist der unveränderte Befund vom 2026-10-13.
Belege (Testzahlen, Gegenproben) stehen in [TESTBERICHT.md](TESTBERICHT.md).

## Hohe Befunde

| Nr. | Stand | Was getan wurde / was fehlt |
|---|---|---|
| H1 | **behoben** | Instrument = Börse + Markttyp + Symbol in Kerze, Snapshot, Setup, Position, Trade, Bewertungskurs, Validator und Datenbank (`instrument_state`). Eine Spot-Kerze berührt die Futures-Position desselben Paares nicht mehr (getestet, auch in PostgreSQL). |
| H2 | **behoben** (vorgezogen) | Eine Kerze, die vor dem Einstieg begann, zählt nur für den Stop (vorsichtig); das Ziel wird in ihr nicht gewertet. |
| H3 | **behoben** | Vollständige Kerzenprüfung; nur abgeschlossene Kerzen in zeitlicher Reihenfolge; doppelte/ältere/fehlerhafte werden verworfen und protokolliert; Lücken und auffällige Kerzen werden protokolliert. |
| H4 | **behoben** (vorgezogen) | Der Handelstag wechselt nur vorwärts; Tag und Tagesstart-Kapital liegen in der Datenbank und überstehen Neustarts (getestet mit falscher Uhr und über drei Tage). |
| H5 | **behoben** | Zwei neue Grenzen: 100 % Brutto-Exposure und 5 % Verlust bei 10 % Kursschock – gleichzeitig mit allen anderen, auf den Zustand nach der Order. Exposure zusätzlich als Datenbank-Gurt. |
| H6 | **behoben** (vorgezogen) | Der Container `app` kennt nur die eingeschränkte Rolle; eigene Einmal-Dienste `migrate` und `tests`; Laufzeit-Image ohne Tests und Werkzeuge. |
| H7 | offen | Wiederverbindung zur Datenbank und Zeitlimit für hängende Transaktionen fehlen weiterhin (Stufe 3). |
| H8 | **behoben** (vorgezogen) | Nummerierte Migrationen mit Versions-Tabelle und Prüfsummen. Die bestehende Datenbank wurde nach einem Backup übernommen; Zustand danach nachweislich identisch. |
| H9 | **behoben** | Alle 14 damals übersehenen Fehler werden jetzt bemerkt. Die Gegenprobe ist ein dauerhaftes Werkzeug (`scripts/mutation_check.py`) mit 193 Einträgen – alle entdeckt. |

## Mittlere Befunde

| Nr. | Stand | Was getan wurde / was fehlt |
|---|---|---|
| M1 | **behoben** (vorgezogen) | Der Datenbank-Gurt schreibt die Konto-Zeile; unter `REPEATABLE READ`/`SERIALIZABLE` bricht die zweite Transaktion ab (getestet). |
| M2 | teilweise | Offene Positionen: nur noch Schließen und Funding-Fortschritt änderbar. **Offen:** Die Anwendungsrolle darf weiterhin den Kontostand schreiben und Konten anlegen. |
| M3 | offen | `load_settings()` wird von Vorführung und Backtest aufgerufen, nicht von einem zentralen Startpunkt; die Schlüssel-Erkennung ist nicht verbreitert. |
| M4 | **behoben** (Ihre Festlegung) | Geplantes Gesamtrisiko ≤ 2 % der Netto-Equity nach bereits angefallenen Kosten; Zustand nach der Order; erwartete Gebühren, Slippage und Funding-Puffer eingerechnet; unter der Konto-Sperre atomar. |
| M5 | **behoben** (vorgezogen) | Jede Operation des Speicher-Executors ist unteilbar; scheitert etwas (auch das Protokoll), wird alles zurückgesetzt. |
| M6 | **behoben** (vorgezogen) | Der Referenzkurs des Sprung-Filters verfällt nach 15 Minuten; verarbeitete Kerzen schreiben ihn fort. |
| M7 | offen | Eine wiederholte Order-ID liefert weiterhin „Duplikat“ statt des ursprünglichen Ergebnisses. |
| M8 | offen | Unter Konkurrenz mehrerer Prozesse wird ein SORA-Vorschlag weiterhin abgelehnt statt neu berechnet (sicher, aber nicht optimal). |
| M9 | offen | `verify()` lädt weiterhin das ganze Protokoll. |
| M10 | **behoben** | Funding nur noch aus ausdrücklichen Ereignissen (Satz und Kurs aus den Daten), je Position höchstens einmal; ohne Funding-Satz kein Futures-Trade. |
| M11 | weitgehend | Schließen auf Signal und per Eingriff (`close_position`), durchgängig bis in Pipeline, Backtest und Datenbank. **Offen:** Teilschließung und ein globaler Not-Aus. |
| M12 | **teilweise behoben** (AP-05, D-010) | Alle Python-Pakete fest mit Prüfsummen (`requirements/*.lock`), Prüfung auf bekannte Lücken in GitHub Actions. Offen: Basis-Image per Digest, Container ohne Internetzugang (AP-27). |
| M13 | **behoben** | Laufend bewertete Kapitalkurve, Drawdown daraus, Sharpe/Sortino, realisiert/unrealisiert, alles netto; exakte Abstimmung des Endkapitals. |
| M14 | offen | Kein regelmäßiges Backup; kein Freigabe-Verfahren nach der Drawdown-Bremse. (Vor Stufe 1 wurden einmalige Sicherungen nach `backups/` gezogen.) |

## Niedrige Befunde

Behoben: Liquidation kostet jetzt die gesamte Margin · Beträge werden auf 8 Stellen gerundet (immer zu Ungunsten des Händlers) ·
die Aussage „verweigert den Start“ im README ist richtiggestellt.
Offen: erfundene, formal gültige Protokolleinträge durch einen Prozess mit App-Zugang · Datei-Protokoll ohne Sperre gegen zwei Schreiber ·
Test-Hook `fault_hook` im Produktivcode · Passwort kurz in den Prozess-Argumenten · Limits im selben Prozess nur per Konvention geschützt ·
Zeitsynchronisation · KI-Schnittstelle ohne Zeitlimit und Kostenbudget.

## Neu aufgefallen während Stufe 1

* **Das Stress-Limit ist die engste Grenze.** 5 % Verlust bei 10 % Schock begrenzt die ungesicherte Gesamtgröße auf knapp 50 % des Kapitals; die 100-%-Exposure-Grenze greift selten.
* **Der Stress-Test hat keinen Datenbank-Gurt.** Er wird nur in der Anwendung gerechnet (unter der Konto-Sperre).
* **Eine Marktzeitleiste je Datenbank.** Ein zweiter Backtest über denselben Zeitraum in derselben Datenbank wird abgebrochen (getestet, gewollt).
* **Alt-Positionen** im Konto `restart-check` der lokalen Datenbank überschreiten die neue Stress-Grenze (8,05 %); neue Trades dort sind blockiert, bis sie geschlossen sind.
* **Keine Börsenregeln je Instrument** (Mindestgröße, Preis- und Mengenschritte) – nötig, bevor Ergebnisse auf echten Daten belastbar sind.
