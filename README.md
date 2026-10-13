# AI Trading System (nur PAPER-Modus)

Technische Grundlage für einen autonomen KI-Krypto-Trading-Bot. **Dieses Projekt handelt nicht mit echtem Geld.**
Es gibt keinen Code, der eine echte Order senden könnte, und das System verweigert den Start, sobald Börsen-Schlüssel gesetzt sind.

> **Wichtig:** Dass der Bot Gewinne macht, ist **nicht bewiesen**. Das muss erst durch Backtests und mehrwöchiges Paper-Trading
> mit realistischen Kosten gezeigt werden. Die Tests in diesem Stand prüfen nur, ob die **Technik** korrekt arbeitet.

## Stand (Phase 3: PostgreSQL, Docker, Mehrprozess-Sicherheit)

| Baustein | Status |
|---|---|
| Projektstruktur, Python-Paket, Docker (Colima), PostgreSQL 16 | **läuft und ist getestet** (Python 3.12.14 auf dem Mac, 3.12.15 im Container) |
| Konto, Positionen, Orders und Protokoll in PostgreSQL, prozessübergreifend per Konto-Sperre | funktionsfähig, getestet (12 echte Prozesse gleichzeitig) |
| Datenbank-Trigger: Protokoll unveränderlich, 2-%-Limit beim Commit, nur Paper-Modus | funktionsfähig, getestet |
| Neustart: offene Paper-Positionen bleiben erhalten | getestet (Prozess, Container, VM, harter Absturz) |
| Sechs Agenten: Schnittstellen und Grundklassen | MOMO, KIBO, YUZU abstrakt · **RIN und SORA regelbasiert implementiert** · AKI implementiert |
| SORA: Positionsgröße, Hebel, 2-%-Gesamtrisiko, Liquidationsabstand | funktionsfähig, getestet |
| RIN: Signal- und Kursdaten-Validierung, Whitelist der Handelspaare | funktionsfähig, getestet |
| Gleichzeitige Anfragen (Sperren in Executor und Pipeline) | funktionsfähig, getestet |
| Paper Executor (Spot/Futures, Long/Short, SL/TP, Gebühren, Slippage, Funding) | funktionsfähig, getestet |
| Unabhängiger Risikomanager (2 % Gesamtrisiko u. a.) | funktionsfähig, getestet |
| Datenprüfung (veraltet/fehlerhaft → blockiert) | funktionsfähig, getestet |
| Unveränderliches Ereignisprotokoll (Hash-Kette) | funktionsfähig, getestet |
| Synthetische Marktdaten | funktionsfähig |
| Austauschbare KI-Modelle | nur Schnittstelle (kein echter Anbieter, keine Kosten) |
| Backtests, Strategien, Live-Marktdaten, Börsen-Adapter, Dashboard | **noch nicht gebaut** |

Testergebnis: **354 Tests bestanden** auf Python 3.12 mit PostgreSQL 16 – auf dem Mac **und** im Docker-Container. Ohne Datenbank laufen 295 Tests,
59 Datenbank-Tests werden dann sichtbar als „skipped“ gemeldet. Details, Gegenproben und **noch offene Punkte** in [docs/TESTBERICHT.md](docs/TESTBERICHT.md).

## Was SORA und RIN jetzt tun

**SORA (Risiko und Größe)** rechnet vom *tatsächlich erwarteten* Einstiegskurs (inkl. Spread und Slippage), nie vom Wunschkurs:

1. **Risikobudget** = der kleinere Wert aus *Ziel-Risiko je Trade (0,4 %)* und *2 % Gesamtrisiko minus Risiko aller bereits offenen Positionen*.
   Das gilt für **alle Strategien gemeinsam** – es gibt nur einen Topf.
2. **Menge** = Budget ÷ möglicher Verlust je Stück (Kursverlust bis zum Stop + Gebühren + Stop-Slippage + ein Funding-Puffer), **abgerundet**.
   Abrunden garantiert, dass der Höchstverlust nie über dem Budget liegt.
3. **Hebel (nur Futures):** der *kleinste* Hebel, bei dem die Margin reicht; nie über dem Limit, und nur wenn die Liquidation mindestens
   1,5× so weit entfernt ist wie der Stop. Reicht die Margin selbst dann nicht, wird die Position **verkleinert, nie vergrößert**.
   Spot: immer 1×, nur so viel, wie bar bezahlbar ist.
4. **Blockiert automatisch:** Spot-Short, falsche Stop-/Ziel-Seite, fehlende/ungültige Zahlen, Tagesverlust-/Drawdown-Notbremse,
   zu viele Positionen, doppelte Position, kein Restbudget, Liquidation zu nah, zu kleine Position.

**RIN (Validierung)** prüft und sammelt *alle* Verstöße:

* **Handelspaar** – korrektes Format *und* ausdrücklich erlaubt (Whitelist je Markt, sonst verboten)
* **Marktdaten** – vollständig, endlich, aktuell (≤ 15 s), Spread, Kurssprung, Kerzen konsistent, Futures nur mit Funding-Rate
* **Setup** – nicht älter als 60 s, Entry max. 0,5 % vom Markt entfernt, Stop/Ziel auf der richtigen Seite *auch gegenüber dem aktuellen Kurs*
* **Stop-Abstand** – nicht kleiner als das 3-Fache der Round-Trip-Kosten (sonst frisst der Spread den Stop auf), nicht größer als 10 %
* **Netto-Chance-Risiko ≥ 1,5** *nach* Gebühren, Slippage, Spread und Funding

Beides sind feste Regeln, **keine KI**. Der unabhängige Risikomanager im Executor prüft danach trotzdem noch einmal.

## Die wichtigsten Entscheidungen (verständlich erklärt)

1. **Nur Papier-Handel, technisch erzwungen.** Es gibt im Code schlicht keinen „Live“-Modus. Setzt jemand `TRADING_MODE=live`
   oder Coinbase-/Bitget-Schlüssel, bricht das Programm sofort ab. Ein Test sucht außerdem den gesamten Quellcode nach
   Netzwerk-Bibliotheken und fest eingetragenen Passwörtern ab.
2. **Zwei Sicherheitsgurte.** Die Agenten (auch die KI-gestützten) *schlagen vor*. Ein davon unabhängiger, starrer Risikomanager
   *prüft* – und der Executor prüft **noch einmal selbst**, bevor etwas ausgeführt wird. Selbst wenn SORA eine zu große Position
   vorschlägt, wird sie blockiert (ein Test beweist das).
3. **Im Zweifel nicht handeln („fail closed“).** Veraltete Kurse, Kurssprünge über 20 %, vertauschte Bid/Ask, fehlende Funding-Rate bei Futures,
   abgestürzte Agenten oder ein beschädigtes Protokoll führen zu *keinem* Trade.
4. **Limits sind eingefroren.** Die Obergrenzen stehen im Code und können zur Laufzeit nur *strenger* konfiguriert werden, nie lockerer.
   Agenten bekommen die Limits gar nicht erst zu sehen.
5. **Realistisch-pessimistische Simulation.** Slippage und Gebühren bei Ein- und Ausstieg, Funding alle 8 Stunden, Stop-Loss mit
   zusätzlicher Slippage, bei Kurslücken Ausführung zum schlechteren Kurs, und wenn in einer Kerze Stop *und* Ziel erreicht wurden,
   gilt der Stop. Lieber Gewinne unterschätzen als überschätzen.
6. **Alles nachvollziehbar.** Jede Entscheidung, jeder Trade und jedes blockierte Setup (mit Stufe und Gründen) landet im
   Ereignisprotokoll. Jeder Eintrag enthält den Fingerabdruck (Hash) des vorherigen – nachträgliche Änderungen fallen auf.
7. **Geld wird mit `Decimal` gerechnet**, nicht mit Fließkommazahlen (sonst entstehen Rundungsfehler bei Beträgen).
8. **Der Kern braucht keine Fremdpakete.** Weniger Fremdcode = weniger Angriffsfläche. Der PostgreSQL-Treiber (`psycopg`) ist ein optionales Paket und wird
   nur im Paket `trading.persistence` importiert (ein Test stellt das sicher). FastAPI und WebSockets kommen erst in späteren Phasen.
9. **Die Datenbank ist die einzige Quelle der Wahrheit (Mehrprozess-Betrieb).** Jede Aktion ist *eine* Transaktion: Zuerst wird die Konto-Zeile gesperrt, alle
   anderen Prozesse warten in einer Schlange; dann entscheidet die bereits getestete Engine; Zustand **und** Protokolleintrag werden gemeinsam gespeichert oder gemeinsam
   verworfen. Stürzt ein Prozess ab, passiert nichts Halbes. Zusätzlich prüft die Datenbank das 2-%-Limit beim Commit selbst (zweiter Gurt), und ein Primärschlüssel
   verhindert doppelte Positions-Nummern (dritter). Die Anwendung nutzt eine eingeschränkte Rolle (`trading_app`): kein Löschen, kein Ändern des Protokolls oder der Limits.

### Annahmen, die ich getroffen habe (bitte prüfen!)

Sie stehen in `src/trading/safety/limits.py` und `src/trading/execution/costs.py` und sind leicht anzupassen:

* **0,5 % Risiko je Trade** (Obergrenze; Gesamtrisiko bleibt 2 % wie gewünscht → höchstens ca. 4 Trades gleichzeitig mit voller Größe)
* **Maximal 5× Hebel**, höchstens 5 offene Positionen
* **Notbremsen:** keine neuen Trades ab 3 % Tagesverlust oder 10 % Drawdown vom Höchststand
* **Gebühren:** 0,06 % Taker / 0,02 % Maker, Slippage 2 bp (Einstieg) bzw. 5 bp (Stop) – *Schätzwerte, keine echten Tarife*
* **Daten gelten als veraltet** nach 15 Sekunden
* **Spot-Short ist verboten** (nur Futures dürfen short gehen); Spot nur ohne Hebel

## Projekt starten

### Einmalig einrichten (macOS, kostenlos, ohne Administrator-Rechte)

```bash
brew install python@3.12 colima docker docker-compose   # Python 3.12 + Docker (Open Source) + Compose
colima start --cpu 2 --memory 4 --disk 20                # startet die kleine Docker-Umgebung
```

Hinweis: `python3` im Terminal bleibt das System-Python; Python 3.12 heißt `python3.12`. Compose wird als `docker-compose` aufgerufen
(nicht `docker compose`). Beenden/Entfernen: `colima stop` bzw. `colima delete`.

### Passwörter (außerhalb von OneDrive ablegen!)

OneDrive lädt alles in diesem Ordner in die Cloud hoch. Lege deshalb die Datenbank-Passwörter **nicht** als `.env` ins Projekt, sondern in eine
Datei außerhalb, z. B. `~/trading-secrets.env` (Rechte `chmod 600`), mit zwei langen Zufallswerten (`openssl rand -hex 16`):

```
POSTGRES_PASSWORD=...
APP_DB_PASSWORD=...
```

### Tests ausführen

```bash
python3.12 -m venv ~/venvs/trading312 && ~/venvs/trading312/bin/pip install pytest "psycopg[binary]" ruff mypy
~/venvs/trading312/bin/pytest                      # ohne Datenbank: DB-Tests werden als "skipped" gemeldet
```

Alle Tests inklusive PostgreSQL (Datenbank, Trigger, mehrere Prozesse, Neustart):

```bash
docker-compose --env-file ~/trading-secrets.env up -d --build db     # startet PostgreSQL (nur 127.0.0.1)
docker-compose --env-file ~/trading-secrets.env run --rm app pytest  # alle Tests in Python 3.12 im Container
```

Oder direkt vom Mac aus (benötigt die beiden Variablen aus der Passwort-Datei):

```bash
set -a; source ~/trading-secrets.env; set +a
export TEST_ADMIN_URL="postgresql://trading:${POSTGRES_PASSWORD}@127.0.0.1:5432/postgres" TEST_APP_PASSWORD="$APP_DB_PASSWORD"
~/venvs/trading312/bin/pytest
```

**Achtung Datenverlust:** `docker-compose down` behält die Daten, `docker-compose down -v` **löscht** die Datenbank (Volume).

### Demo (synthetische Daten)

```bash
PYTHONPATH=src python3.12 -m trading.demo         # oder: docker-compose --env-file ~/trading-secrets.env run --rm app
```

### Neustart-Prüfung von Hand

```bash
export DATABASE_URL="postgresql://trading_app:${APP_DB_PASSWORD}@127.0.0.1:5432/trading" PYTHONPATH=src
python3.12 scripts/restart_check.py seed   /tmp/state.json    # 2 offene Paper-Positionen anlegen
docker-compose --env-file ~/trading-secrets.env restart db    # (oder stop/start, down/up, colima stop/start, docker kill)
python3.12 scripts/restart_check.py verify /tmp/state.json    # muss "IDENTISCH" melden
```

## Aufbau

Siehe [AGENTS.md](AGENTS.md) (Regeln, Struktur, Agenten, nächste Schritte). Kurzfassung des Datenflusses:

```
MOMO (Idee) → RIN (prüft Kurse/Entry/SL/TP) → KIBO (Technik) → YUZU (Umfeld) → SORA (Größe/Risiko)
            → Executor + unabhängiger Risikomanager → Paper-Position
AKI protokolliert jeden Schritt, auch jede Blockade.
```

## Bekannte Grenzen (ehrlich)

* **Keine Strategie, kein Profitnachweis.** Es existiert noch keine Handelslogik (MOMO/KIBO/YUZU sind Schnittstellen).
* **Docker nur lokal getestet:** Colima auf macOS (Apple Silicon). Nicht getestet: Docker Desktop, Linux-Server (Hetzner), `amd64`-Architektur.
* **Python 3.13/3.14 und das neue GIL-freie Python** wurden nicht getestet (Ziel ist 3.12).
* **Ein Konto = ein Flaschenhals:** Die Konto-Sperre reiht alle Aktionen eines Kontos hintereinander ein (bewusst, Sicherheit vor Tempo). Dauert eine Aktion länger als 10 Sekunden
  auf die Sperre, wird sie abgebrochen und gilt als blockiert (`EXECUTION_ERROR`). Auch `portfolio_state()` nimmt kurz die Sperre.
* **Kein Backup, keine Replikation, kein TLS:** Die Datenbank hört nur auf `127.0.0.1`. Vor dem VPS-Betrieb: Backups, Verschlüsselung, Zugriffsschutz.
* **Ein Datenbank-Superuser kann Schutzmaßnahmen abschalten** (`ALTER TABLE ... DISABLE TRIGGER`). Die Anwendung nutzt ihn nie; die Hash-Kette samt Kettenende-Zeiger macht
  solche Eingriffe sichtbar (getestet), verhindert sie aber nicht. Das Passwort des Superusers gehört nur in die lokale Passwort-Datei.
* **Hash-Kette ≠ Manipulationsschutz gegen Angreifer mit Systemzugriff.** Sie macht Änderungen *sichtbar*; wer die gesamte Kette (und den Zeiger) neu berechnet,
  fällt nicht auf. In der **Datei-Variante** bleibt auch das Löschen *letzter* Einträge unentdeckt (in der Datenbank-Variante erkennt es der Kettenende-Zeiger).
  Spätere Maßnahme: das Kettenende regelmäßig extern ablegen.
* **Menge/Preise ohne Börsenregeln** (Mindestgröße, Tick-Größe, Lot-Schritte) – kommt mit den Börsen-Adaptern.
* **Vereinfachte Futures-Mathematik:** isolierte Margin, einfache Liquidationsformel, Funding auf Schlusskurs der Kerze. Reale Börsen weichen ab.
* **Kein Orderbuch-Modell:** Slippage ist ein fester Wert, unabhängig von der Ordergröße.
* **Keine Teilausführungen, keine Limit-Einstiegsorders, kein Trailing-Stop.**
* **Pipeline-Freigaberegel ist bewusst simpel** (alle müssen zustimmen); eine gewichtete Entscheidung braucht Belege aus Backtests.
* **Python 3.9-Kompatibilität** wurde in Phase 1/2 geprüft; ab Phase 3 laufen die offiziellen Tests unter Python 3.12. Der Kern (ohne Datenbank) ist weiter 3.9-kompatibel, `trading.persistence` getestet nur unter 3.12.
* **Risiko-Limit gilt zum Zeitpunkt der Freigabe.** Fällt das Eigenkapital später (Verluste, ungünstige Kurse), kann das *geplante* Risiko offener Positionen
  rechnerisch über 2 % des dann kleineren Kapitals steigen. Es werden dann keine neuen Trades zugelassen, bestehende laufen mit ihrem Stop weiter.
* **SORA kennt nur das Kapital und die Positionen des Executors.** Aufträge, die erst *geplant*, aber noch nicht ausgeführt sind, zählen nicht – deshalb werden
  Dimensionierung und Ausführung in der Pipeline unteilbar ausgeführt. Mehrere *getrennte* Pipelines auf demselben Executor sind dadurch ebenfalls sicher,
  Über mehrere Prozesse hinweg sichert die Datenbank-Sperre (siehe oben) das Limit; dort kann eine Freigabe von SORA durch den Risikomanager noch *abgelehnt* werden,
  wenn ein anderer Prozess inzwischen Risiko belegt hat (sicher, aber ohne automatische Verkleinerung).
* **Mengenschritt und Mindestgröße** (0,0001 / 10 USD) sind für alle Symbole gleich angenommen; echte Börsenregeln je Paar kommen mit den Adaptern.

## Sicherheits-Checkliste für später (VPS)

Kein Betrieb auf dem Hetzner-VPS, bevor: Firewall aktiv, SSH nur per Schlüssel, Datenbank nicht öffentlich erreichbar,
`.env` nur mit Dateirechten 600, regelmäßige Updates, Backups des Ereignisprotokolls.
