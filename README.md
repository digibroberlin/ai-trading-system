# AI Trading System (nur PAPER-Modus)

Technische Grundlage für einen autonomen KI-Krypto-Trading-Bot. **Dieses Projekt handelt nicht mit echtem Geld.**
Es gibt keinen Code, der eine echte Order senden könnte (keine Netzwerk-Bibliothek, kein Börsen-Adapter). Die Startprogramme
(Vorführung und Backtest) brechen außerdem ab, sobald ein anderer Modus als `paper` oder Börsen-Schlüssel gesetzt sind.

> **Wichtig:** Dass der Bot Gewinne macht, ist **nicht bewiesen**. Das muss erst durch Backtests und mehrwöchiges Paper-Trading
> mit realistischen Kosten gezeigt werden. Die Tests in diesem Stand prüfen nur, ob die **Technik** korrekt arbeitet.

## Stand (Stufe 1: integriertes Paper-Trading-Grundsystem mit Backtesting)

| Baustein | Status |
|---|---|
| Projektstruktur, Python-Paket, Docker (Colima), PostgreSQL 16 | **läuft und ist getestet** (Python 3.12.14 auf dem Mac, 3.12.15 im Container) |
| Instrument = Börse + Markttyp + Symbol (Spot und Futures, Coinbase/Bitget vorbereitet) | **neu in Stufe 1**, getestet |
| Marktdaten: vollständige Kerzenprüfung, zeitliche Reihenfolge, Lücken/Duplikate, Funding als Ereignis | **neu in Stufe 1**, getestet |
| Risikolimits, alle gleichzeitig: 2 % Gesamtrisiko, 0,5 % je Trade, 100 % Brutto-Exposure, 5 % Verlust bei 10 % Kursschock | **neu/erweitert in Stufe 1**, getestet |
| Positionen schließen: Stop-Loss, Take-Profit, Liquidation, Ausstiegssignal, Eingriff | **erweitert in Stufe 1**, getestet |
| Kapitalkurve und Performance-Bericht (alles netto nach Kosten) | **neu in Stufe 1**, getestet |
| Backtesting-Engine (deterministisch, ohne Blick in die Zukunft) | **neu in Stufe 1**, getestet – siehe [docs/BACKTESTING.md](docs/BACKTESTING.md) |
| Konto, Positionen, Orders, Marktzustand und Protokoll in PostgreSQL; Schema-Migrationen | funktionsfähig, getestet (12 echte Prozesse gleichzeitig) |
| Datenbank-Trigger: Protokoll unveränderlich, Gesamtrisiko und Exposure beim Commit, nur Paper-Modus | funktionsfähig, getestet |
| Neustart: offene Paper-Positionen, Handelstag und Notbremsen bleiben erhalten | getestet |
| Sechs Agenten | RIN, SORA, AKI, **KIBO (Technik) und YUZU (Marktumfeld)** regelbasiert implementiert (KIBO/YUZU seit AP-17, optional mit KI-Zweitmeinung über den Kosten-Orchestrator) · MOMO: Strategie-Bibliothek (AP-16) |
| Paper Executor, unabhängiger Risikomanager, Datenprüfung, Ereignisprotokoll (Hash-Kette) | funktionsfähig, getestet |
| Austauschbare KI-Modelle | nur Schnittstelle (kein echter Anbieter, keine Kosten) |
| Strategie-Bibliothek (6 Strategien + Vergleichsmaßstäbe), Marktphasen | gebaut und getestet (AP-16/17), nur auf erfundenen Daten |
| Dashboard (nur lesend, Browser) | gebaut und getestet (AP-23/24), siehe unten |
| Laufzeit-Engine (ereignisgesteuert, alle sechs Agenten) | gebaut und getestet (AP-21), **standardmäßig aus** – Start nur mit Ihrer Freigabe |
| Echte historische Daten, Live-Marktdaten, Börsen-Adapter | **noch nicht gebaut** |

Testergebnis: **606 Tests bestanden, 0 übersprungen** auf Python 3.12 mit PostgreSQL 16 – auf dem Mac **und** im Docker-Container.
Ohne Datenbank laufen 512 Tests, 94 Datenbank-Tests werden dann sichtbar als „skipped“ gemeldet.
Gegenprobe: 193 absichtlich eingebaute Fehler, alle von den Tests bemerkt (`scripts/mutation_check.py`).
Details und **noch offene Punkte** in [docs/TESTBERICHT.md](docs/TESTBERICHT.md) und [docs/AUDIT.md](docs/AUDIT.md).

## Was SORA und RIN jetzt tun

**SORA (Risiko und Größe)** rechnet vom *tatsächlich erwarteten* Einstiegskurs (inkl. Spread und Slippage), nie vom Wunschkurs:

1. **Größte Menge, die alle Grenzen zugleich einhält:** Ziel-Risiko je Trade (0,4 %), 2 % Gesamtrisiko aller offenen Positionen,
   100 % Brutto-Exposure, 5 % Verlust beim 10-%-Kursschock und die freie Margin. Das gilt für **alle Strategien gemeinsam** –
   es gibt nur einen Topf. Möglicher Verlust je Stück = Kursverlust bis zum Stop + Gebühren + Stop-Slippage + ein Funding-Puffer.
2. **Abgerundet**, danach **Endkontrolle mit derselben Rechenfunktion wie der Risikomanager** (`safety/order_risk.py`).
   Lehnt sie ab, gibt es keinen Vorschlag.
3. **Hebel (nur Futures):** der *kleinste* Hebel, bei dem die Margin reicht; nie über dem Limit, und nur wenn die Liquidation mindestens
   1,5× so weit entfernt ist wie der Stop. Reicht die Margin selbst dann nicht, wird die Position **verkleinert, nie vergrößert**.
   Spot: immer 1×, nur so viel, wie bar bezahlbar ist.
4. **Blockiert automatisch:** Spot-Short, falsche Stop-/Ziel-Seite, fehlende/ungültige Zahlen, Tagesverlust-/Drawdown-Notbremse,
   zu viele Positionen, doppelte Position, kein Restbudget, Liquidation zu nah, zu kleine Position, **veraltete Bewertung
   offener Positionen** (das Kapital ist dann nicht sicher bestimmbar).

**RIN (Validierung)** prüft und sammelt *alle* Verstöße:

* **Instrument** (Börse + Markttyp + Symbol) – korrektes Format *und* ausdrücklich erlaubt (sonst verboten)
* **Marktdaten** – vollständig, endlich, aktuell (≤ 15 s), Spread, Kurssprung, Kerzen konsistent, Futures nur mit Funding-Rate
* **Setup** – nicht älter als 60 s, Entry max. 0,5 % vom Markt entfernt, Stop/Ziel auf der richtigen Seite *auch gegenüber dem aktuellen Kurs*
* **Stop-Abstand** – nicht kleiner als das 3-Fache der Round-Trip-Kosten (sonst frisst der Spread den Stop auf), nicht größer als 10 %
* **Netto-Chance-Risiko ≥ 1,5** *nach* Gebühren, Slippage, Spread und Funding

Beides sind feste Regeln, **keine KI**. Der unabhängige Risikomanager im Executor prüft danach trotzdem noch einmal.

## Die wichtigsten Entscheidungen (verständlich erklärt)

1. **Nur Papier-Handel, technisch erzwungen.** Es gibt im Code schlicht keinen „Live“-Modus. Setzt jemand `TRADING_MODE=live`
   oder Coinbase-/Bitget-Schlüssel, brechen die Startprogramme sofort ab (ein zentraler Startpunkt für *jeden* Prozess folgt in Stufe 2). Ein Test sucht außerdem den gesamten Quellcode nach
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
9. **Eine einzige Risikorechnung.** Risikomanager und SORA benutzen dieselbe Funktion. Geprüft wird der Zustand **nach** der Order
   gegen das Kapital **nach** den dabei anfallenden Kosten – und alle Grenzen müssen gleichzeitig halten.
10. **Marktdaten werden nicht geglaubt, sondern geprüft.** Kerzen müssen vollständig, in sich stimmig, abgeschlossen und in zeitlicher
    Reihenfolge sein; doppelte oder ältere Kerzen werden verworfen und protokolliert, Lücken werden protokolliert. Funding wird nur
    aus ausdrücklichen Daten gebucht, je Position höchstens einmal.
11. **Backtest = dieselbe Maschine.** Ein Backtest läuft durch dieselbe Pipeline, denselben Risikomanager und denselben Executor wie
    der Paper-Betrieb. Die Strategie sieht nur Vergangenes; ausgeführt wird zum nächsten Kurs.
12. **Gegenprobe der Tests.** Ein Werkzeug baut bekannte Fehler in eine Kopie des Projekts ein. Bemerken die Tests einen davon nicht,
    fehlt ein Test.
13. **Die Datenbank ist die einzige Quelle der Wahrheit (Mehrprozess-Betrieb).** Jede Aktion ist *eine* Transaktion: Zuerst wird die Konto-Zeile gesperrt, alle
   anderen Prozesse warten in einer Schlange; dann entscheidet die bereits getestete Engine; Zustand **und** Protokolleintrag werden gemeinsam gespeichert oder gemeinsam
   verworfen. Stürzt ein Prozess ab, passiert nichts Halbes. Zusätzlich prüft die Datenbank Gesamtrisiko und Brutto-Exposure beim Commit selbst (zweiter Gurt), und ein Primärschlüssel
   verhindert doppelte Positions-Nummern (dritter). Die Anwendung nutzt eine eingeschränkte Rolle (`trading_app`): kein Löschen, kein Ändern des Protokolls oder der Limits.

### Annahmen, die ich getroffen habe (bitte prüfen!)

Sie stehen in `src/trading/safety/limits.py` und `src/trading/execution/costs.py` und sind leicht anzupassen:

* **0,5 % Risiko je Trade** (Obergrenze; Gesamtrisiko bleibt 2 % wie gewünscht → höchstens ca. 4 Trades gleichzeitig mit voller Größe)
* **100 % Brutto-Exposure und 5 % Verlust bei 10 % Kursschock** (von Ihnen festgelegt). Wichtig zu wissen: Ohne Absicherung bedeutet
  ein 10-%-Schock auf 50 % Exposure bereits rund 5 % Verlust. Das Stress-Limit begrenzt die Gesamtgröße aller Positionen daher
  praktisch auf **knapp 50 % des Kapitals** – die 100-%-Grenze wird selten erreicht.
* **Beträge auf 8 Nachkommastellen**, Kosten aufgerundet, Gewinne abgerundet; bei Liquidation gilt die gesamte Margin als verloren
* **Maximal 5× Hebel**, höchstens 5 offene Positionen
* **Notbremsen:** ab 3 % Tagesverlust oder 10 % Drawdown vom Höchststand löst der **globale Not-Aus** aus (seit AP-07). Er gilt
  auch am nächsten Tag und nach Neustarts weiter, bis ein Mensch ihn bewusst aufhebt (früher löste sich die Tagesbremse um
  Mitternacht von selbst).
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
python3.12 -m venv ~/venvs/trading312 && ~/venvs/trading312/bin/pip install --require-hashes -r requirements/dev.lock
~/venvs/trading312/bin/pytest                      # ohne Datenbank: DB-Tests werden als "skipped" gemeldet
```

Alle Tests inklusive PostgreSQL (Datenbank, Trigger, mehrere Prozesse, Neustart) im Container:

```bash
docker-compose --env-file ~/trading-secrets.env up -d --build db       # startet PostgreSQL (nur 127.0.0.1)
docker-compose --env-file ~/trading-secrets.env run --rm tests         # alle Tests in Python 3.12 im Container
```

Oder direkt vom Mac aus (benötigt die beiden Variablen aus der Passwort-Datei):

```bash
set -a; source ~/trading-secrets.env; set +a
export TEST_ADMIN_URL="postgresql://trading:${POSTGRES_PASSWORD}@127.0.0.1:5432/postgres" TEST_APP_PASSWORD="$APP_DB_PASSWORD"
~/venvs/trading312/bin/pytest
~/venvs/trading312/bin/python scripts/mutation_check.py     # Gegenprobe: bemerken die Tests eingebaute Fehler? (dauert einige Minuten)
```

Die Tests legen kurzlebige Test-Datenbanken an und entfernen sie wieder; die Datenbank `trading` wird dabei nicht angefasst.

### Datenbank-Schema anlegen oder aktualisieren (Migrationen)

```bash
docker-compose --env-file ~/trading-secrets.env exec -T db pg_dump -U trading trading > backups/datenbank_$(date +%F).sql   # erst sichern!
docker-compose --env-file ~/trading-secrets.env run --rm migrate
```

Das Schema besteht aus nummerierten Schritten in `db/migrations/`. Der Läufer wendet nur fehlende Schritte an, jeden in einer
Transaktion, und bricht ab, wenn eine bereits angewendete Datei nachträglich verändert wurde.

**Achtung Datenverlust:** `docker-compose down` behält die Daten, `docker-compose down -v` **löscht** die Datenbank (Volume).

### Vorführungen (synthetische Daten)

```bash
PYTHONPATH=src python3.12 -m trading.demo         # ein Paper-Durchlauf; oder: docker-compose --env-file ~/trading-secrets.env run --rm app
PYTHONPATH=src python3.12 -m trading.backtest     # Backtest: alle Strategien gegen Kaufen-und-Halten und Nicht-Handeln
PYTHONPATH=src python3.12 -m trading.dashboard    # Dashboard im Browser: http://127.0.0.1:8000 (nur lesend)
PYTHONPATH=src python3.12 -m trading.research     # Forschungsrahmen: Validierung/Test aller Strategien (erfundene Daten)
```

### Dashboard (AP-23/24, D-020)

`python3.12 -m trading.dashboard` rechnet alle Strategien als Backtest auf erfundenen Daten und zeigt sie unter
**http://127.0.0.1:8000** an: Kapital, Netto-Ergebnis, Kapitalkurve und Drawdown, Kennzahlen, offene Positionen,
abgeschlossene Trades, die sechs Agenten mit Zustimmungen/Ablehnungen, Blockaden nach Grund, Strategievergleich,
Kosten (Handel, KI je Agent/Modell/Monat, Budget, Betriebskosten) und das Nettoergebnis nach allen Kosten, Fehler und
Warnungen. Die Seite aktualisiert sich selbst (WebSocket). `--strategy momentum-20` wählt die angezeigte Strategie.
**Nur lesend:** Kein Knopf und kein Endpunkt kann handeln, freigeben oder etwas ändern; LIVE ist gesperrt. Standardmäßig
nur von diesem Rechner aus erreichbar (keine Anmeldung – nicht ins Internet stellen).

![Dashboard mit synthetischen Daten](docs/img/dashboard.png)

`python3.12 -m trading.dashboard --simulation` spielt stattdessen die Laufzeit-Engine einmal im Zeitraffer mit erfundenen
Daten ab und zeigt das Paper-Konto im Modus DEMO (kein laufender Betrieb, keine Marktdaten, keine Börse).

### Forschungsrahmen (AP-18/19, D-022)

`trading.research`: Training, Validierung und Test liegen zeitlich hintereinander (mit Sperrabstand); Walk-forward-Fenster
überlappen nie. Jede geprüfte Variante zählt als Versuch – je mehr Versuche, desto höher die geforderte Sharpe-Ratio
(Schutz gegen „so lange probieren, bis Zufall gut aussieht“). Bestanden ist nur, was nach allen Kosten Gewinn bringt,
genug Trades hat, die Schwelle schlägt und auch bei 1,5-fachen Kosten noch im Plus ist. Die **Testdaten darf jede
Version genau einmal sehen**; das Register (JSON-Datei) merkt sich das. „Freigegeben für Paper“ geht nur nach bestandenem
Test und mit Namen – das ist eine Empfehlung, kein Start. AKI bewertet nach allen Kosten und je Marktphase und prüft
gepaart, ob KIBO/YUZU gegenüber „ohne Filter“ etwas bringen (bei wenigen Fenstern ehrlich: „zu wenig Daten“).

### Historische Marktdaten laden (AP-14, D-025) – nur lesend

```bash
PYTHONPATH=src python3.12 -m trading.marketdata --exchange coinbase --market spot --symbol BTC-USD \
    --interval 3600 --start 2026-09-01 --end 2026-09-08
PYTHONPATH=src python3.12 -m trading.marketdata --exchange bitget --market futures --symbol BTC-USDT \
    --interval 3600 --start 2026-09-01 --end 2026-09-08
```
Lädt öffentliche Kerzen (bei Futures auch die Funding-Sätze) **ohne Schlüssel**, prüft sie streng (nichts wird aufgefüllt)
und speichert abgeschlossene Tage unter `data/marketdata` (nicht im Repository). Das ist der einzige Netzzugang des Systems:
nur Lesen, nur `api.exchange.coinbase.com` und `api.bitget.com`.

### Marktüberwachung live ansehen (AP-15, D-026) – ohne Handel

```bash
PYTHONPATH=src python3.12 -m trading.marketdata.watch --exchange bitget --symbol BTC-USDT --interval 60 --minutes 5
```
Zeigt jede geschlossene, geprüfte Kerze aus dem öffentlichen WebSocket-Strom und am Ende den Zustand (Verbindungen,
nachgeladene Lücken, verworfene Nachrichten). Bei Ausfall: neu verbinden, Lücken per REST nachladen; bleiben die Daten
120 s aus, würde der Not-Aus ausgelöst (hier nur angezeigt).

### Laufzeit-Engine (AP-21, D-021) – bleibt aus

`trading.runtime.engine.RuntimeEngine` verarbeitet Marktereignisse einzeln: Kerze → Executor (Stops/Ziele) →
Marktphase (bei Wechsel ein Ereignis) → Strategie für genau dieses Instrument → volle Pipeline mit allen sechs Agenten →
AKI. KI nur ereignisbasiert über den Kosten-Orchestrator; die Kosten je Ereignis werden gemessen. Nach drei Fehlern in Folge
löst sie den Not-Aus aus. **Starten lässt sie sich nur**, wenn alles zutrifft: `EngineConfig(enabled=True)`, Modus PAPER
ohne Börsen-Schlüssel und Ihre ausdrückliche Freigabe `TRADING_ENGINE_APPROVAL="PAPER-HANDEL FREIGEGEBEN"`. Echte
Marktdaten-Anbindungen (AP-14/15) fehlen noch – bis dahin läuft sie nur in Tests und Simulationen.

### Globaler Not-Aus (Kill Switch)

Ist er ausgelöst, eröffnet **kein** Prozess und **kein** Konto eine neue Position; Stop-Loss, Take-Profit, Ausstiegssignale und
Funding laufen weiter. Er löst von selbst aus bei formal kaputten Marktdaten (z. B. NaN, negative Kurse, Zeitstempel aus der
Zukunft), bei einem Fehler im Ereignisprotokoll und bei den Notbremsen. Aufheben kann ihn nur ein Administrator – mit Namen
und dem Bestätigungstext `HANDEL FREIGEBEN`:

```bash
export DATABASE_URL="postgresql://trading_app:${APP_DB_PASSWORD}@127.0.0.1:5432/trading" PYTHONPATH=src
python3.12 -m trading.persistence.kill_switch status                 # Zustand anzeigen
python3.12 -m trading.persistence.kill_switch halt "Grund"           # von Hand auslösen
export ADMIN_DATABASE_URL="postgresql://trading:${POSTGRES_PASSWORD}@127.0.0.1:5432/trading"
python3.12 -m trading.persistence.kill_switch release "Ihr Name" "HANDEL FREIGEBEN"   # bewusst aufheben
```
Vor dem Aufheben die Ursache klären (Ereignisprotokoll: `HALT_ACTIVATED` nennt Grund und Hinweis). Das Aufheben nimmt den
bisherigen Verlust bewusst hin: Tagesbeginn und Höchststand der Notbremsen werden auf das aktuelle Kapital gesetzt.
Rückgabecodes: `status` 0 = frei, 1 = gestoppt; `halt`/`release` 0 = erledigt, 2 = nicht möglich (Meldung „FEHLER: …“).

### KI-Kosten und Budget (D-016)

Ziel ist der Nettogewinn nach **allen** Kosten. Jede KI-Analyse läuft durch einen Orchestrator: zuerst die Regeln (kostenlos),
dann der Zwischenspeicher, dann nur bei passenden Ereignissen das **billigste** Modell; ein teureres nur, wenn die Antwort
unsicher war und sich der Aufwand lohnt. Vor jedem Aufruf wird die höchstmögliche Kost gegen ein hartes Budget reserviert.
**Ohne Freigabe ist das Budget 0 – es gibt keinen einzigen bezahlten Aufruf.** Freigabe (erst nach Ihrer Entscheidung, B-004):

```bash
export AI_BUDGET_PER_CALL_USD=0.05 AI_BUDGET_DAILY_USD=1 AI_BUDGET_MONTHLY_USD=20   # Grenzen der Anwendung
# zusätzlich in der Datenbank (gilt für alle Prozesse und über Neustarts; die strengere Grenze gewinnt):
PYTHONPATH=src python3.12 -c "from decimal import Decimal as D; from trading.ai.budget import AIBudget; \
  from trading.persistence.ai_budget import set_limits_as_admin; import os; \
  set_limits_as_admin(os.environ['ADMIN_DATABASE_URL'], AIBudget(D('0.05'), D('1'), D('20')), 'Ihr Name')"
```
Tage und Monate zählen nach der Uhr der **Datenbank** (UTC), nicht nach der Uhr der Anwendung. Lagen die tatsächlichen Kosten
einmal über der Reservierung (oder meldet der Anbieter keinen Verbrauch), sperrt sich das Budget für alle Prozesse. Aufheben
nur nach Prüfung der Ursache, mit Ihrem Namen (er wird mit Zeitpunkt gespeichert):

```bash
PYTHONPATH=src python3.12 -c "from trading.persistence.ai_budget import unblock_as_admin; import os; \
  unblock_as_admin(os.environ['ADMIN_DATABASE_URL'], 'Ihr Name')"
```
AKI weist aus: KI-Aufrufe, Tokens und Kosten je Agent und Modell, je Analyse und je Signal, je Tag und Monat, KI-Kosten je
Trade (direkt und voll umgelegt), Server-/Marktdaten-/sonstige Kosten und das **Nettoergebnis nach allen Kosten**.

**Claude-Anschluss (AP-22, D-024):** `trading.ai.anthropic_provider` verbindet den Orchestrator mit der Claude-API
(Haiku → Sonnet → Opus nur mit Wertschranke). Er ist fertig, aber **nicht eingeschaltet** und nur mit einer Attrappe
getestet. `live_registry(...)` baut ihn erst, wenn alles zutrifft: `TRADING_AI_APPROVAL="KI-AUFRUFE FREIGEGEBEN"`,
`ANTHROPIC_API_KEY` gesetzt (wird nie angezeigt), Paket `anthropic` installiert, Preistabelle höchstens 60 Tage alt und mit
Preis für jede Stufe. Auch dann gilt das Budget oben: Budget 0 = kein bezahlter Aufruf.

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

### Strategie-Bibliothek (AP-16, D-018)

`src/trading/strategies/`: Trendfolge (EMA-Kreuzung), Momentum (Veränderungsrate), Mean Reversion (Bollinger + RSI),
Breakout (Donchian-Kanal), Volatilität (Ausbruch nach enger Phase), Scalping (RSI(2) im Trend) sowie die Vergleichsmaßstäbe
**Nicht-Handeln** und **Kaufen-und-Halten** (nach Spread, Slippage, Gebühren und Funding). Jede Strategie **schlägt nur vor**
(mit Stop-Loss und Ziel aus der ATR); RIN, SORA, Risikomanager und Executor entscheiden. Keine ist optimiert oder als
profitabel belegt – auf erfundenen Daten ist nach Kosten ein leichtes Minus normal.

```python
from trading.strategies import build_strategy, default_strategies, buy_and_hold
ergebnis = BacktestEngine(datensatz, build_strategy("breakout"), BacktestConfig()).run()
```

## Bekannte Grenzen (ehrlich)

* **Marktüberwachung (AP-15) nur simuliert geprüft:** simulierter Server und lokales WebSocket-Gegenstück; die
  Live-Probe wartet auf die Netzfreigabe (B-008). Bitget-Kerzen: Schlusswert = letzter Stand vor der nächsten Kerze.
  Futures-Funding kommt im laufenden Betrieb noch nicht über den Strom (Futures werden dann blockiert – fail closed).
  Der WebSocket-Zugang braucht Python ≥ 3.10.
* **Marktdaten-Adapter (AP-14) noch nicht gegen die echten Börsen geprüft:** Die Antwortformen sind nach der
  Schnittstellenbeschreibung nachgebildet; der eine echte Testabruf scheitert in der Cloud an der Netzregel (B-008). Der
  Kurs zur Funding-Abrechnung ist eine Näherung (Kerzen-Schluss statt Mark-Preis). Bitget nur bis 4-Stunden-Kerzen.
* **Kein Profitnachweis.** Strategien (AP-16), KIBO und YUZU (AP-17) sind regelbasiert und technisch geprüft, aber nur
  auf erfundenen Daten. Ob sie nach allen Kosten einen Vorteil bringen, ist offen (AP-18/20). KIBO/YUZU geben bei zu wenig
  Kerzen (unter ~115) kein Urteil ab und halten dann nichts auf – die Sicherheit hängt an RIN, SORA und dem Risikomanager.
* **Nur erfundene Daten.** Es gibt noch keinen Adapter für echte historische oder laufende Marktdaten.
* **Das Stress-Limit wirkt strenger als die Exposure-Grenze** (siehe „Annahmen“): praktisch knapp 50 % Gesamtgröße.
* **Der Stress-Test wird nur in der Anwendung gerechnet** (unter der Konto-Sperre). Die Datenbank prüft beim Commit zusätzlich
  Gesamtrisiko und Exposure, den Stress-Test nicht.
* **PAPER-Schutz** läuft zentral beim ersten `import trading` in jedem Prozess (AP-06). Er erkennt Börsen-Schlüssel an typischen
  Namen und an zwei Inhalts-Mustern (Coinbase-CDP), nicht an jedem denkbaren Namen: Ein Schlüssel unter einem frei erfundenen Namen
  (z. B. `MEIN_WERT`) bliebe unbemerkt. Zugänge der Cloud-Umgebung wie `AWS_SECRET_ACCESS_KEY` oder `GH_TOKEN` sind bewusst erlaubt.
  Einen Live-Modus und eine Order-Funktion an eine Börse gibt es im Code nirgends.
* **Konten legt nur ein Administrator an** (AP-10, `python -m trading.persistence.accounts create KONTO STARTKAPITAL`).
  Jedes Konto hat ein **eigenes** 2-%-Risikobudget – ein gemeinsames Limit über alle Konten gibt es nicht; deshalb nur so viele
  Konten anlegen wie nötig. Den Kontostand schreibt weiterhin die Anwendung (nötig für den Betrieb); die Datenbank prüft jede
  Änderung nur auf Plausibilität (Version, Tag, Positionszähler, Höchststand), nicht die Beträge selbst.
* **Alt-Positionen in der lokalen Datenbank:** Im Konto `restart-check` liegen zwei Test-Positionen aus Phase 3, die die neue
  Stress-Grenze überschreiten (8,05 % statt 5 %). Sie bleiben bestehen; neue Trades in diesem Konto sind blockiert, bis sie geschlossen sind.
* **Docker nur lokal getestet:** Colima auf macOS (Apple Silicon). Nicht getestet: Docker Desktop, Linux-Server (Hetzner), `amd64`-Architektur.
  Seit AP-05 (Dockerfile ohne `pip install .`, nur Sperrdateien) wurde das Image **noch nicht gebaut** (Cloud: Docker Hub
  „429 Too Many Requests“); geprüft sind nur die Installationsschritte in frischen Python-Umgebungen.
* **Abhängigkeiten:** fest mit Prüfsummen in `requirements/*.lock` (D-010). Noch offen: Basis-Image `python:3.12-slim` nicht per Digest
  festgeschrieben, Container mit freiem Internetzugang. Der Python-3.9-Job nutzt bewusst pytest 8.4.2 mit einer bekannten, dort nicht
  ausnutzbaren Lücke (D-010).
* **Not-Aus reiht alle Konten hintereinander:** Jede Aktion sperrt nach ihrem Konto auch die Not-Aus-Zeile. Damit kann kein
  Prozess zwischen „Not-Aus gelesen“ und „Position gespeichert“ überholt werden – aber Aktionen verschiedener Konten laufen nicht
  mehr gleichzeitig. Für den geplanten Betrieb (ein Konto) ohne Folgen. Hängt ein Prozess mitten in einer Transaktion, blockiert
  er alle Konten und das Auslösen von außen, bis die Datenbank seine Sitzung beendet (spätestens nach rund 15 Sekunden).
* **Wiederverbindung zur Datenbank (AP-11, D-015):** Nach einem Verbindungsabbruch verbindet sich der Prozess selbst neu
  (höchstens 4 Versuche in rund 3,5 Sekunden, dann Fehler). Riss die Verbindung **während** einer Order ab, meldet der Executor
  einen Fehler statt still zu wiederholen; der Aufrufer sendet dieselbe Order-ID erneut. Ein Alarm bei Verbindungsproblemen
  fehlt noch (AP-25). Getestet mit vom Server beendeten Sitzungen, nicht mit echten Netzwerkausfällen oder Datenbank-Neustarts.
* **Not-Aus erkennt nur formal kaputte Daten**, keine plausibel aussehenden falschen Kurse. Ausbleibende Daten (veralteter Feed)
  lösen ihn noch nicht aus – das kommt mit der Marktüberwachung (AP-15).
* **Wiederholte Order (AP-08):** Gleiche Order-ID mit **exakt** gleichem Inhalt liefert das ursprüngliche Ergebnis (Status,
  Gründe, Position wie bei der Eröffnung) – ohne erneut zu handeln. Der Vergleich ist bewusst streng: Schon `100` statt `100.0`
  oder eine andere Zeitzonen-Schreibweise gelten als anderer Inhalt und werden als Duplikat abgelehnt. Die Risikobewertung der
  ersten Bearbeitung wird bei der Wiederholung nicht mitgeliefert (sie steht im Ereignisprotokoll). Orders aus der Zeit vor
  Migration 004 haben keinen Fingerabdruck; ihre Wiederholung wird weiter als Duplikat abgelehnt.
* **KI ist noch nicht angeschlossen:** Orchestrierung, Budgets und Kostenmessung sind fertig und nur mit einer Attrappe getestet.
  Einen echten Anbieter gibt es erst mit AP-22 nach Budget-Freigabe (B-004); KIBO/YUZU entscheiden bis dahin rein regelbasiert.
  Ob eine KI-Analyse ihr Geld wert ist, ist **unbewiesen**, bis der Vergleich (AP-33) auf echten Daten gelaufen ist.
* **Dashboard ohne Anmeldung:** gedacht für diesen Rechner (127.0.0.1). Für einen Server braucht es vorher Anmeldung und
  verschlüsselte Verbindung (AP-28). Der DEMO-Modus (laufendes Paper-Konto) ist vorbereitet und getestet, wird aber erst
  mit der Laufzeit-Engine (AP-21) live gespeist. Die Testhilfe von Starlette meldet eine Hinweis-Warnung (httpx → httpx2),
  ohne Einfluss auf das Ergebnis.
* **Börsenregeln (AP-13) nur für die Test-Börse hinterlegt:** Preis-/Mengenschritte, Mindestgrößen und Höchsthebel
  echter Börsen kommen mit den Adaptern (AP-14) aus deren Instrument-Angaben. Ein Backtest nutzt Regeln nur, wenn
  `BacktestConfig(rules=...)` gesetzt ist; ist ein Regelwerk gesetzt, wird ein Instrument ohne Regeln abgelehnt.
  Gebührenstufen nach Volumen gibt es als `FeeSchedule`; negative Maker-Gebühren (Rabatte) werden bewusst abgelehnt.
* **Strategien sind nicht erforscht:** Standard-Einstellungen aus der Fachliteratur, nur auf erfundenen Daten technisch
  geprüft. Ob eine davon nach allen Kosten einen Vorteil hat, klärt erst der Forschungsrahmen (AP-18) auf getrennten
  echten Daten (AP-20). Stop und Ziel werden auf 8 Nachkommastellen gerundet (zu Ungunsten), mit Börsenregeln auf deren Preisschritt
  (AP-13). Kaufen-und-Halten wird analytisch gerechnet (ohne Stop – mit den
  Sicherheitsregeln wäre ein Dauer-Long nicht handelbar).
* **KI-Budget – bewusst offene, kleinere Punkte:** Der Claude-Anschluss (AP-22) bricht jeden Aufruf nach 30 s ab; gleiche
  Analysen warten höchstens 120 s auf einen laufenden Aufruf (dann Regel-Ersatz). Meldet ein Anbieter einen *zu kleinen*, aber nicht
  leeren Verbrauch, lässt sich das ohne eigene Token-Zählung nicht erkennen (erkannt werden: kein Verbrauch, nur Ausgabe,
  0 Ausgabe-Tokens bei Text, anderes Modell, Überschreitung der Obergrenze). Lässt sich die Budget-Sperre nicht in die
  Datenbank schreiben, zahlt nur der betroffene Prozess bis zum Neustart nichts mehr.
* **Geheimnis-Scan:** erkennt bekannte Schlüsselformate überall, allgemeine Zuweisungen (`NAME=Wert`) aber nur in der
  einfachen Form – JSON-Schlüssel, `os.environ[...] = ...` und Zuweisungen mit Typangabe nur über die bekannten Formate.
* **Preise sind ein Stand (2026-10-06)** der Anbieter-Preisliste; der Cache-Lesepreis von Haiku 5.5 ist abgeleitet (0,1 × Eingabe).
  Die Kostenobergrenze vor einem Aufruf setzt voraus, dass ein Token nie kürzer als ein Byte ist; wird das je verletzt,
  sperrt sich das Budget selbst. Die Betriebskosten (Server, Daten) muss jemand eintragen – automatisch kommen nur KI-Kosten.
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
* **Vereinfachte Futures-Mathematik:** isolierte Margin, einfache Liquidationsformel, bei Liquidation volle Margin verloren. Reale Börsen weichen ab.
* **Kein Orderbuch-Modell:** Slippage ist ein fester Wert, unabhängig von der Ordergröße.
* **Keine Teilausführungen, keine Teilschließungen, keine Limit-Einstiegsorders, kein Trailing-Stop.**
* **Pipeline-Freigaberegel ist bewusst simpel** (alle müssen zustimmen); eine gewichtete Entscheidung braucht Belege aus Backtests.
* **SORA wird zweimal gefragt** (D-017): als Vorprüfung nach RIN und endgültig unter der Sperre des Executors. KIBO und YUZU
  sehen die vorgeschlagene Größe noch nicht (Schnittstelle unverändert).
* **Python 3.9-Kompatibilität** wurde in Phase 1/2 geprüft; ab Phase 3 laufen die offiziellen Tests unter Python 3.12. Der Kern (ohne Datenbank) ist weiter 3.9-kompatibel, `trading.persistence` getestet nur unter 3.12.
* **Risiko-Limit gilt zum Zeitpunkt der Freigabe.** Fällt das Eigenkapital später (Verluste, ungünstige Kurse), kann das *geplante* Risiko offener Positionen
  rechnerisch über 2 % des dann kleineren Kapitals steigen. Es werden dann keine neuen Trades zugelassen, bestehende laufen mit ihrem Stop weiter.
* **SORA kennt nur das Kapital und die Positionen des Executors.** Aufträge, die erst *geplant*, aber noch nicht ausgeführt sind, zählen nicht – deshalb werden
  Dimensionierung und Ausführung in der Pipeline unteilbar ausgeführt. Mehrere *getrennte* Pipelines auf demselben Executor sind dadurch ebenfalls sicher,
  Über mehrere Prozesse hinweg rechnet SORA innerhalb der Datenbank-Transaktion mit gesperrter Konto-Zeile (`submit_sized`, D-013): Hat ein anderer Prozess
  inzwischen Risiko belegt, wird die Position kleiner statt abgelehnt. Folge: Solange SORA rechnet, warten die anderen Prozesse – SORA muss deshalb schnell und
  regelbasiert bleiben (keine KI-Aufrufe unter der Sperre).
* **Mengenschritt und Mindestgröße** (0,0001 / 10 USD) sind für alle Symbole gleich angenommen; echte Börsenregeln je Paar kommen mit den Adaptern.

## Sicherheits-Checkliste für später (VPS)

Kein Betrieb auf dem Hetzner-VPS, bevor: Firewall aktiv, SSH nur per Schlüssel, Datenbank nicht öffentlich erreichbar,
`.env` nur mit Dateirechten 600, regelmäßige Updates, Backups des Ereignisprotokolls.
