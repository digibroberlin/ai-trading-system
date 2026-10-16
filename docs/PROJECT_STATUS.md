# Projektstatus

Wird bei jedem Arbeitspaket aktualisiert. So geschrieben, dass eine neue Sitzung (oder eine Cloud-Routine) ohne
Informationsverlust weitermachen kann.

## Sperre

frei

(Format: `gesperrt von <Sitzung/Routine> seit <Datum Uhrzeit UTC> für AP-xx` – Sperren älter als 3 Stunden gelten als verwaist.
Mehrere Sperren sind erlaubt: eine Zeile je Arbeitspaket; jede Sitzung ändert nur ihre eigene Zeile.)

## Aktueller Stand (2026-10-16)

* **Letzter stabiler Stand:** `stable/004-safety-stage2` (2026-10-16, 886 Tests, Gegenprobe 278/278 mit Datenbank, Preflight STABIL).
  `main` = `ad6cc9f` ist hochgeladen; der **Tag selbst fehlt auf GitHub** (Cloud darf keine Tags pushen, 403) → B-007.
* **Zweige:** `main` (stabil), `develop` (Integration).
* **GitHub:** Repository `digibroberlin/ai-trading-system` (privat). `main`, `develop`, `stable/001`–`003` hochgeladen (2026-10-15).
* **Arbeitsort:** künftig die Cloud (D-008); der Mac-Ordner bleibt danach nur Sicherung.
* **Handel:** keiner. Es läuft kein Bot, kein Paper- oder Demo-Betrieb.

## In Arbeit (Zwischenstand)

* Nächstes Paket in Plan-Reihenfolge: **AP-13** (Börsenregeln je Instrument). Die interaktive Sitzung arbeitet auf Wunsch des
  Eigners (Fokus: Strategien, Agenten-Integration, Dashboard) an **AP-16** (Zweig `ap/16-strategien`), danach AP-17,
  AP-23/24, AP-21 – jeweils mit eigener Sperr-Zeile. Bitte nicht am selben Paket arbeiten.

## Zuletzt erledigt

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
