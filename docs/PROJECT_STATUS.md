# Projektstatus

Wird bei jedem Arbeitspaket aktualisiert. So geschrieben, dass eine neue Sitzung (oder eine Cloud-Routine) ohne
Informationsverlust weitermachen kann.

## Sperre

Keine. (Format: `gesperrt von <Sitzung/Routine> seit <Datum Uhrzeit UTC> für AP-xx` – Sperren älter als 3 Stunden gelten als verwaist.)

## Aktueller Stand (2026-10-15)

* **Letzter stabiler Stand:** `stable/003-autonomy-baseline` (633 Tests, Gegenprobe 193/193, Preflight STABIL).
* **Zweige:** `main` (stabil), `develop` (Integration).
* **GitHub:** noch nicht verbunden (Blocker B-001). Alles liegt lokal; zusätzlich Git-Bündel in `backups/`.
* **Handel:** keiner. Es läuft kein Bot, kein Paper- oder Demo-Betrieb.

## Zuletzt erledigt

* AP-01 (lokal): Branch-Modell, Tags `stable/001` und `stable/002`, Geheimnis-Scanner (`scripts/secret_scan.py`) mit Tests,
  Prüfskript für Checkpoints (`scripts/preflight.py`), GitHub-Actions-Datei, feste Werkzeug-Versionen, Wiederherstellungsanleitung,
  `CLAUDE.md`, Master-Plan, Entscheidungen, Blocker. Zwei Datenbank-Abzüge vor der ersten Veröffentlichung aus der Geschichte
  entfernt (D-002).

## Nächste Schritte

1. Nach B-001: AP-02 (hochladen, Actions prüfen), dann B-002/AP-03 (Cloud-Routine).
2. Unabhängig davon: AP-04 (Framework-Untersuchung), AP-05 (Abhängigkeiten), dann Phase C (AP-06 …).

## Arbeitsumgebung (lokal, Mac)

* Python 3.12 in einer Umgebung außerhalb des Projekts; Werkzeuge laut `requirements-dev.txt`.
* PostgreSQL 16 in Docker über Colima; Passwort-Datei außerhalb des Projektordners.
* Echte lokale Datenbank `trading`: enthält nur das Prüfkonto `restart-check` (2 Alt-Positionen). Nie löschen.
