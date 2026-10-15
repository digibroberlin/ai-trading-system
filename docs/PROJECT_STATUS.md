# Projektstatus

Wird bei jedem Arbeitspaket aktualisiert. So geschrieben, dass eine neue Sitzung (oder eine Cloud-Routine) ohne
Informationsverlust weitermachen kann.

## Sperre

gesperrt von Cloud-Sitzung (interaktiv, Eigner-Auftrag) seit 2026-10-15 19:37 UTC für AP-07

(Format: `gesperrt von <Sitzung/Routine> seit <Datum Uhrzeit UTC> für AP-xx` – Sperren älter als 3 Stunden gelten als verwaist.)

## Aktueller Stand (2026-10-15)

* **Letzter stabiler Stand:** `stable/003-autonomy-baseline` (633 Tests, Gegenprobe 193/193, Preflight STABIL).
* **Zweige:** `main` (stabil), `develop` (Integration).
* **GitHub:** Repository `digibroberlin/ai-trading-system` (privat). `main`, `develop`, `stable/001`–`003` hochgeladen (2026-10-15).
* **Arbeitsort:** künftig die Cloud (D-008); der Mac-Ordner bleibt danach nur Sicherung.
* **Handel:** keiner. Es läuft kein Bot, kein Paper- oder Demo-Betrieb.

## In Arbeit (Zwischenstand)

* **AP-07 Not-Aus** auf Zweig `ap/07-not-aus` (WIP-Commit 37d87a0, hochgeladen): Code, Migration 003, Tests fertig und grün.
  Nächster Schritt: Ergebnis der Gegenprobe `python scripts/mutation_check.py --only N01 … N12 G31 … G40` prüfen,
  Testbericht ergänzen, Preflight, nach `develop` zusammenführen.

## Zuletzt erledigt

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
