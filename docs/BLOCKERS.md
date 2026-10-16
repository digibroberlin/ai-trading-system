# Blocker – was ohne den Eigner nicht weitergeht

Offene Punkte stehen oben. Erledigte werden mit Datum nach unten verschoben.

## Offen

* **B-006 Geheimnis-Scan meldet Fehlalarme auf dem Zweig `ap/31-kostenoptimierung` (Hinweis, kein Eingriff des Eigners nötig).**
  WAS: `python scripts/secret_scan.py --history` prüft alle Zweige. Zwei WIP-Commits der interaktiven AP-31-Sitzung (`965e701`,
  `c4c36d2`) enthalten Variablen wie `input_tokens` – das sind Token-*Anzahlen* der KI-Kostenmessung, keine Zugangsdaten. Der
  Scanner auf `develop` kennt die enge Ausnahme dafür noch nicht (sie liegt auf dem AP-31-Zweig, Commit `f4a98b1`).
  WO: jede Sitzung, die alle Zweige holt (`git fetch --all`). WIE bis dahin: Die Cloud-Routine hat am 2026-10-16 geprüft, dass
  die Funde nur auf dem AP-31-Zweig liegen, und für ihren eigenen Stand ohne den lokalen Verweis auf diesen Zweig gescannt
  (auf GitHub bleibt er unverändert). WAS DANACH: Mit dem Zusammenführen von AP-31 nach `develop` ist der Punkt erledigt.

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
