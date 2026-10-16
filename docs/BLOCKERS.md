# Blocker – was ohne den Eigner nicht weitergeht

Offene Punkte stehen oben. Erledigte werden mit Datum nach unten verschoben.

## Offen

* **B-007 Tag `stable/004-safety-stage2` muss einmal von Hand auf GitHub angelegt werden (Eigner, ca. 2 Minuten).**
  WAS: Der stabile Stand ist geprüft und auf `main` hochgeladen (Commit `ad6cc9f`), aber die Cloud darf keine Tags hochladen
  (GitHub antwortet mit „403 verboten“; Zweige gehen). WO: auf github.com im Repository `digibroberlin/ai-trading-system`.
  WIE (am einfachsten im Browser): rechts „Releases“ → „Draft a new release“ → „Choose a tag“: `stable/004-safety-stage2`
  eintippen → „Create new tag on publish“ → Target: `main` → Titel `stable/004-safety-stage2` → „Publish release“.
  Alternativ auf dem Mac im Projektordner: `git fetch origin && git tag -a stable/004-safety-stage2 ad6cc9f -m "stable/004:
  886/886 Tests, Gegenprobe 278/278"` und `git push origin stable/004-safety-stage2`.
  WAS DANACH: Die Routine prüft den Tag beim nächsten Lauf. Dauerhaft besser: der Cloud-Umgebung erlauben, Tags `stable/*`
  zu pushen (Einstellung der Claude-GitHub-App bzw. der Umgebung); bis dahin wird jeder künftige stabile Stand so gemeldet.

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
* **B-004 KI-Anbieter.** Echte KI-Aufrufe (z. B. Claude-API) kosten Geld → Freigabe mit Budget nötig (AP-22). Seit D-016
  ist alles vorbereitet: Ohne Freigabe ist das Budget 0. Zu entscheiden sind dann nur drei Zahlen (USD je Aufruf, je Tag,
  je Monat) und der Anbieter-Zugang; vorher muss der Nutzen empirisch geprüft werden können (AP-33).
* **B-005 Server.** Hetzner-VPS erst, wenn Betrieb ansteht (AP-26). Anleitung mit Kosten folgt dann.

## Erledigt

* **B-001 (2026-10-15):** GitHub-Konto `digibroberlin`, privates Repository, Deploy Key nur für dieses Repository,
  Claude-GitHub-App freigegeben. `main`, `develop` und `stable/001`–`stable/003` hochgeladen.
* **B-002 (2026-10-15):** Cloud-Routine geprüft (AP-03, erster Lauf): PostgreSQL lässt sich in der Cloud über
  `scripts/cloud_test_db.sh` einrichten, alle Datenbank-Tests laufen, Push auf Arbeitszweige und `develop` funktioniert.
