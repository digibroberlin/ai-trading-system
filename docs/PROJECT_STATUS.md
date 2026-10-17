# Projektstatus

Wird bei jedem Arbeitspaket aktualisiert. So geschrieben, dass eine neue Sitzung (oder eine Cloud-Routine) ohne
Informationsverlust weitermachen kann.

> **AP-20 ABGESCHLOSSEN (2026-10-17, Auftrag des Eigners):** historische Profitabilitätsanalyse und Agenten-Vergleich auf
> echten Daten (Bitstamp BTC/USD 2017–2026) – **kein belastbarer Vorteil**; Bericht
> [`docs/PROFITABILITAETSANALYSE.md`](PROFITABILITAETSANALYSE.md). Laut Auftrag nach dieser Phase **gestoppt**: Wie es
> weitergeht (Netzfreigabe B-008 für die Gegenprobe auf Börsendaten, Demo-Einrichtung), entscheidet der Eigner.
> Es wurde nicht gehandelt.

## Sperre

gesperrt von Cloud-Sitzung (interaktiv, Eigner-Auftrag) seit 2026-10-17 10:18 UTC für AP-20 (Profitabilitätsanalyse)

(Format: `gesperrt von <Sitzung/Routine> seit <Datum Uhrzeit UTC> für AP-xx` – Sperren älter als 3 Stunden gelten als verwaist.
Mehrere Sperren sind erlaubt: eine Zeile je Arbeitspaket; jede Sitzung ändert nur ihre eigene Zeile.)

## Aktueller Stand (2026-10-17) – für die Wiederaufnahme (Ergänzung des Eigners, Punkt 6)

* **Handel:** keiner. Es läuft kein Bot, kein Paper-, Demo- oder Echtgeld-Betrieb; Handel ist standardmäßig aus.
* **Letzter Git-Checkpoint:** `develop` auf GitHub (Abschlussbericht nach dem geprüften Stand `0dd5d3e`, nur Doku).
* **Letzter STABLE-Stand:** `stable/005-release-candidate` = `develop` @ `0dd5d3e` – Preflight STABIL am 2026-10-17
  01:28 UTC: 1821/1821 Tests (0 übersprungen), Gegenprobe 513/513, Python 3.9: 1575 bestanden, GitHub Actions grün.
  Der Tag ist nur lokal gesetzt: Die Cloud darf keine Tags hochladen (403, B-007). **`main` verändert Claude nicht selbst**
  (Ergänzung 2026-10-16): `main` = `ad6cc9f` (`stable/004-safety-stage2`); der Eigner übernimmt den Stand (B-009).
* **Zweige:** `main` (stabil, nur durch den Eigner), `develop` (Integration), Arbeitszweige `ap/NN-name`.
* **Was fehlt (alles wartet auf den Eigner):** Gegenprobe von AP-20 auf Coinbase-/Bitget-Daten, ETH und echten
  Funding-Sätzen (Netzfreigabe B-008) · AP-29 (Entscheidung B-003) · Server (B-005) · KI-Budget (B-004, optional) ·
  `main`/Tags (B-009, B-007).
* **Als Nächstes:** Entscheidung des Eigners zu den Empfehlungen in `docs/PROFITABILITAETSANALYSE.md`, Abschnitt 11
  (keine Strategie mit Gewinnerwartung; Demo höchstens zur technischen Erprobung mit `trend-4h`, BTC Spot).
* **Arbeitsort:** die Cloud (D-008); der Mac-Ordner ist nur Sicherung.

## In Arbeit (Zwischenstand)

* nichts

## Zuletzt erledigt

* **AP-20 Historische Profitabilitätsanalyse und Agenten-Vergleich** (interaktive Cloud-Sitzung, 2026-10-17, D-034):
  Daten: öffentliche Kopie der Bitstamp-BTC/USD-Minutenkerzen (Börsen-Schnittstellen gesperrt, B-008), streng geprüft,
  Lücken ausgewiesen. Protokoll vorab (`docs/AP20_PROTOKOLL.md`, 3 Nachträge), Training 2017–2022, Validierung 2023–2024,
  Test 2025–2026 eingefroren (223 Zellen, Prüfsumme) und einmal gerechnet; 7 Strategien + 3 Varianten auf 4-h-Kerzen ×
  Agenten-Varianten V0, A–E, S × Kostenszenarien F, S, K, Stress; Not-Aus mit simulierter monatlicher Prüfung und streng.
  Ergebnis: kein Kandidat, keine Strategie belastbar profitabel; `trend-4h` in allen Abschnitten leicht positiv, nicht
  belastbar; KIBO/YUZU ohne Nutzen für Trendfolger. Nebenbei behoben: Warnung „Notbremse fast erreicht“ (stiller
  Stillstand), KI-Budget 0 sperrt auch Anbieter zum Preis 0. Ergebnistabellen `docs/ap20/`.
* **AP-30 Abschlussprüfung und Abschlussbericht** (interaktive Cloud-Sitzung, 2026-10-17): volle Prüfung auf dem
  Endstand `0dd5d3e` STABIL – 1821/1821 Tests mit PostgreSQL, Gegenprobe 513/513, Python 3.9 1575 bestanden, GitHub
  Actions grün; gekennzeichnet als `stable/005-release-candidate` (Tag lokal, B-007). Unterwegs gefunden und behoben:
  Die Gegenprobe passte nacheinander nicht mehr ins 2-Stunden-Zeitlimit der Cloud → läuft jetzt in fünf gleichzeitigen
  Teilen (58 min); dabei zeigte die strenge Grundprobe, dass gleichzeitige Testläufe sich an der serverweiten Datenbank-
  Rolle stören → Sperre und eindeutige Namen in der Testumgebung (ein Lauf vorher ehrlich als NICHT STABIL protokolliert).
  Abschlussbericht: `docs/ABSCHLUSSBERICHT.md`.
* **AP-34 Sicherheitsnachweis und verbindliche Ergänzung des Eigners** (interaktive Cloud-Sitzung, 2026-10-16, D-033):
  Ergänzung dauerhaft in `CLAUDE.md` und `MASTER_PLAN.md` (Abschnitt 0). Automatisierte Tests belegen: keine Order-,
  Abhebungs- oder Signatur-Wege in Code, Skripten, Server-Dateien und Containern; nur Modus PAPER; nur bekannte
  Fremdpakete, kein Börsen-SDK; Netz nur lesend mit Laufzeit-Sperren für Pfade und WebSocket-Nachrichten.
  `scripts/check_api_config.py` prüft Umgebung und Passwort-Dateien auf Börsen-Zugänge und Echtgeld-Schalter, ohne Werte
  auszugeben (Cloud-Umgebung: OK). Unabhängige Gegenprüfung: 12 bestätigte Test-Lücken, alle geschlossen.
  Preflight STABIL 1817/1817; Python 3.9: 1571 bestanden, 16 übersprungen (erwartet); Gegenprobe AS01–AS20 20/20.
* **D-031 Funding-Sätze für Futures im Betrieb und D-032 Untersuchung auf Knopfdruck** (interaktive Cloud-Sitzung,
  2026-10-16): Der Paper-Dienst holt nach jeder Futures-Kerze die Abrechnungen der Börse (vor der Kerze, je Position
  höchstens einmal, Not-Aus nach 12 h ohne Abrechnung). `python -m trading.research.study` führt AP-20 mit einem
  Befehl aus, sobald Netz da ist (B-008). Preflight STABIL 1782/1782; Python 3.9: 1537 bestanden; Gegenprobe LF01–LF07
  7/7, SD01–SD03 3/3.
* **AP-28 Server vorbereitet** (interaktive Cloud-Sitzung, 2026-10-16, D-030): Härtung (SSH nur Schlüssel, Firewall nur
  SSH, Updates, fail2ban) mit Aussperr-Schutz, Installation mit systemd (Start, tägliche Sicherung, Alarm alle 5 min),
  Alarm-Prüfung inkl. KI-Budget-Sperre, Serverkosten im Netto. Nur Trockenlauf; Anleitung `docs/DEPLOYMENT.md` (B-005).
  Preflight STABIL 1759/1759; Python 3.9: 1514 bestanden; Gegenprobe DP01–DP11 11/11.
* **AP-27 Betrieb vorbereitet** (interaktive Cloud-Sitzung, 2026-10-16, D-029): Paper-Dienst `python -m trading.runtime`
  (Monitor → Engine mit allen sechs Agenten → Paper-Executor in PostgreSQL, Dashboard mit Gesundheit), doppelte
  Freigabe, Wächter mit Neustart, Produktions-Compose (Datenbank ohne Port, Dashboard nur lokal) und Skripte
  start/stop/status/backup. **Nicht gestartet.** Preflight STABIL 1737/1737; Python 3.9: 1492 bestanden; Gegenprobe
  BE01–BE13 13/13.
* **AP-26 Sicherungen mit Rückspiel-Test** (interaktive Cloud-Sitzung, 2026-10-16, D-028, Audit M14):
  `scripts/backup.py create|verify|prune` – pg_dump mit Prüfsumme und Beschreibung, Rückspiel-Test in eine
  Wegwerf-Datenbank mit Prüfung von Zeilen, Schema und Ereigniskette. Preflight STABIL 1701/1701; Python 3.9: 1461
  bestanden; Gegenprobe BK01–BK10 10/10 (mit Datenbank).
* **AP-25 Logging, Gesundheit, Kennzahlen** (interaktive Cloud-Sitzung, 2026-10-16, D-027): JSON-Logging, das Geheimnisse
  schwärzt; Gesundheit OK/WARN/CRITICAL (Not-Aus, Datenbank, Marktdaten, Engine, KI-Budget) unter `/api/health` (503 bei
  CRITICAL) und Kennzahlen unter `/metrics` (Prometheus-Text). Preflight STABIL 1683/1683; Python 3.9: 1451 bestanden;
  Gegenprobe OP01–OP11 11/11.
* **AP-15 Marktüberwachung über WebSocket** (interaktive Cloud-Sitzung, 2026-10-16, D-026): geschlossene, geprüfte
  Kerzen aus dem öffentlichen Strom (Bitget-Kerzen, Coinbase aus Handel), Wiederverbindung mit wachsender Wartezeit,
  Lücken per REST nachgeladen und in Reihenfolge, 120 s ohne Daten → Not-Aus, Zeit aus der Zukunft/fehlerhafte Kerze →
  Not-Aus. Live-Ansicht ohne Handel: `python -m trading.marketdata.watch`. **Live-Probe offen** (B-008).
  Preflight STABIL 1655/1655; Python 3.9: 1424 bestanden; Gegenprobe WS01–WS15 15/15.
* **AP-14 Marktdaten-Adapter** (interaktive Cloud-Sitzung, 2026-10-16, D-025): historische Kerzen von Coinbase (Spot)
  und Bitget (Spot, USDT-Futures inkl. Funding), nur lesend und ohne Schlüssel; strenge Prüfung (nichts wird
  aufgefüllt), Zwischenspeicher je Tag mit Prüfsumme, Ratenbegrenzung, wenige Wiederholungen. Einziger Netzzugang des
  Systems, im Schutztest sichtbar eingetragen. **Echter Testabruf offen:** die Cloud-Netzregel sperrt die Börsen-Hosts
  (B-008). Preflight STABIL 1618/1618; Python 3.9: 1390 bestanden; Gegenprobe MD01–MD15 15/15.
* **AP-22 KI-Anbieter-Anschluss** (interaktive Cloud-Sitzung, 2026-10-16, D-024): Claude-Anschluss hinter dem
  Kosten-Orchestrator – Verbrauch aus der Anbieter-Antwort, Prompt-Caching der festen Anweisung, 30 s Zeitlimit, keine
  automatischen Wiederholungen, abgeschnittene Antworten werden nicht genutzt. **Nicht eingeschaltet:** braucht Freigabe,
  Schlüssel, installiertes SDK und aktuelle Preistabelle (B-004); Budget bleibt 0. Preflight STABIL 1580/1580;
  Python 3.9: 1352 bestanden; Gegenprobe AN01–AN10 10/10.
* **AP-13 Börsenregeln je Instrument** (interaktive Cloud-Sitzung, 2026-10-16, D-023): Preis-/Mengenschritt,
  Mindestmenge, Mindestwert, Börsen-Hebel und Gebührenstufen nach Volumen. SORA rechnet auf dem Mengenschritt und nie über
  dem Börsen-Hebel; der Risikomanager lehnt jede Order ab, die eine Börse ablehnen würde, und **rät nie** (fehlen Regeln für
  ein Instrument → abgelehnt); Strategie-Stops/-Ziele auf dem Preisschritt zu Ungunsten. Kritischer Fund im Prüfwerkzeug
  behoben (Gegenprobe zählte ihre eigene Textstellen-Prüfung als Entdeckung). Preflight STABIL 1553/1553; Python 3.9: 1325
  bestanden; Gegenprobe K24 + BR01–BR09 10/10.
* **AP-18/19 Forschungsrahmen und Bewertung** (interaktive Cloud-Sitzung, 2026-10-16, D-022): getrennte Zeiträume,
  Walk-forward, Versuchszählung mit steigender Sharpe-Schwelle, Kosten-Stress, Testdaten nur einmal, Strategie-Register mit
  Freigabestatus; AKI-Bewertung nach allen Kosten, je Marktphase, Stabilität, Nutzen von KIBO/YUZU.
  `python -m trading.research` verwirft auf Zufallsdaten alle Strategien (erwartet). Preflight STABIL 1529/1529;
  Python 3.9: 1301 bestanden; Gegenprobe RS01–RS09 9/9.
* **AP-21 Laufzeit-Engine** (interaktive Cloud-Sitzung, 2026-10-16, D-021): ereignisgesteuert, alle sechs Agenten,
  KI nur ereignisbasiert mit Kostenmessung je Ereignis, Neustart mit Aufwärmen, Not-Aus nach wiederholten Fehlern.
  **Standardmäßig aus** – Start nur mit `enabled`, PAPER und `TRADING_ENGINE_APPROVAL="PAPER-HANDEL FREIGEGEBEN"`.
  Preflight STABIL 1502/1502; Python 3.9: 1274 bestanden; Gegenprobe RT01–RT08 8/8.
* **AP-23/24 Dashboard** (interaktive Cloud-Sitzung, 2026-10-16, D-020): `python3.12 -m trading.dashboard` →
  http://127.0.0.1:8000. FastAPI nur lesend mit WebSocket, Modi BACKTEST/DEMO, LIVE gesperrt; Oberfläche mit Kapital,
  Kurve, Drawdown, Kennzahlen, Positionen, Trades, Agenten, Blockaden, Strategievergleich, Kosten und Netto nach allen
  Kosten. In Chromium geprüft (`docs/img/dashboard.png`). Neue Abhängigkeiten mit Prüfsummen, Lückenprüfung sauber.
  Preflight STABIL 1477/1477; Python 3.9: 1251 bestanden; Gegenprobe DA01–DA06 6/6.
* **AP-17 Marktphasen und regelbasierte KIBO/YUZU** (interaktive Cloud-Sitzung, 2026-10-16, D-019): Trend/Seitwärts und
  Volatilitätsrang; KIBO prüft Technik (Stop im Rauschen, gegen klaren Trend, Momentum, RSI, Volumen), YUZU das Umfeld
  (Volatilität, Funding, Spread/Volumen, Stimmung); KI nur als Zweitmeinung über den Kosten-Orchestrator. Alle sechs
  Agenten laufen jetzt im Backtest. Preflight STABIL 1451/1451; Python 3.9: 1241 bestanden; Gegenprobe KY01–KY10 10/10.
* **AP-16 Strategie-Bibliothek** (interaktive Cloud-Sitzung, 2026-10-16, D-018): Indikatoren (Decimal), sechs Strategien
  (Trendfolge, Momentum, Mean Reversion, Breakout, Volatilität, Scalping) auf gemeinsamem Rahmen mit ATR-Stops, Vergleich
  mit Nicht-Handeln und Kaufen-und-Halten (nach allen Handelskosten). Alle laufen durch die volle Pipeline; kein Blick in
  die Zukunft (Schnitt-Vergleich), bitgleich reproduzierbar. Preflight STABIL 1417/1417, 0 übersprungen; Python 3.9: 1207
  bestanden. Gegenprobe ST01–ST09 9/9. Nicht optimiert, nicht als profitabel belegt (AP-18/20).
* **AP-31/32/33 KI-Kostenoptimierung und Nettogewinn nach allen Kosten** (interaktive Cloud-Sitzung, Eigner-Vorgabe,
  2026-10-16, D-016): Kosten-Orchestrator (Regeln zuerst, Cache, ereignisbasierte Aktivierung, Modellstufen mit Wertschranke
  je Analyse), harte KI-Budgets je Aufruf/Tag/Monat (Standard 0; im Betrieb zusätzlich Datenbank-Gurt, Migration 006, Tag
  nach Datenbank-Uhr, sicher bei jeder Isolationsstufe und über Threads/Prozesse), AKI mit KI-Kosten je
  Agent/Modell/Analyse/Signal/Tag/Monat, Kosten je Trade (direkt und voll umgelegt), Betriebskosten und **Nettoergebnis
  nach allen Kosten**; Wirtschaftlichkeitsvergleich (gepaarter Bootstrap). Zwei unabhängige Prüfungen (25 + 6 bestätigte
  Befunde, alle behoben). Preflight STABIL 1363/1363, 0 übersprungen; Gegenprobe der Kosten-Einträge 69/69. Kein echter
  KI-Anbieter (Budget 0, B-004). AP-33-Nachweis mit echten Daten folgt in AP-20.
* **Meilenstein `stable/004-safety-stage2`** (Cloud-Routine 2026-10-16, 12:41–15:29 UTC): erste volle Gegenprobe fand eine
  Testlücke (K14: Tagesverlust-Grenzfall „genau 3 %“ seit AP-07 ungetestet) → Grenzfall-Tests für Tagesverlust und Drawdown
  ergänzt; zweite volle Gegenprobe 278/278, 886/886 Tests, STABIL. `develop` → `main` hochgeladen; Tag lokal gesetzt, Upload
  verweigert (403) → B-007.

* AP-12 Pipeline-Reihenfolge nach Vorgabe des Eigners (Cloud-Routine 2026-10-16 12:12–12:45 UTC, D-017): MOMO → RIN →
  SORA-Vorprüfung → KIBO → YUZU → SORA endgültig unter der Sperre → Executor → AKI. Was RIN oder SORA schon verwerfen, erreicht
  KIBO/YUZU (später ggf. teure KI) nicht mehr; die Größe der Order entsteht weiterhin unter der Sperre (Audit M8 bleibt erfüllt).
  Preflight STABIL 886/886, 0 übersprungen; Python 3.9: 703 bestanden. Gegenprobe der Pipeline-Einträge 14/14 (dabei eine
  Testlücke bei Q01 gefunden und mit einem deterministischen Test geschlossen). Hinweis B-006 (Geheimnis-Scan-Fehlalarme auf
  dem AP-31-Zweig).

* AP-11 Wiederverbindung zur Datenbank (Cloud-Routine 2026-10-16 06:13–06:40 UTC, Audit H7, D-015): Nach einem
  Verbindungsabbruch verbinden sich Executor, Protokoll und Not-Aus selbst neu (höchstens 4 Versuche mit 0,5/1/2 s Wartezeit,
  dann Fehler). Aktionen werden nur wiederholt, wenn die Verbindung vor der Entscheidung abriss; bei Abbruch während des COMMIT
  kommt ein Fehler, dieselbe Order-ID kann gefahrlos erneut gesendet werden. Zeitlimit für hängende Transaktionen gab es schon
  (AP-07). Preflight STABIL 877/877, 0 übersprungen; Python 3.9: 694 bestanden. Gegenprobe G66–G73 + angepasste G08/G35/G47/G62
  12/12. Volle Gegenprobe steht vor `stable/004` aus. Alarm bei Verbindungsproblemen fehlt noch (AP-25).
* AP-10 Rechte der App-Rolle (Cloud-Routine 2026-10-16 05:08–05:40 UTC, Audit M2 Rest, D-014): Konten legt nur noch ein
  Administrator an (Migration 005, `python -m trading.persistence.accounts create KONTO STARTKAPITAL`, Protokoll
  `ACCOUNT_CREATED`); der Executor startet ohne Konto nicht und hat keinen Parameter „Startkapital“ mehr. Die Datenbank prüft jede
  Kontostand-Änderung der Anwendung (Version, Tag, Positionszähler, Tagesstart-Equity, Höchststand). Preflight STABIL 861/861,
  0 übersprungen; Python 3.9: 694 bestanden. Gegenprobe G54–G65 12/12. Volle Gegenprobe steht vor `stable/004` aus.
  Hinweis für den Mac: `scripts/restart_check.py seed` braucht für ein neues Konto jetzt zusätzlich `ADMIN_DATABASE_URL`.
* AP-09 Größe unter Konkurrenz innerhalb der Sperre (Cloud-Routine 2026-10-16 04:08–04:35 UTC, Audit M8, D-013):
  SORA rechnet jetzt unter der Sperre des Executors (`submit_sized`), bei PostgreSQL in der Transaktion mit gesperrter
  Konto-Zeile. Ist das Restbudget durch einen anderen Prozess kleiner geworden, wird die Position verkleinert statt abgelehnt.
  Exakter Mehrprozess-Test: 8 Prozesse, Ziel 0,45 % → genau 5 Positionen (4 voll, 1 verkleinert). Preflight STABIL 821/821,
  0 übersprungen; Python 3.9: 691 bestanden. Gegenprobe der AP-09-Einträge 7/7. Volle Gegenprobe steht vor `stable/004` aus.
* AP-08 Wiederholte Order liefert das ursprüngliche Ergebnis (Cloud-Routine 2026-10-16 03:08–03:40 UTC, Audit M7, D-012):
  Gleiche Order-ID mit gleichem Inhalt bekommt Status, Gründe und Position der ersten Bearbeitung (`replayed=True`,
  Protokoll `ORDER_REPLAYED`) – auch aus anderen Prozessen und nach einem Neustart; anderer Inhalt bleibt „Duplikat“.
  Migration 004 (Fingerabdruck + Position in `orders`). Preflight STABIL 806/806, 0 übersprungen; Python 3.9: 683 bestanden.
  Gegenprobe der AP-08-Einträge 15/15. Volle Gegenprobe steht vor `stable/004` weiter aus.
* AP-07 Globaler Not-Aus (interaktive Sitzung 2026-10-15; abgeschlossen von der Cloud-Routine 2026-10-16 02:08–03:10 UTC,
  nachdem deren Sperre verwaist war): dauerhaft in der Datenbank (Migration 003), automatisch bei kaputten Daten,
  Protokollfehlern und Notbremsen, Freigabe nur durch einen Administrator mit Namen und `HANDEL FREIGEBEN` (D-011).
  Preflight STABIL 798/798, 0 übersprungen (mit PostgreSQL 16). Gegenprobe der AP-06/07-Einträge 46/46 entdeckt; vier veraltete
  Einträge angepasst, Gegenprobe prüft Textstellen jetzt vorab. Volle Gegenprobe (236) steht vor `stable/004` noch aus.
* AP-03 (Cloud-Routine, erster Lauf 2026-10-15 19:08–20:00 UTC): Cloud-Umgebung geprüft. Python 3.12.3 (Achtung: `python3` ist
  dort 3.11) und 3.9.25 über `uv`; Wegwerf-PostgreSQL 16 über `scripts/cloud_test_db.sh` läuft als root ohne Rückfrage;
  Preflight STABIL inkl. Datenbank (633/633 auf Stand `ae639e5`); Gegenprobe 193/193 (ca. 41 min, rund viermal langsamer als
  auf dem Mac); Push auf Arbeitszweige und `develop` funktioniert; GitHub-Actions-Ergebnisse sind lesbar. Sperre wirkte: Die
  parallele interaktive Sitzung hat AP-03 nicht angefasst. Die rote Python-3.9-Prüfung (Importfehler in 5 Datenbank-Testmodulen)
  hatte die Routine ebenfalls gefunden und behoben; da AP-05 dieselbe Korrektur schon auf `develop` gebracht hatte, wurde nur die
  Doku übernommen (Routine-Stand gesichert im lokalen Zweig, nicht hochgeladen). Noch nicht ausprobiert: Tags aus der Cloud setzen.
* AP-06 (Cloud-Sitzung, 2026-10-15): zentraler PAPER-Schutz beim `import trading` für jeden Prozess, breitere Schlüssel-Erkennung
  (Audit M3 behoben). Gegenprobe E04–E10 entdeckt. Gegenprobe-Skript repariert (Kopie vollständig, Grundprobe). 716/716 Tests.
* AP-05 (Cloud-Sitzung, 2026-10-15): Abhängigkeiten fest mit Prüfsummen in `requirements/*.lock`, pytest 9.0.3 (Lücke
  CVE-2025-71176), Lückenprüfung `scripts/dependency_audit.py` + eigener GitHub-Job, Python-3.9-Job repariert (D-010).
  Preflight STABIL in der Cloud: 646/646 Tests, 0 übersprungen (Python 3.12.3, PostgreSQL 16 über `scripts/cloud_test_db.sh`).
  Nebenbei belegt: Die Cloud kann PostgreSQL bereitstellen, und Push auf `develop` funktioniert aus einer Cloud-Sitzung.
  Docker-Images lassen sich in der Cloud derzeit nicht bauen (Docker Hub: 429).
* AP-01 (lokal): Branch-Modell, Tags `stable/001` und `stable/002`, Geheimnis-Scanner (`scripts/secret_scan.py`) mit Tests,
  Prüfskript für Checkpoints (`scripts/preflight.py`), GitHub-Actions-Datei, feste Werkzeug-Versionen, Wiederherstellungsanleitung,
  `CLAUDE.md`, Master-Plan, Entscheidungen, Blocker. Zwei Datenbank-Abzüge vor der ersten Veröffentlichung aus der Geschichte
  entfernt (D-002).

## Nächste Schritte

1. AP-02: GitHub Actions ist auf `develop` grün (seit Lauf 8, 19:26 UTC); `main` ist noch rot (Stand `stable/003`, Python-3.9-
   Importfehler) und wird mit dem nächsten stabilen Stand grün → dann AP-02 auf ✅. Hinweis aus Actions: `actions/checkout@v4`
   und `actions/setup-python@v5` nutzen das veraltete Node 20 – bei Gelegenheit anheben.
2. Phase C ab AP-07 (Kill Switch). AP-05 und AP-06 sind erledigt.

## Arbeitsumgebung

**Cloud (maßgeblich):** siehe `CLAUDE.md`, Abschnitt „Arbeitsort: Cloud“.

**Mac (nur Sicherung, seit 2026-10-15 nicht mehr bearbeitet):**

* Python 3.12 in einer Umgebung außerhalb des Projekts; Werkzeuge laut `requirements/dev.lock` (vor AP-05: `requirements-dev.txt`).
* PostgreSQL 16 in Docker über Colima; Passwort-Datei außerhalb des Projektordners.
* Echte lokale Datenbank `trading`: enthält nur das Prüfkonto `restart-check` (2 Alt-Positionen). Nie löschen.
