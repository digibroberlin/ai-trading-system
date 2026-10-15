# Projektstatus

Wird bei jedem Arbeitspaket aktualisiert. So geschrieben, dass eine neue Sitzung (oder eine Cloud-Routine) ohne
Informationsverlust weitermachen kann.

## Sperre

gesperrt von Cloud-Routine (Lauf 2026-10-15 19:08 UTC) seit 2026-10-15 19:08 UTC für AP-03

(Format: `gesperrt von <Sitzung/Routine> seit <Datum Uhrzeit UTC> für AP-xx` – Sperren älter als 3 Stunden gelten als verwaist.)

## Aktueller Stand (2026-10-15)

* **Letzter stabiler Stand:** `stable/003-autonomy-baseline` (633 Tests, Gegenprobe 193/193, Preflight STABIL).
* **Zweige:** `main` (stabil), `develop` (Integration).
* **GitHub:** Repository `digibroberlin/ai-trading-system` (privat). `main`, `develop`, `stable/001`–`003` hochgeladen (2026-10-15).
* **Arbeitsort:** künftig die Cloud (D-008); der Mac-Ordner bleibt danach nur Sicherung.
* **Handel:** keiner. Es läuft kein Bot, kein Paper- oder Demo-Betrieb.

## Zuletzt erledigt

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

1. **AP-03 abschließen (erste Cloud-Sitzung/-Routine):** Python-Umgebung und `scripts/cloud_test_db.sh` in der Cloud ausprobieren
   (Skript ist auf dem Mac nicht ausführbar gewesen → **ungetestet**), `scripts/preflight.py` laufen lassen, prüfen ob Push auf
   `develop` und das Setzen von Tags aus der Cloud funktionieren, Ergebnis des ersten GitHub-Actions-Laufs prüfen (falls von der
   Cloud aus einsehbar). Alles in `docs/BLOCKERS.md`/`docs/TEST_RESULTS.md` festhalten.
2. Phase C (AP-06 …). AP-05 ist erledigt (D-010).

## Arbeitsumgebung

**Cloud (maßgeblich):** siehe `CLAUDE.md`, Abschnitt „Arbeitsort: Cloud“.

**Mac (nur Sicherung, seit 2026-10-15 nicht mehr bearbeitet):**

* Python 3.12 in einer Umgebung außerhalb des Projekts; Werkzeuge laut `requirements/dev.lock` (vor AP-05: `requirements-dev.txt`).
* PostgreSQL 16 in Docker über Colima; Passwort-Datei außerhalb des Projektordners.
* Echte lokale Datenbank `trading`: enthält nur das Prüfkonto `restart-check` (2 Alt-Positionen). Nie löschen.
