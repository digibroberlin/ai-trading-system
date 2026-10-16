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

* **B-008 Echter Testabruf historischer Marktdaten braucht Netzfreigabe (Eigner, ca. 2 Minuten; kostenlos).**
  WAS: Die Marktdaten-Adapter (AP-14) sind fertig und mit nachgebildeten Antworten getestet. Der geplante **eine** echte Abruf
  öffentlicher Kerzen (nur lesend, ohne Schlüssel) scheitert in der Cloud: Die Netzwerk-Regel der Umgebung lässt
  `api.exchange.coinbase.com` und `api.bitget.com` nicht zu. Dies ist zugleich die in AGENTS.md §9.3 verlangte Rückfrage.
  Für die Marktüberwachung (AP-15) zusätzlich `ws.bitget.com` und `ws-feed.exchange.coinbase.com` (WebSocket, nur lesend).
  WO/WIE: in der Claude-Code-Cloud die Umgebung öffnen (Menü der Umgebung in der Titelleiste der Sitzung → „Edit“) →
  „Network access“: die vier Hosts unter „Allowed domains“ eintragen (Haken bei „Allow package managers“ lassen).
  Anleitung: https://code.claude.com/docs/en/cloud-environments#network-access. Alternativ auf dem Mac:
  `PYTHONPATH=src python3.12 -m trading.marketdata --exchange coinbase --symbol BTC-USD --start 2026-09-01 --end 2026-09-08`.
  WAS DANACH: Die nächste Sitzung lädt eine Woche BTC-Kerzen (Coinbase Spot, Bitget Spot/Futures), prüft sie und hält das
  Ergebnis im Testbericht fest, beobachtet einige Minuten den Live-Strom (`python -m trading.marketdata.watch`, ohne
  Handel) und führt AP-20 aus (`python -m trading.research.study ...`, D-032 – das Werkzeug ist fertig).

* **B-009 Geprüfte Stände nach `main` übernehmen (Eigner, ca. 1 Minute) – seit der Ergänzung vom 2026-10-16.**
  WAS: Claude verändert `main` nicht mehr selbst. Ist ein Stand STABLE (Preflight STABIL + volle Gegenprobe), steht der Commit
  in PROJECT_STATUS. WO/WIE: auf github.com „Pull requests“ → „New“ → base `main`, compare `develop` (bzw. den genannten
  Commit) → „Merge“. Alternativ Claude ausdrücklich erlauben, geprüfte Stände selbst zusammenzuführen (ohne Überschreiben).
  WAS DANACH: GitHub Actions prüft `main` erneut; `main` ist wieder grün (AP-02).
  **Dazu bitte:** Der Auftrag der stündlichen Routine enthält in Schritt 6 noch „develop nach main“. Claude darf ihn nicht
  ändern (die Routine wurde im Browser angelegt). WO: https://claude.ai/code/routines/trig_01HtmuP8ds89wqCdpFJya5Z9 →
  Bearbeiten. WIE: in Schritt 6 „develop nach main, annotierter Tag …“ ersetzen durch „den develop-Stand als STABLE
  kennzeichnen (Tag + Eintrag in PROJECT_STATUS); main nie selbst verändern“. Bis dahin schützen CLAUDE.md (gilt für die
  Routine verbindlich) und der Abschlussvermerk in PROJECT_STATUS, nach dem die Routine nichts mehr ändert.

## Später (noch nicht nötig)

* **B-003 Order-Anbindung für Börsen-Demokonten.** AGENTS.md §2 Regel 1 verbietet heute jede Order-Funktion an eine Börse.
  Für die spätere „Demo-Einrichtung“ braucht es eine Order-Anbindung an ein Demokonto (z. B. Bitget Demo-Trading). Entscheidung
  des Eigners nötig, bevor dieses Arbeitspaket (AP-29) beginnt. Bis dahin: vorbereitet nur gegen Attrappen getestet.
* **B-004 KI-Anbieter.** Echte KI-Aufrufe (z. B. Claude-API) kosten Geld → Freigabe mit Budget nötig (AP-22). Seit D-016
  ist alles vorbereitet: Ohne Freigabe ist das Budget 0. Zu entscheiden sind dann nur drei Zahlen (USD je Aufruf, je Tag,
  je Monat) und der Anbieter-Zugang; vorher muss der Nutzen empirisch geprüft werden können (AP-33).
  Seit D-024 (AP-22) ist auch der Claude-Anschluss fertig (nur mit Attrappe getestet). **WAS** bei Freigabe zu tun ist:
  (1) Budget-Zahlen nennen, (2) in der Anthropic-Konsole (console.anthropic.com) einen API-Schlüssel anlegen und ihn
  **nur** auf dem Server als `ANTHROPIC_API_KEY` hinterlegen (nie in den Chat oder ins Repository), (3) Freigabetext
  `TRADING_AI_APPROVAL="KI-AUFRUFE FREIGEGEBEN"` setzen. Danach nimmt die Entwicklung das Paket `anthropic` mit Prüfsummen
  auf und prüft die Preistabelle gegen die aktuelle Preisliste.
* **B-005 Server.** Erst, wenn der Paper-Betrieb auf einem Server laufen soll. Seit AP-28 (D-030) ist alles
  vorbereitet: Schritt-für-Schritt-Anleitung mit Kosten (ca. 4–6 €/Monat) in `docs/DEPLOYMENT.md`. Zu entscheiden:
  Anbieter/Server, ob ein Alarm-Kanal (z. B. ntfy) und ob eine Sicherungskopie außerhalb des Servers gewünscht ist.

## Erledigt

* **B-006 (2026-10-16):** Fehlalarme des Geheimnis-Scans auf dem AP-31-Zweig (Token-*Anzahlen*): mit dem Zusammenführen
  von AP-31…33 nach `develop` erledigt – der Scanner nimmt jetzt nur feste Zähler-Namen aus.
* **B-001 (2026-10-15):** GitHub-Konto `digibroberlin`, privates Repository, Deploy Key nur für dieses Repository,
  Claude-GitHub-App freigegeben. `main`, `develop` und `stable/001`–`stable/003` hochgeladen.
* **B-002 (2026-10-15):** Cloud-Routine geprüft (AP-03, erster Lauf): PostgreSQL lässt sich in der Cloud über
  `scripts/cloud_test_db.sh` einrichten, alle Datenbank-Tests laufen, Push auf Arbeitszweige und `develop` funktioniert.
