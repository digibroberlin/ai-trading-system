# Blocker – was ohne den Eigner nicht weitergeht

Offene Punkte stehen oben. Erledigte werden mit Datum nach unten verschoben.

## Offen

### B-001 Zugriffsschlüssel auf GitHub hinterlegen (seit 2026-10-15)
* Konto `digibroberlin` und Repository angelegt (laut Eigner). Schlüssel `~/.ssh/ai_trading_deploy` auf dem Mac erzeugt,
  Verbindungsname `github-ai-trading` in `~/.ssh/config` (alte Datei gesichert als `~/.ssh/config.vor-ai-trading-2026-10-15`).
* **Was der Eigner tut:** öffentlichen Schlüssel als Deploy Key mit Schreibrecht im Repository hinterlegen; Claude-GitHub-App in
  claude.ai Zugriff auf dieses Repository geben.
* **Danach:** Hochladen aller Zweige und Tags, Prüfung GitHub Actions, Einrichtung der Routine (B-002 ist zugestimmt).

### B-002 Cloud-Routine einrichten – Zustimmung liegt vor (2026-10-15)
* Wartet nur auf B-001 (Repository muss auf GitHub liegen und für Claude in der Cloud erreichbar sein).
* Noch zu prüfen: ob die Cloud-Umgebung PostgreSQL bereitstellen kann (sonst Datenbank-Tests über GitHub Actions) und
  auf welche Zweige eine Cloud-Sitzung schreiben darf.

## Später (noch nicht nötig)

* **B-003 Order-Anbindung für Börsen-Demokonten.** AGENTS.md §2 Regel 1 verbietet heute jede Order-Funktion an eine Börse.
  Für die spätere „Demo-Einrichtung“ braucht es eine Order-Anbindung an ein Demokonto (z. B. Bitget Demo-Trading). Entscheidung
  des Eigners nötig, bevor dieses Arbeitspaket (AP-28) beginnt. Bis dahin: vorbereitet nur gegen Attrappen getestet.
* **B-004 KI-Anbieter.** Echte KI-Aufrufe (z. B. Claude-API) kosten Geld → Freigabe mit Budget nötig (AP-20).
* **B-005 Server.** Hetzner-VPS erst, wenn Betrieb ansteht (AP-26). Anleitung mit Kosten folgt dann.

## Erledigt

(noch keine)
