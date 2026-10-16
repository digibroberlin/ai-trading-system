# Entscheidungen

Jede Entscheidung mit Datum, Begründung und Folgen. Neue Einträge unten anfügen; alte nicht löschen, sondern als
„ersetzt durch D-xxx“ markieren.

## D-001 · 2026-10-15 · Branch-Modell und stabile Stände
`main` = nur stabile Stände (je ein annotierter Tag `stable/NNN-name`); `develop` = geprüfte Zwischenstände;
`ap/<nr>-<name>` = Arbeitszweige je Arbeitspaket. Stabil heißt: `scripts/preflight.py` meldet STABIL (Geheimnis-Scan inkl.
Geschichte, ruff, mypy --strict, alle Tests **ohne** übersprungene). Für Meilensteine zusätzlich die Gegenprobe.
**Warum:** jederzeit zu einem funktionierenden Stand zurückkehren können; `main` nie durch Unfertiges überschreiben.
**Hinweis:** Auf einem kostenlosen GitHub-Konto lässt sich `main` bei privaten Repositories nicht technisch sperren
(Branch-Schutz ist dort kostenpflichtig). Der Schutz beruht deshalb auf diesem Ablauf und auf GitHub Actions.

## D-002 · 2026-10-15 · Datenbank-Abzüge aus der Git-Geschichte entfernt (vor der ersten Veröffentlichung)
Zwei Abzüge der lokalen Paper-Datenbank (`backups/*.sql`) waren eingecheckt. Sie enthielten keine Passwörter (geprüft), aber
Datenbank-Abzüge gehören grundsätzlich nicht in ein Repository. Da noch nichts hochgeladen war, wurde die lokale Geschichte
bereinigt (`git filter-branch`). Vorher wurde die vollständige alte Geschichte als `backups/git-vor-bereinigung_2026-10-15.bundle`
gesichert. Folge: Die Commit-Kennungen ab Stufe 1 haben sich geändert; die Tags zeigen auf die bereinigten Stände.
Inhaltlich (Code, Tests, Doku) ist nichts verändert.

## D-003 · 2026-10-15 · Feste Werkzeug-Versionen
`requirements-dev.txt` legt pytest, ruff, mypy und psycopg fest; lokal, im Container und in GitHub Actions gleich.
Prüfsummen und Sicherheitsprüfung der Abhängigkeiten folgen in AP-05.

## D-004 · 2026-10-15 · Empfohlene GitHub-Anbindung: Schlüssel nur für dieses eine Repository
Für das Hochladen vom Mac wird ein eigener SSH-Schlüssel erzeugt, der auf GitHub als „Deploy Key“ mit Schreibrecht **nur für
`ai-trading-system`** hinterlegt wird. Er kann keine anderen Repositories und kein Konto verwalten. Alternative mit mehr
Rechten wäre das GitHub-Programm `gh` mit Kontoanmeldung. Der Schlüssel liegt in `~/.ssh` (außerhalb des Projekts), nie im Repository.

## D-005 · 2026-10-15 · Vorläufig: eigener Kern bleibt, fremde Bausteine nur an den Rändern — ersetzt durch D-006
Der eigene Kern (Risikorechnung, Executor, Datenbank-Gurte, Protokoll) ist auf die Sicherheitsregeln dieses Projekts
zugeschnitten und durch 606 Tests und 193 Gegenproben abgesichert. Ein Wechsel auf ein großes Framework würde diese Absicherung
neu verlangen. Vorläufige Linie: Börsenzugriff über eine bewährte Bibliothek (Kandidat CCXT), Ideen aus Freqtrade/Nautilus/
Hummingbot übernehmen, aber keinen fremden Code unkontrolliert kopieren. Endgültige Entscheidung mit Lizenzprüfung in AP-04.

## D-006 · 2026-10-15 · Eigener Kern + CCXT für den Börsenzugriff (AP-04)
Untersucht: CCXT (MIT), Freqtrade (GPL-3.0), NautilusTrader (LGPL-3.0), Hummingbot (Apache-2.0). Details und Quellen:
`docs/FRAMEWORK_ANALYSIS.md`. Ergebnis: Der abgesicherte eigene Kern bleibt. Marktdaten (historisch und per WebSocket) und
später die Demo-Anbindung laufen über **CCXT**, gekapselt im Paket `trading.adapters` hinter unseren Schnittstellen.
Freqtrade wird wegen der GPL nur als Ideengeber genutzt (kein Code); NautilusTrader fehlt Bitget und es verlangt Python 3.13+;
Hummingbot ist als ganze Anwendung gebaut. **Folgen:** CCXT wird optionale Abhängigkeit (Extra `exchange`), vorher
Sicherheitsprüfung (AP-05); `tests/test_no_real_trading.py` erlaubt Netzwerkzugriff dann ausschließlich in `trading.adapters`.

## D-007 · 2026-10-15 · GitHub Actions nur für `main`, `develop`, Pull Requests und `stable/*`-Tags
Spart das kostenlose Kontingent privater Repositories (2 000 Minuten/Monat beim kostenlosen Konto; ein Lauf dauert rund
5 Minuten). Arbeitszweige werden lokal mit `scripts/preflight.py` geprüft. Ohne hinterlegte Zahlungsmethode entstehen keine Kosten;
ist das Kontingent aufgebraucht, laufen die Prüfungen auf GitHub bis zum Monatsende nicht mehr (lokal weiterhin).

## D-008 · 2026-10-15 · Entwicklung in der Cloud, lokale Kopie nur als Sicherung (Entscheidung des Eigners)
Maßgeblich ist das private GitHub-Repository `digibroberlin/ai-trading-system`. Weiterentwickelt wird in der Cloud
(stündliche Cloud-Routine, Claude Code im Web). Der lokale Ordner auf dem Mac bleibt nach dem Umzug als Sicherung unverändert
liegen und wird nicht mehr bearbeitet; die lokale Docker-Umgebung (Colima) wird angehalten, nichts wird gelöscht.
Vom Mac aus wird nur einmalig hochgeladen, über einen Schlüssel, der ausschließlich für dieses Repository gilt (D-004).

## D-009 · 2026-10-15 · Stündliche Cloud-Routine mit Opus 5.5 (Entscheidung des Eigners)
Einmalige Zustimmung des Eigners am 2026-10-15. Modell Opus 5.5 (sicherheitskritische, architekturlastige Arbeit).
Läufe, die wegen des Nutzungslimits nicht starten, werden nicht nachgeholt oder erzwungen; der nächste Lauf setzt fort.
Keine kostenpflichtige Mehrnutzung. Die Routine hält sich an CLAUDE.md (gleiche Regeln wie lokale Sitzungen).


## D-010 · 2026-10-15 · Abhängigkeiten nur aus Sperrdateien mit Prüfsummen; pytest 9.0.3 (AP-05)
**Anlass:** Audit M12 (Pakete nicht festgeschrieben) und Prüfung mit `pip-audit` am 2026-10-15: `pytest 8.4.2` hat eine bekannte
Lücke (CVE-2025-71176 / PYSEC-2026-1845, behoben in 9.0.3; ein *anderer lokaler Benutzer* kann das Temp-Verzeichnis von pytest stören).
**Entscheidung:**
* Alle Abhängigkeiten liegen in `requirements/`: `*.txt` = Vorgabe (feste Versionen), `*.lock` = vollständige Liste inklusive
  aller indirekten Pakete mit SHA-256-Prüfsummen (erzeugt mit `requirements/update_locks.sh`). Installiert wird überall –
  Dockerfile, GitHub Actions, Cloud-Sitzung – nur mit `pip install --require-hashes -r requirements/<name>.lock`.
  Ein Test (`tests/test_dependency_locks.py`) prüft das.
* Das Docker-Image installiert das Projekt nicht mehr per `pip install .` (das hätte `setuptools` ungeprüft nachgeladen),
  sondern findet den Code über `PYTHONPATH=/app/src`.
* `pytest` wird auf **9.0.3** angehoben. Ausnahme: Der GitHub-Job „Kern ohne Datenbank (Python 3.9)“ bleibt bei 8.4.2, weil
  pytest 9 Python 3.9 nicht mehr unterstützt. Er läuft nur auf einer frischen Wegwerf-Maschine ohne weitere Benutzer; die Lücke ist dort
  nicht ausnutzbar. Diese eine Ausnahme steht mit Begründung in `scripts/dependency_audit.py` und gilt nur für `py39.lock`.
* `scripts/dependency_audit.py` prüft **jede** Paketversion aus allen Sperrdateien auf bekannte Lücken – auch Einträge, die
  nur für Python 3.9 oder Windows gelten (pip-audit allein hätte sie unter 3.12 still übersprungen). Kann es nicht vollständig
  prüfen (kein Netz, pip-audit fehlt), meldet es Code 2 „nicht vollständig“ statt eines Ergebnisses. Es braucht Netzwerk und
  läuft deshalb in GitHub Actions (eigener Job), nicht in `preflight.py`.
* **CCXT (Vorabprüfung für AP-14, D-006):** Version 4.5.88 zieht 24 Pakete nach (u. a. aiohttp, cryptography, requests, coincurve);
  `pip-audit` meldete am 2026-10-15 keine bekannte Lücke. CCXT wird erst mit AP-14 als Extra `exchange` aufgenommen – dann mit eigener
  Sperrdatei und erneuter Prüfung.
**Grenzen:** Das Basis-Image `python:3.12-slim` ist noch nicht per Digest festgeschrieben, und die Container haben weiter freien
Internetzugang (beides Rest von M12, gehört zu AP-27). Das geänderte Dockerfile konnte in der Cloud nicht gebaut werden
(Docker Hub: „429 Too Many Requests“); geprüft wurden dieselben Installationsschritte in frischen Python-Umgebungen.

## D-011 · 2026-10-15 · Globaler Not-Aus; Notbremsen halten bis zur bewussten Freigabe (AP-07)
**Entscheidung:**
* Ein globaler Not-Aus (eine Zeile `trading_halt` für alle Konten und Prozesse) sperrt neue Positionen. Schließen
  (Stop-Loss, Take-Profit, Liquidation, Ausstiegssignal) und Funding laufen weiter – Risiko abbauen muss immer gehen.
* Automatische Auslöser: formal fehlerhafte Marktdaten (NaN, nicht-positive Kurse, kaputte Kerzen, naive oder zukünftige
  Zeitstempel, fehlerhafte Funding-Ereignisse), Fehler beim Schreiben oder Prüfen des Ereignisprotokolls, die Notbremsen
  Tagesverlust (3 %) und Drawdown (10 %). **Nicht** ausgelöst wird durch doppelte/ältere Kerzen (normale Wiederholung), zu weite
  Spreads, Kurssprünge oder veraltete Kurse – sie blockieren wie bisher nur die einzelne Order.
* **Strenger als vorher:** Die Tagesverlust-Bremse löste sich bisher um Mitternacht von selbst. Jetzt bleibt der Handel gestoppt,
  bis ein Mensch die Ursache geprüft und den Not-Aus aufgehoben hat (Audit M14: „Bremse ohne Freigabe-Verfahren“).
* Aufheben nur mit Namen und dem Text `HANDEL FREIGEBEN`, in der Datenbank nur mit einer Administrator-Verbindung
  (Trigger verweigert es der Anwendungsrolle). Der erste Grund bleibt stehen, bis aufgehoben wird. Alles im Ereignisprotokoll.
* Fail closed: Ist der Zustand nicht lesbar oder fehlt die Zeile, gilt der Handel als gestoppt (Programm und Datenbank).
* Jede Aktion sperrt nach der Konto-Zeile auch die Not-Aus-Zeile (`FOR UPDATE`). Folge: Aktionen **verschiedener** Konten laufen
  nacheinander. Bewusst gewählt: Sonst könnte ein Prozess zwischen „Not-Aus gelesen“ und „Position gespeichert“ überholt werden.
* **Freigabe setzt die Bremsen neu an:** Wer freigibt, nimmt den bisherigen Verlust bewusst hin. Tagesbeginn und Höchststand
  werden auf die aktuelle Equity gesetzt (in der Datenbank für alle Konten, in derselben Transaktion wie die Freigabe).
  Ohne das löste dieselbe Bremse sofort wieder aus, und nach einem Drawdown wäre eine Freigabe nie möglich.
* **Ergänzt nach der unabhängigen Prüfung (2026-10-15):** (a) Beim Tageswechsel zählt ein Verlust seit der letzten
  Bewertung noch zum alten Tag (vorher fiel z. B. der Verlust der Kerze, die um Mitternacht schließt, aus der Tagesbremse);
  (b) die Notbremsen in der Risikoprüfung gelten wie alle Grenzen am Zustand **nach** der Order (Kosten der Order zählen);
  (c) ein Executor hält den (geteilten) Not-Aus für die Dauer seiner Operation fest; (d) scheitert das Auslösen des
  gemeinsamen Not-Aus, stoppt wenigstens der Executor selbst – ein Stop-Loss wird dadurch nie abgebrochen;
  (e) die Datenbank beendet eine in einer Transaktion hängende Sitzung nach 15 s (sonst blockierte sie den Not-Aus selbst),
  das Auslösen von außen wird bei belegter Sperre wiederholt; (f) eine Freigabe braucht in der Datenbank einen neuen
  Zeitpunkt (sie erbt nicht Name/Zeit einer früheren Freigabe); (g) die Kommandozeile liefert eindeutige Rückgabecodes.
**Folgen für Backtests:** Der Backtest nutzt denselben Executor; trifft eine Strategie die Notbremse, endet der Handel in diesem
Backtest (das bildet den echten Betrieb ab). Die bestehenden Backtest-Ergebnisse haben sich dadurch nicht geändert.

## D-012 · 2026-10-16 · Wiederholte Order liefert das ursprüngliche Ergebnis (AP-08, Audit M7)
**Problem:** Schickt ein Absender dieselbe Order erneut (z. B. weil nach einem Zeitlimit oder Neustart unklar ist, ob sie
ankam), bekam er bisher „abgelehnt: Duplikat“ – obwohl die erste Order vielleicht ausgeführt wurde. Er konnte daraus nicht
erkennen, ob eine Position besteht.
**Entscheidung:**
* Jede Order-ID wird weiterhin **höchstens einmal** bearbeitet. Eine Wiederholung mit **genau gleichem Inhalt** (Fingerabdruck
  SHA-256 über ID, Setup, Menge und Hebel) bekommt das ursprüngliche Ergebnis: Status, Gründe und die Position so, wie sie
  eröffnet wurde. Gekennzeichnet mit `replayed=True`; protokolliert als `ORDER_REPLAYED`. Es wird dabei **nichts** gehandelt.
* Gleiche ID mit **anderem** Inhalt: wie bisher „abgelehnt: Duplikat“ (`DUPLICATE_ORDER_ID`) – ein fremdes Ergebnis wird nie
  zurückgegeben. Ebenso, wenn sich das ursprüngliche Ergebnis nicht belegen lässt (Einträge vor Migration 004).
* Auch eine ursprünglich **abgelehnte** Order bekommt bei Wiederholung ihre ursprüngliche Ablehnung (ein neuer Versuch
  braucht eine neue ID). Eine Wiederholung wird auch bei ausgelöstem Not-Aus beantwortet – sie eröffnet ja nichts.
* Die Risikobewertung (`risk`) wird bei der Wiederholung nicht mitgeliefert (sie steht im Protokoll der ersten Bearbeitung);
  so liefern Speicher- und Datenbank-Executor exakt dasselbe.
* Datenbank: Migration 004 ergänzt `orders` um `fingerprint` (festes Format) und `position_id` (Fremdschlüssel auf
  `positions`; bei „filled“ Pflicht, sonst verboten). Die Tabelle bleibt unveränderlich.
* Pipeline: Eine Wiederholung zählt im Durchlauf als **nicht ausgeführt** (Stufe EXECUTOR, Grund `DUPLICATE_ORDER_ID`).

## D-013 · 2026-10-16 · SORA rechnet die Größe unter der Konto-Sperre (AP-09, Audit M8)
**Problem:** SORA las den Portfolio-Zustand **vor** der Ausführung. Hatte ein anderer Prozess (oder eine andere Pipeline auf
demselben Executor) in der Zwischenzeit Risiko belegt, lehnte der Risikomanager SORAs Vorschlag ab, obwohl eine kleinere
Position noch gepasst hätte (sicher, aber nicht optimal; Audit: „2 von 8 Pipelines führten aus“).
**Entscheidung:**
* Neue Executor-Methode `submit_sized(order_id, snapshot, sizer)`: Der Executor ruft den Größen-Rechner **unter seiner Sperre**
  auf – beim PostgreSQL-Executor innerhalb der Transaktion mit gesperrter Konto-Zeile – und gibt ihm genau den Zustand, gegen den
  der Risikomanager danach prüft. Liefert der Rechner nichts, wird nichts gehandelt und keine Order-ID verbraucht.
* Die Pipeline führt Einstiege nur noch so aus. SORA bekommt dabei weiterhin **keinen** Executor, keine Limits und keinen
  Risikomanager; die Pipeline reicht nur eine Funktion „Zustand → Auftrag“ weiter. Der Risikomanager hat weiter das letzte Wort.
* Ein Auftrag mit fremder Order-ID oder ein Fehler im Rechner bricht die ganze Operation ab (alles zurückgesetzt, fail closed).
  Ein SORA-Absturz wird als Blockade auf Stufe SORA (`AGENT_ERROR`) gemeldet.
* AKI protokolliert SORAs Vorschlag weiterhin **vor** dem Handel. Beim PostgreSQL-Protokoll geschieht das über eine eigene
  Verbindung, während die Konto-Sperre gehalten wird. Das verklemmt nicht, weil die Protokoll-Kette erst danach (beim Speichern)
  gesperrt wird und kein anderer Ablauf die Kette hält, während er auf Konto- oder Not-Aus-Zeile wartet. Scheitert die
  Ausführung danach, bleibt der Vorschlag im Protokoll stehen – wie bisher.
* `submit` bleibt für direkte Aufträge (Tests, Backtest-Werkzeuge) erhalten.
**Folge:** SORA muss schnell und deterministisch bleiben (keine KI-Aufrufe, kein Netz), weil die Konto-Sperre während der
Rechnung gehalten wird. Wird SORA später durch KI beraten, geschieht das **vor** dem Sperr-Abschnitt; unter der Sperre
läuft nur die Regelrechnung.

## D-014 · 2026-10-16 · Konten nur administrativ; Kontostand mit Plausibilitätsprüfung (AP-10, Audit M2 Rest)
**Problem:** Die Anwendungsrolle `trading_app` durfte Konten anlegen. Jedes Konto hat ein eigenes 2-%-Gesamtrisiko – ein
fehlerhafter oder manipulierter Prozess hätte sein Budget durch weitere Konten vervielfachen können. Außerdem durfte sie die
Kontostand-Spalten beliebig überschreiben (z. B. den Höchststand senken und damit die Drawdown-Bremse aushebeln).
**Entscheidung:**
* Migration 005 entzieht `trading_app` das Recht `INSERT` auf `accounts`. Konten legt nur ein Administrator an:
  `python -m trading.persistence.accounts create KONTO STARTKAPITAL` (braucht `ADMIN_DATABASE_URL`). Die Anlage steht in derselben
  Transaktion im Ereignisprotokoll (`ACCOUNT_CREATED`). Ein bestehendes Konto wird nie verändert, auch nicht bei anderem Startkapital.
* `PostgresPaperExecutor` legt kein Konto mehr an und hat deshalb keinen Parameter „Startkapital“ mehr. Fehlt das Konto, startet
  er nicht (`AccountMissingError`, fail closed).
* Den Kontostand muss die Anwendung weiter schreiben. Ein Trigger prüft jede Änderung durch eine Rolle ohne Administratorrechte:
  genau ein Versionsschritt, Positionszähler und Handelstag nur vorwärts, Tagesstart-Equity innerhalb eines Tages fest,
  Höchststand fällt nie und liegt nie unter der Equity. Eine Änderung, die nichts ändert (Sperr-Trick des Risiko-Gurts), bleibt
  erlaubt. Administratoren sind ausgenommen, weil die Freigabe des Not-Aus die Bezugswerte bewusst neu setzt (D-011).
* Die Prüfung hängt nicht am Rollennamen: Auch jede andere Rolle ohne Administratorrechte unterliegt ihr.
**Bewusst nicht umgesetzt:** ein gemeinsames Risikolimit über alle Konten. Da nur noch der Administrator Konten anlegt, ist die
Zahl der Konten eine bewusste Entscheidung des Eigners. Die Beträge selbst (Guthaben, Equity) prüft die Datenbank nicht nach –
sie stammen aus der einen Risikorechnung der Anwendung; jede Änderung steht im Protokoll.

## D-015 · 2026-10-16 · Wiederverbindung zur Datenbank (AP-11, Audit H7)
**Problem:** Riss die Verbindung zur Datenbank ab (Datenbank-Neustart, Netzwerk, vom Server beendete Sitzung), scheiterte jeder
weitere Aufruf von Executor, Protokoll und Not-Aus, bis der Prozess neu gestartet wurde.
**Entscheidung:**
* Eine gemeinsame Verbindungsverwaltung (`persistence/db.py`, `ConnectionKeeper`) prüft vor jeder Transaktion, ob die
  Verbindung noch gilt, und baut sie sonst neu auf: bis zu 4 Versuche mit 0,5 / 1 / 2 s Wartezeit, je Versuch höchstens 5 s
  Verbindungsaufbau. Danach `DatabaseUnavailableError` – nie endloses Warten, nie Handeln ohne Datenbank. Beim Start wird nicht
  gewartet (falsche Adresse oder falsches Passwort sofort melden). Eine bewusst geschlossene Verbindung verbindet sich nie neu.
* **Aktionen** (Order, Schließen, Kerze, Funding, Portfolio): Reißt die Verbindung ab, **bevor** die Engine entschieden hat
  (beim Sperren oder Laden), ist nichts geschehen → genau eine Wiederholung mit neuer Verbindung. Reißt sie **danach** ab
  (auch beim COMMIT), ist der Ausgang ungewiss → keine stille Wiederholung, der Fehler geht an den Aufrufer. Dieselbe Order-ID
  darf er gefahrlos erneut senden: Sie liefert das gespeicherte Ergebnis oder wird jetzt bearbeitet, nie doppelt (D-012).
* **Reine Abfragen** (Kontostand, Positionen, Trades, Not-Aus-Zustand) werden nach einem Abbruch einmal wiederholt – Lesen
  ändert nichts. Ist der Not-Aus-Zustand trotzdem nicht lesbar, gilt der Handel weiter als gestoppt.
* **Not-Aus von außen** wiederholt auch nach einer abgerissenen Verbindung (doppelt auslösen schadet nie). Ein
  **Protokolleintrag** (`PostgresEventLog.append`) wird nicht still wiederholt (er könnte schon gespeichert sein); der nächste
  Aufruf verbindet neu.
* Fehler bei **intakter** Verbindung (z. B. Sperr-Zeitlimit) werden nie automatisch wiederholt.
* Hängende Transaktionen begrenzt seit AP-07 der Server (`idle_in_transaction_session_timeout` 15 s, TCP-Keepalive).
**Bewusst nicht umgesetzt:** Überwachung/Alarm bei Verbindungsproblemen (kommt mit Logging und Gesundheits-Endpunkten, AP-25);
ein Verbindungspool (ein Prozess braucht nur eine Verbindung). Der Zähler `reconnects` ist dafür vorbereitet.

## D-016 · 2026-10-16 · Kostenoptimierung und Nettogewinn nach allen Kosten (Vorgabe des Eigners)
**Vorgabe (Eigner, 2026-10-15):** Oberstes wirtschaftliches Ziel ist der langfristige risikobereinigte Nettogewinn nach
sämtlichen Kosten – Trades, Börsengebühren, Spread/Slippage, Funding, KI-API, Server, Marktdaten, Sonstiges. Die Agenten
sollen nicht möglichst viele Tokens oder Trades erzeugen, sondern kostenpflichtige Analysen nur bei ausreichendem,
**empirisch belegtem** Nutzen durchführen; vergleichbar gute günstigere Methoden haben Vorrang.
**Architektur (fest eingebaut):**
* **Jede KI-Analyse läuft durch `trading.ai.orchestrator.AIOrchestrator`** – nirgends sonst. Reihenfolge: deterministische
  Regeln zuerst (0 USD) → Cache/Doppel-Erkennung (0 USD) → ereignisbasierte Aktivierung mit Abkühlzeit → Modellstufen
  billig → mittel → teuer, eine teurere Stufe nur bei unsicherer Antwort **und** wenn die Kosten-Obergrenze durch den
  erwarteten Nutzen gedeckt ist (Wertschranke, Vorgabe 10 %) → hartes Budget.
* **Harte Budgets** je Aufruf, Tag und Monat (USD). Vor jedem Aufruf wird eine echte Kosten-**Obergrenze** reserviert
  (Eingabe-Tokens ≤ Bytes + Zuschlag, Ausgabe ≤ `max_tokens`); passt sie nicht, kein Aufruf. Ein gescheiterter Aufruf
  zählt mit der vollen Reservierung. Lagen tatsächliche Kosten über der Reservierung, sperrt sich das Budget (fail closed).
  **Standard 0 USD** – ohne Freigabe des Eigners (B-004) gibt es keinen bezahlten Aufruf. Im Betrieb liegt das Budget
  zusätzlich in der Datenbank (Migration 006): Grenzen setzt nur ein Administrator mit Namen der Freigabe, die Datenbank
  selbst verweigert jede Überschreitung, auch über Prozesse und Neustarts hinweg.
* **Budget-Konfiguration:** `AI_BUDGET_PER_CALL_USD`, `AI_BUDGET_DAILY_USD`, `AI_BUDGET_MONTHLY_USD` (fehlt ein Wert: 0;
  ungültig: Abbruch); Datenbank-Grenzen: `persistence.ai_budget.set_limits_as_admin` (Name der Freigabe Pflicht).
* **Messung:** Jeder Schritt (auch Regel-Entscheidungen, Cache-Treffer, übersprungene Aufrufe mit Grund) wird mit Agent,
  Anlass, Setup, Modell, Input-/Output-/Cache-Tokens und Kosten an AKI gemeldet.
* **AKI** wertet aus: Aufrufe/Tokens/Kosten je Agent, Kosten je Modell, je Analyse, je Signal, je Tag/Monat; KI-Kosten je
  Trade direkt zugeordnet **und** voll umgelegt (mehrere Analysen je Trade, Analysen ohne Trade zählen mit); Betriebskosten
  (Server, Marktdaten, Sonstiges, zeitanteilig); **Nettoergebnis nach allen Kosten**.
* **Arbeitspakete:** AP-31 (Kosten/Netto in AKI), AP-32 (Orchestrierung, Budgets), AP-33 (Wirtschaftlichkeitsnachweis), Phase C2
  im Master-Plan; angepasst: AP-19, AP-20, AP-21, AP-22, AP-24, AP-28.
* **Wirtschaftlichkeitsnachweis:** gepaarter Vergleich zweier Methoden auf denselben Setups nach Analysekosten mit
  Bootstrap-Vertrauensbereich; die teurere Methode gewinnt nur, wenn die untere Grenze über 0 liegt – sonst die günstigere.
* **Sicherheit unberührt:** Risikomanager, RIN, SORA, Executor, Not-Aus und Datenprüfung kennen weder KI-Orchestrierung
  noch Budget (Test prüft die Importe). Ist das Budget erschöpft, entscheiden die Agenten regelbasiert weiter.
**Preise:** datierte Tabelle (Stand 2026-10-06, Anthropic-Erstanbieter-API): Haiku 5.5 0,10/0,50, Sonnet 5.5 2/10, Opus 5.5
4/20 USD je Million Input/Output-Tokens; Cache-Lesen ~0,1×, Cache-Schreiben 1,25× Input (Haiku-Cache-Lesepreis daraus
abgeleitet, nicht aus der Liste). Vor echtem Einsatz gegen die aktuelle Preisliste prüfen.
**Nachgeschärft nach unabhängiger Prüfung (25 bestätigte Befunde, alle behoben):**
* Die Wertschranke gilt für die **ganze Analyse** (alle Stufen zusammen höchstens 10 % des erwarteten Nutzens), nicht je Stufe.
* Meldet der Anbieter keinen Verbrauch oder ein anderes Modell als angefragt, wird die **volle Reservierung** gebucht und das
  Budget gesperrt; ebenso, wenn Eingabe- oder Ausgabe-Tokens die Annahmen der Obergrenze verletzen (auch wenn die Kosten
  zufällig darunter bleiben). Ein Anbieterfehler zählt als bezahlter Aufruf mit voller Reservierung.
* Eine KI-Antwort ersetzt eine Regel-Entscheidung nur, wenn sie **sicherer** ist als die Regeln; sonst gilt die Regel.
* Die Sperre des Orchestrators wird während des Netzwerkaufrufs nicht gehalten; gleiche gleichzeitige Analysen warten auf
  die erste (in-flight-Erkennung, höchstens 120 s). Der Cache ist begrenzt (10 000 Einträge, abgelaufene werden entfernt).
* Scheitern AKI-Protokoll oder Budget-Abrechnung nach einem bezahlten Aufruf, sperrt sich das Budget; der Aufruf geht nie
  verloren.
* KI-Kosten je Trade werden über Zeitraumgrenzen richtig zugeordnet (Analysen vor dem Zeitraum für Trades im Zeitraum).
* **Datenbank-Budget:** Tag und Zeitpunkt jeder Buchung setzt die **Datenbank-Uhr** (UTC) – eine falsch gehende
  Anwendungsuhr kann keinen „neuen Tag“ vortäuschen. Neue Grenzen brauchen Namen **und neuen Zeitpunkt** der Freigabe,
  Entsperren ebenso (`unblocked_by`/`unblocked_at`); eine frühere Freigabe gilt nicht weiter.
* Geheimnis-Scan: Nur feste Zähler-Namen (`input_tokens`, `output_tokens`, `max_tokens`, `tokens_in` …) sind ausgenommen,
  nicht jeder Name mit „tokens“.
**Zweite unabhängige Prüfung (der Korrekturen selbst):**
* **Schwer, durch die Korrekturen entstanden:** Seit der Orchestrator den Anbieteraufruf ohne Sperre macht, rufen mehrere
  Threads das PostgreSQL-Budget gleichzeitig über **eine** Verbindung auf – die zweite Transaktion wurde nur ein Savepoint
  der ersten, Reservierungen und Sperren gingen verloren. Jetzt: Sperre im `PostgresBudgetLedger` um jede Transaktion, und
  in einer fremden offenen Transaktion wird nie gebucht (dann gilt das Budget als gesperrt).
* Der Datenbank-Gurt serialisiert Reservierungen **schreibend** (Zähler `reservations` in der Grenzen-Zeile) statt mit einer
  reinen Lesesperre: Unter REPEATABLE READ zählte eine wartende Reservierung sonst einen veralteten Stand; jetzt bricht sie
  ab. Test über alle drei Isolationsstufen.
* Auch ein Cache-Treffer (oder das Warten auf eine parallel laufende gleiche Analyse) ersetzt nie sicherere Regeln.
* AKI merkt sich einen KI-Messpunkt vor dem Protokollieren – scheitert das Protokoll, fehlen die Kosten nicht im Nettobericht.
* Weitere Tests: gelöschter Freigabe-/Entsperr-Zeitpunkt, Abrechnungszeit von der Datenbank, Trigger der Anwendungsrolle
  auch bei breiteren Spaltenrechten.
**Grenzen:** KIBO/YUZU sind noch neutrale Platzhalter – die Orchestrierung ist die Infrastruktur, an die sie mit echter
Regel-Logik (AP-17) und später mit KI (AP-22, nach Budgetfreigabe) angeschlossen werden. Der Nutzen jeder KI-Analyse ist
bis zum Nachweis (AP-33 mit echten Daten) unbewiesen.

## D-017 · 2026-10-16 · Pipeline-Reihenfolge nach Vorgabe des Eigners (AP-12)
**Problem:** Die Pipeline fragte RIN → KIBO → YUZU → SORA. Der Eigner gibt MOMO → RIN → SORA → KIBO/YUZU → Executor → AKI vor:
Was regelbasiert schon scheitert (Daten, Budget, Stop-Abstand), soll die späteren, ggf. teuren Bewertungen (KI) gar nicht erst
erreichen. Gleichzeitig muss SORA die Größe **unter der Sperre** des Executors rechnen (D-013, Audit M8) – sonst könnten zwei
Prozesse dasselbe Restbudget verplanen.
**Entscheidung:**
* SORA wird **zweimal** gefragt: einmal als **Vorprüfung** direkt nach RIN (mit einem vorab gelesenen Portfolio-Zustand,
  Protokoll `SIZING_PRECHECK`) und einmal **endgültig** unter der Sperre (unverändert, Protokoll `SIZING_PROPOSED`).
* Die Vorprüfung ist nur ein Filter: Lehnt sie ab oder stürzt SORA ab, wird blockiert (Stufe `SORA`), KIBO und YUZU werden nicht
  gefragt. Stimmt sie zu, ist das **keine** Größe und **keine** Erlaubnis – die Order bekommt die Größe von unter der Sperre;
  lehnt SORA dort ab, wird nicht gehandelt.
* Ist der Portfolio-Zustand für die Vorprüfung nicht lesbar, wird blockiert (Stufe `EXECUTOR`, `EXECUTION_ERROR`; fail closed).
* KIBO und YUZU behalten ihre Schnittstelle (`evaluate(setup, ctx)`); sie sehen die vorgeschlagene Größe noch nicht.
**Bewusst nicht umgesetzt:** Weitergabe der SORA-Größe an KIBO/YUZU (Schnittstellenänderung erst, wenn eine echte Bewertung sie
braucht). Der Risikomanager im Executor bleibt unverändert die letzte Instanz.

## D-018 · 2026-10-16 · Strategie-Bibliothek (AP-16)
**Entscheidung:** Sechs regelbasierte Strategien in `trading.strategies` auf einem gemeinsamen Rahmen (`RuleStrategy`):
je Instrument in fester Reihenfolge, mit offener Position nur Ausstiegsentscheidung, Short nur bei Futures (und nur mit
`allow_short`), zu kurze Historie = kein Signal. Stop-Abstand = max(stop_atr × ATR, min_stop_pct × Kurs), Ziel =
reward_risk × Stop-Abstand (Standard 2,5, damit RIN nach Kosten ≥ 1,5 sieht) oder ein fachliches Ziel (Mean Reversion:
Bollinger-Mitte). Stop und Ziel werden auf 8 Nachkommastellen **zu Ungunsten** gerundet (Stop weiter weg, Ziel näher).
**Warum so:** Strategien bleiben reine Vorschlaggeber – sie sehen nur `MarketView` (abgeschlossene Kerzen, eigene
Positionen) und können Sicherheitslimits weder sehen noch ändern. Indikatoren liefern bei zu wenig Daten `None` statt
eines geschätzten Werts.
**Vergleichsmaßstäbe:** Nicht-Handeln läuft durch dieselbe Engine; Kaufen-und-Halten wird analytisch gerechnet (gleiche
Gewichte, Kauf zum ersten Eröffnungskurs mit Spread/Slippage/Gebühr, Funding abgezogen, Verkauf zum letzten Schluss),
weil ein Dauer-Long ohne Stop mit den Sicherheitsregeln nicht handelbar wäre. Kennzahlen aus derselben Funktion wie
Backtests.
**Geprüft:** Indikatoren gegen Handrechnungen; Signale je Strategie an gebauten Kursverläufen; für alle Strategien über
lange synthetische Läufe: Stop/Ziel auf der richtigen Seite, kein Short auf Spot, kein zweiter Einstieg; volle Pipeline:
alles vor einem Schnitt ist identisch, ob danach Daten folgen oder nicht (kein Blick in die Zukunft), zwei Läufe bitgleich.
**Grenzen:** Nicht optimiert, nicht als profitabel belegt (AP-18/20); Preis-/Mengenschritte je Börse folgen mit AP-13.

## D-019 · 2026-10-16 · Marktphasen und regelbasierte KIBO/YUZU (AP-17)
**Entscheidung:** `trading.analysis` (Indikatoren, Marktphasen) ist ein eigenes Paket ohne Handelslogik – Strategien und
Agenten nutzen es, ohne voneinander abzuhängen. Marktphase: Trend nur bei gerichteter Bewegung (Kaufman-Effizienz ≥ 0,3)
UND passender Steigung des EMA(50) UND Kurs auf dessen richtiger Seite, sonst seitwärts; Volatilität als mittlerer Rang
der ATR-% gegenüber den letzten 100 Kerzen (≤ 0,2 ruhig, ≥ 0,8 unruhig).
**KIBO** (Technik) lehnt hart ab: Stop im Rauschen (< 0,5 × ATR), Setup gegen einen klaren Trend (abschaltbar). **YUZU**
(Umfeld) lehnt hart ab: ATR > 5 % je Kerze, Funding > 0,2 % je Abrechnung gegen die Position, Spread > 20 % des
Stop-Abstands, Volumen < 30 % des Durchschnitts. Sonst Bewertung ab 0,5 mit nachvollziehbaren Zu- und Abschlägen;
Zustimmung ab 0,35. Zu wenig Kerzen: kein Urteil (Zustimmung 0,5 mit Hinweis) – KIBO/YUZU sind Bewerter, keine
Sicherheitsinstanz; die Sicherheit hängt unverändert an RIN, SORA, Risikomanager und Executor.
**KI nur als Zweitmeinung** über den Kosten-Orchestrator (D-016): nur ohne harte Ablehnung, nur bei unsicherem
Regel-Ergebnis (Sicherheit 0,5 + Abstand zur Schwelle), nur mit konfiguriertem Nutzen (Standard 0 = nie bezahlt) und
Budget; eine Antwort zählt nur mit eindeutigem `approve` und höherer Sicherheit als die Regeln.
**Backtest-Standard:** KIBO und YUZU regelbasiert; neutrale Platzhalter nur noch ausdrücklich (im Ergebnis ausgewiesen).
**Grenzen:** Schwellen aus Fachliteratur und Plausibilität, nicht optimiert; Nutzen erst mit AP-18/20 belegbar.

## D-020 · 2026-10-16 · Dashboard nur lesend (AP-23/24)
**Entscheidung:** FastAPI **0.141.1** mit uvicorn 0.54.0 und wsproto 1.3.2 (WebSocket), Starlette 1.7.0, Pydantic 2.13.5 –
alle mit Prüfsummen in `requirements/*.lock`, Lückenprüfung ohne Befund. Bewusst nicht die neueste FastAPI (ab 0.142
kommt OpenTelemetry als Pflicht-Abhängigkeit) und nicht die erst wenige Tage alten Starlette/Pydantic-Versionen. httpx
nur für Tests. FastAPI braucht Python ≥ 3.10: Auf 3.9 (Kern-Prüfung) werden die Dashboard-Tests sichtbar übersprungen.
**Sicherheit:** nur GET-Endpunkte und ein WebSocket, der nur sendet (alles andere 405); Modus LIVE lässt sich gar nicht
starten; feste Liste ausgelieferter Dateien; Content-Security-Policy ohne fremde Quellen, kein Einbetten, kein Cache;
keine Anmeldung, daher Standard 127.0.0.1. Strukturtests: Webserver-Bibliotheken nur im Paket `dashboard`; der
Dashboard-Code ruft keine ändernden Methoden (Order, Schließen, Not-Aus, Budget, Protokoll) auf.
**Anzeige:** Momentaufnahme mit Prüfsumme (`version`); der WebSocket sendet nur bei Änderung. Zahlen als Text
(Decimal-genau). Kapitalkurve für die Anzeige auf ≤ 500 Punkte ausgedünnt, tiefster Punkt bleibt immer erhalten; der
Drawdown wird an allen Punkten gemessen. Paper-Modus: aktueller Kontostand als letzter Punkt (nur für die Anzeige);
widersprüchliche Buchhaltung wird angezeigt statt falscher Zahlen.
**Abweichung vom Plan:** keine. **Grenzen:** keine Anmeldung (für Server: AP-28); DEMO wird erst mit AP-21 live gespeist.

## D-021 · 2026-10-16 · Ereignisgesteuerte Laufzeit-Engine (AP-21)
**Entscheidung:** `RuntimeEngine.process(ereignis)` statt einer Dauerschleife: Jede geschlossene Kerze durchläuft
Executor (Stops/Ziele/Bewertung, eigener Datenschutz) → Marktphase (Wechsel = Ereignis `REGIME_CHANGE`, KI dafür nur
über den Orchestrator mit Auslöser REGIME_CHANGE) → Strategie für **genau dieses Instrument** → Ausstiege, dann Einstiege
durch die volle Pipeline (RIN → SORA-Vorprüfung → KIBO → YUZU → SORA unter der Sperre → Executor) → AKI (neu geschlossene
Trades, Kapitalkurve nach allen Folgen des Ereignisses; gleichzeitige Kerzen ergeben einen Punkt). Funding → Executor.
**Kennungen** = Strategie|Instrument|Richtung|Entscheidungszeit: reproduzierbar, nach Neustart idempotent (AP-08).
**Neustart:** Executor und Protokoll liegen im Betrieb in PostgreSQL; die Engine füllt nur ihre Historie auf
(`warm_up`, entscheidet und protokolliert dabei nichts) und macht genau dort weiter – geprüft: identische Trades wie ein
ununterbrochener Lauf.
**Fehler:** jeder Fehler wird protokolliert; nach `max_consecutive_errors` (3) in Folge Not-Aus. Verworfene Kerzen
(fehlerhaft, doppelt, älter, noch offen) lösen keine Entscheidung aus. Futures ohne bekannten Funding-Satz: kein
Ersatzwert, Validator/RIN blockieren.
**Start nur mit Freigabe:** `enabled=True` + PAPER ohne Börsen-Schlüssel + `TRADING_ENGINE_APPROVAL="PAPER-HANDEL
FREIGEGEBEN"`; sonst `EngineDisabledError`. Tests und Vorführungen nutzen nur `process`/`simulate` mit simulierter Uhr.
**Grenzen:** Ausführung zum Schlusskurs der Kerze plus Spread/Slippage (im Betrieb später zum aktuellen Kurs); echte
Marktdaten-Anbindung fehlt (AP-14/15).
