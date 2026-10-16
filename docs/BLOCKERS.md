# Blocker – was ohne den Eigner nicht weitergeht

Offene Punkte stehen oben. Erledigte werden mit Datum nach unten verschoben.

## Offen

## Später (noch nicht nötig)

* **B-003 Order-Anbindung für Börsen-Demokonten.** AGENTS.md §2 Regel 1 verbietet heute jede Order-Funktion an eine Börse.
  Für die spätere „Demo-Einrichtung“ braucht es eine Order-Anbindung an ein Demokonto (z. B. Bitget Demo-Trading). Entscheidung
  des Eigners nötig, bevor dieses Arbeitspaket (AP-28) beginnt. Bis dahin: vorbereitet nur gegen Attrappen getestet.
* **B-004 KI-Anbieter.** Echte KI-Aufrufe (z. B. Claude-API) kosten Geld → Freigabe mit Budget nötig (AP-20).
* **B-005 Server.** Hetzner-VPS erst, wenn Betrieb ansteht (AP-26). Anleitung mit Kosten folgt dann.

## Erledigt

* **B-001 (2026-10-15):** GitHub-Konto `digibroberlin`, privates Repository, Deploy Key nur für dieses Repository,
  Claude-GitHub-App freigegeben. `main`, `develop` und `stable/001`–`stable/003` hochgeladen.
* **B-002 (2026-10-15):** Cloud-Routine geprüft (AP-03, erster Lauf): PostgreSQL lässt sich in der Cloud über
  `scripts/cloud_test_db.sh` einrichten, alle Datenbank-Tests laufen, Push auf Arbeitszweige und `develop` funktioniert.
