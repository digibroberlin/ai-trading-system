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
