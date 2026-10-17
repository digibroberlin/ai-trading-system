# Abschlussbericht der Softwareentwicklung (AP-30) – Stand 2026-10-17

**Kurzfassung:** Das KI-Trading-System ist als **Paper-System** (kein echtes Geld) fertig entwickelt und geprüft, soweit
das ohne Ihre Entscheidungen möglich ist. Es läuft nichts: kein Bot, kein Paper-, Demo- oder Echtgeld-Betrieb, keine
Börsen-Schlüssel, keine KI-Ausgaben. Was noch fehlt, hängt an sechs Entscheidungen bzw. Freigaben von Ihnen (unten).

## 1. Was gebaut ist

| Bereich | Inhalt | Pakete |
|---|---|---|
| Sicherheit | PAPER-Schutz in jedem Prozess, unveränderliche Limits, unabhängiger Risikomanager, globaler Not-Aus in der Datenbank, Börsenregeln je Instrument | AP-06–13 |
| Sechs Agenten | MOMO (Strategien), RIN (Regeln/Universum), SORA (Positionsgröße), KIBO (Technik), YUZU (Umfeld), AKI (Protokoll, Kosten, Bewertung) – alle in Backtest, Laufzeit-Engine und Paper-Dienst | AP-12, 16, 17, 19, 21, 27 |
| Strategien & Forschung | 6 Strategien + 2 Vergleichsmaßstäbe; getrennte Zeiträume, Walk-forward, Versuchszählung, Kosten-Stress, Register mit Freigabestatus; Untersuchung auf echten Daten auf Knopfdruck | AP-16, 18, 19, D-032 |
| Kosten | Nettogewinn nach ALLEN Kosten: Handel, Funding, KI (je Agent/Modell/Analyse/Trade), Server und Marktdaten; harte KI-Budgets (Standard 0) | AP-31–33, 28 |
| KI | Kosten-Orchestrator (Regeln zuerst, Cache, Modellstufen mit Wertschranke), Claude-Anschluss **nicht eingeschaltet** | AP-31, 32, 22 |
| Marktdaten | Historische Kerzen/Funding (Coinbase, Bitget), Live-Überwachung per WebSocket mit Lücken-Nachladen und Not-Aus bei veralteten Daten, Funding-Sätze für Futures im Betrieb – nur lesend | AP-14, 15, D-031 |
| Oberfläche | Dashboard (nur lesend) mit Kapital, Kurve, Positionen, Agenten, Kosten, Gesundheit; LIVE gesperrt | AP-23–25 |
| Betrieb | Paper-Dienst mit doppelter Freigabe und Wächter, Sicherungen mit Rückspiel-Test, Produktions-Compose, Server-Härtung, Alarme | AP-25–28 |
| Sicherheitsnachweis | Keine echte Börsenorder möglich – durch Tests über Code, Skripte, Container und Abhängigkeiten belegt, dazu Laufzeit-Sperren für jede Netzanfrage; Prüfung der API-Konfiguration ohne Ausgabe von Geheimnissen (`python scripts/check_api_config.py`) | AP-34 |

## 2. Prüfergebnisse

Alle Prüfungen auf dem Endstand `develop` @ `0dd5d3e`, gekennzeichnet als `stable/005-release-candidate`
(Protokoll: `docs/TEST_RESULTS.md`, 2026-10-17 01:28 UTC):

| Prüfung | Ergebnis |
|---|---|
| Geheimnis-Scan (Arbeitsstand und gesamte Git-Geschichte) | keine Funde |
| Codequalität (ruff) und Typen (mypy --strict) | ohne Befund (109 Quelldateien) |
| Alle Tests – Python 3.12, PostgreSQL 16, nichts übersprungen | **1821/1821 bestanden**, 0 übersprungen, 0 fehlgeschlagen |
| Python 3.9 (Kernprüfung) | 1575 bestanden, 16 übersprungen (WebSocket-Bibliothek und Datenbank-Treiber gibt es dort nicht – erwartet) |
| Gegenprobe (absichtlich eingebaute Fehler müssen auffallen) | **513/513 entdeckt** (Kern 226, Backtest 182, Datenbank 105), in fünf gleichzeitigen Teilen, 58 min |
| GitHub Actions (`develop`, mit PostgreSQL) | grün |
| **Gesamturteil `preflight.py --mutation`** | **STABIL** |

**Ehrlich protokolliert:** Ein erster Abschlusslauf meldete NICHT STABIL – nicht wegen eines Fehlers im Handelscode,
sondern weil gleichzeitige Testläufe sich an der serverweiten Datenbank-Rolle störten. Die strenge Grundprobe hat das
erkannt; behoben in der Testumgebung, danach STABIL (beide Läufe stehen in `docs/TEST_RESULTS.md`).

**Keine echte Börsenorder möglich (AP-34):** Es gibt nur den Modus PAPER; Börsen-Schlüssel in der Umgebung brechen jeden
Start ab; der Code enthält keine Order-, Abhebungs- oder Signatur-Wege, kein Börsen-SDK und nur vier bekannte
Fremdpakete; jede Netzanfrage wird zur Laufzeit auf öffentliche Marktdaten beschränkt; Container starten nur fünf bekannte
Programme. Das prüfen automatische Tests bei jedem Lauf; eine unabhängige Gegenprüfung fand 12 Test-Lücken, die alle
geschlossen sind (heute ermöglichte keine davon eine Order). Die API-Konfiguration der Cloud-Umgebung ist geprüft:
`python scripts/check_api_config.py` → OK (keine Börsen-Zugangsdaten, kein Echtgeld-Schalter); einziger Hinweis:
`GH_TOKEN` – der GitHub-Zugang der Cloud-Sitzung, kein Börsen-Schlüssel.

## 3. Was Sie entscheiden oder tun müssen (Details in `docs/BLOCKERS.md`)

| Nr. | Worum es geht | Was danach möglich ist |
|---|---|---|
| B-008 | Netzfreigabe der Cloud-Umgebung für 4 Börsen-Adressen (kostenlos, ca. 2 Minuten) | erster echter Datenabruf, Live-Probe der Überwachung, AP-20 mit einem Befehl (`python -m trading.research.study`) |
| B-005 | Server (z. B. Hetzner, ca. 4–6 €/Monat), Alarm-Kanal, Sicherungskopie außerhalb | Paper-Betrieb rund um die Uhr nach `docs/DEPLOYMENT.md` |
| B-004 | KI-Budget (3 Zahlen) und Anthropic-Zugang | KI-Zweitmeinungen – erst sinnvoll, wenn AP-20/33 einen Nutzen zeigen |
| B-003 | Order-Anbindung an ein Börsen-**Demokonto** (z. B. Bitget Demo) – ja/nein | AP-29; bis dahin handelt der Paper-Executor intern |
| B-009 | Geprüften Stand nach `main` übernehmen (Pull Request `develop` → `main`, ca. 1 Minute); Auftrag der stündlichen Routine anpassen | `main` = geprüfter Stand; Claude verändert `main` nicht selbst |
| B-007 | Tags `stable/004` und `stable/005` einmal im Browser anlegen (die Cloud darf keine Tags hochladen; Anleitung in B-007) | stabile Stände auf GitHub gekennzeichnet |

## 4. Ehrliche Grenzen

* **Kein Profitnachweis.** Alle Strategien sind nur auf erfundenen Daten technisch geprüft; ob eine davon nach allen
  Kosten Geld verdient, zeigt erst AP-20 mit echten Daten – auch das Ergebnis „kein Vorteil“ wäre ein gültiges Ergebnis.
* **Börsen-Anbindung nur nachgebildet getestet:** Antwortformen nach der Schnittstellenbeschreibung, kein echter Abruf
  (B-008). Echte Börsenregeln (Mindestgrößen, Schritte) fehlen; bis dahin gelten die eigenen, strengeren Limits.
* **Funding-Kurs** zur Abrechnung ist eine Näherung (Kerzen-Schluss statt Mark-Preis); wirkt nur auf den Funding-Betrag.
* **Docker-Images** wurden in der Cloud nicht gebaut (Proxy); Compose-Schema, Skripte (Trockenlauf) und Dienst (mit
  simuliertem Server und echter Datenbank) sind geprüft.
* **KI** ist angeschlossen, aber aus (Budget 0, kein Schlüssel).

## 5. Bereitschaft

**SOFTWARE BEREIT FÜR GEMEINSAME DEMO-EINRICHTUNG.**

Alles, was ohne Ihre Entscheidungen möglich ist, ist gebaut und geprüft. Es wurde nichts gestartet und nichts gehandelt.
Für die gemeinsame Einrichtung schlage ich diese Reihenfolge vor:

1. **B-008** Netzfreigabe → AP-20 auf echten Daten (`python -m trading.research.study …`). Erst dieses Ergebnis zeigt, ob
   eine Strategie nach allen Kosten besteht. Besteht keine, ist ein Demo-Betrieb nur ein Technik-Test.
2. **B-009 / B-007** geprüften Stand nach `main` übernehmen und die Tags anlegen.
3. **B-005** Server buchen → Paper-Betrieb nach `docs/DEPLOYMENT.md` (erst nach Ihrer Freigabe: `TRADING_ENGINE_APPROVAL`).
4. **B-003** Entscheidung über die Anbindung an ein Börsen-**Demokonto** → AP-29 (eigenes Paket mit eigenen Tests).
5. **B-004** KI-Budget – optional, erst wenn AP-20/AP-33 einen Nutzen zeigen.

Echter Handel mit Geld ist darüber hinaus **nicht** vorbereitet: Er braucht eine separate Freigabe und zusätzliche
Sicherheitsprüfungen (Ergänzung vom 2026-10-16).
