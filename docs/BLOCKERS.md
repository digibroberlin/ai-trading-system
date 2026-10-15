# Blocker – was ohne den Eigner nicht weitergeht

Offene Punkte stehen oben. Erledigte werden mit Datum nach unten verschoben.

## Offen

### B-001 GitHub-Konto, privates Repository und Verbindung (seit 2026-10-15)
* **Warum nötig:** Sicherung außerhalb dieses Rechners, automatische Tests (GitHub Actions), Voraussetzung für Cloud-Routinen.
* **Was der Eigner tut:** Konto anlegen, leeres privates Repository `ai-trading-system` anlegen, Zugriffsschlüssel hinterlegen
  (Anleitung im Chat vom 2026-10-15 und unten). Danach den Nutzernamen mitteilen.
* **Was Claude danach tut:** Verbindung testen, alle Zweige und `stable/*`-Tags hochladen, GitHub Actions prüfen.
* **Bis dahin:** Arbeit lokal auf Zweigen, Tags lokal, Sicherung zusätzlich als Git-Bündel in `backups/`.

### B-002 Einmalige Zustimmung zur stündlichen Cloud-Routine (seit 2026-10-15)
* **Voraussetzung:** B-001 erledigt und GitHub in claude.ai verbunden (Claude-GitHub-App nur für dieses Repository).
* **Was die Routine tut:** stündlich den GitHub-Stand laden, nächstes Arbeitspaket bearbeiten, Tests, sichern – mit denselben Regeln.
* **Kosten:** keine zusätzlichen; sie verbraucht das normale Nutzungskontingent des Abos (konkurriert mit eigenen Sitzungen).
* **Grenze:** Im Cloud-Container steht vermutlich kein Docker/PostgreSQL zur Verfügung → Datenbank-Tests dort evtl. nur über
  GitHub Actions. Wird nach Einrichtung geprüft und dokumentiert.

## Später (noch nicht nötig)

* **B-003 Order-Anbindung für Börsen-Demokonten.** AGENTS.md §2 Regel 1 verbietet heute jede Order-Funktion an eine Börse.
  Für die spätere „Demo-Einrichtung“ braucht es eine Order-Anbindung an ein Demokonto (z. B. Bitget Demo-Trading). Entscheidung
  des Eigners nötig, bevor dieses Arbeitspaket (AP-28) beginnt. Bis dahin: vorbereitet nur gegen Attrappen getestet.
* **B-004 KI-Anbieter.** Echte KI-Aufrufe (z. B. Claude-API) kosten Geld → Freigabe mit Budget nötig (AP-20).
* **B-005 Server.** Hetzner-VPS erst, wenn Betrieb ansteht (AP-26). Anleitung mit Kosten folgt dann.

## Erledigt

(noch keine)
