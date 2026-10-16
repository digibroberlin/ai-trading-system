# Master-Plan – vom Paper-Grundsystem zur technisch fertigen Software

Stand: 2026-10-15 · Ziel: technisch fertige, getestete und dokumentierte Software, **bereit für die Demo-Einrichtung**.
Der Handel selbst (Demo oder echt) wird nicht von Claude gestartet. Wirtschaftliches Ziel des Systems: langfristig möglichst
hoher, robuster **Netto**gewinn nach allen Kosten. Ein solcher Vorteil ist ein Forschungsziel, keine Eigenschaft der Software –
findet sich keiner, muss das System nachweisbar auf Trades verzichten.

Status-Zeichen: ✅ fertig · 🔄 in Arbeit · ⏳ offen · ⛔ blockiert (siehe `BLOCKERS.md`)

## 1. Bestandsaufnahme (2026-10-15)

**Fertig und getestet** (606 Tests, Gegenprobe 193/193, siehe `TESTBERICHT.md`):
Domänenmodell mit Instrument-Schlüssel · Sicherheitslimits und die eine Risikorechnung (2 % Gesamtrisiko, 0,5 % je Trade,
100 % Exposure, 5 % Stress bei 10 % Schock, Margin, Liquidation, Notbremsen) · Datenprüfung (Snapshots, Kerzen, Reihen) ·
Kostenmodell mit Rundung zu Ungunsten · Paper-Executor (atomar, Spot/Futures, Long/Short, SL/TP/Liquidation/Signal, Funding) ·
RIN und SORA regelbasiert · AKI mit Kapitalkurve und Bericht · Pipeline · Ereignisprotokoll mit Hash-Kette (Speicher, Datei,
PostgreSQL) · PostgreSQL-Executor mit Konto-Sperre und Datenbank-Gurten · Migrationen · Neustartfestigkeit · deterministische
Backtesting-Engine auf synthetischen Daten · Docker (db, migrate, app, tests) · Gegenprobe- und Geheimnis-Werkzeuge.

**Unvollständig:** MOMO (nur Schnittstelle; im Backtest über `Strategy`) · KIBO/YUZU (neutrale Platzhalter) · KI-Anbindung
(nur `NullProvider`) · `marketdata/feed.py` (nur Schnittstelle) · keine Börsen-Adapter · nur eine Referenz-Strategie ·
kein Walk-forward · keine Laufzeit-Engine (nur `demo.py`) · kein Dashboard/API · kein Logging/Monitoring · keine automatischen
Backups · keine Server-Konfiguration.

**Technische Schulden / bekannte Sicherheitspunkte** (Audit, `AUDIT.md`): M3 PAPER-Schutz nicht zentral · M7 Order-Wiederholung ·
M8 Ablehnung statt Verkleinerung unter Konkurrenz · M2 Rest (App-Rolle schreibt Kontostand, legt Konten an) · H7 keine
Wiederverbindung zur Datenbank · M9 `verify()` lädt alles · M12 Pakete ohne Prüfsummen · M14 keine Backups · Test-Hook im
Produktivcode · Stress-Limit ohne Datenbank-Gurt · Pipeline-Reihenfolge weicht von der Vorgabe des Eigners ab
(heute RIN → KIBO → YUZU → SORA; gewünscht RIN → SORA → KIBO/YUZU).

**Nicht getestet:** Linux/amd64 und Docker Desktop · Python 3.13+ · Dauerbetrieb/Last · echte Marktdaten · echte Börsen.

## 2. Arbeitspakete (Reihenfolge = Priorität)

### Phase A – Sicherung und Automatisierung
| AP | Inhalt | Abnahme | Status |
|---|---|---|---|
| AP-01 | Lokale Sicherung: Branch-Modell, `stable/*`-Tags, Geheimnis-Scan, Prüfskript, GitHub-Actions-Datei, Wiederherstellungsanleitung, Steuerdokumente | Preflight STABIL, Scan der ganzen Geschichte sauber | ✅ (lokal) |
| AP-02 | GitHub verbinden, alles hochladen, Actions grün | Actions-Lauf grün auf `develop` und `main` | 🔄 `develop` grün seit 2026-10-15 19:26 UTC; `main` noch rot bis zum nächsten stabilen Stand |
| AP-03 | Cloud-Routine (stündlich) einrichten und dokumentieren; Cloud-Umgebung prüfen (PostgreSQL über `scripts/cloud_test_db.sh`, Push-Rechte auf `develop`/Tags) | Probelauf erfolgreich, Sperr-Mechanismus wirkt | ✅ 2026-10-15 (erster Routine-Lauf; Tags aus der Cloud noch nicht ausprobiert) |

### Phase B – Architekturentscheidung
| AP | Inhalt | Abnahme | Status |
|---|---|---|---|
| AP-04 | Untersuchung Hummingbot, Freqtrade, NautilusTrader, CCXT (Doku, Architektur, Lizenz) → Entscheidung in `DECISIONS.md` | begründete Entscheidung, Lizenzen geprüft | ✅ D-006, `FRAMEWORK_ANALYSIS.md` |
| AP-05 | Abhängigkeiten: Sicherheitsprüfung (pip-audit), Versionen mit Prüfsummen (M12) | Audit ohne bekannte Lücken, Lockfile | ✅ D-010 (Rest M12 → AP-27) |

### Phase C – Sicherheit und Robustheit (Audit Stufe 2)
| AP | Inhalt | Abnahme | Status |
|---|---|---|---|
| AP-06 | Zentraler Startpunkt mit PAPER-Schutz für jeden Prozess, breitere Schlüssel-Erkennung (M3) | jeder Einstieg prüft; Tests | ✅ (Gegenprobe E04–E10) |
| AP-07 | Globaler Kill Switch: dauerhaft in der DB, manuell und automatisch (Datenfehler, Protokollfehler, Notbremsen), Freigabe nur bewusst | Tests inkl. Mehrprozess, Gegenprobe | ✅ D-011 |
| AP-08 | Order-Wiederholung liefert ursprüngliches Ergebnis (M7) | Tests, auch nach Neustart | ✅ D-012 |
| AP-09 | Größe unter Konkurrenz innerhalb der Sperre neu berechnen (M8) | exakte Mehrprozess-Erwartung | ✅ D-013 |
| AP-10 | Rechte der App-Rolle (M2-Rest), Konten nur administrativ | Schema-Tests | ⏳ |
| AP-11 | Datenbank-Wiederverbindung, Zeitlimits, hängende Transaktionen (H7) | Tests mit Verbindungsabbruch | ⏳ |
| AP-12 | Pipeline-Reihenfolge nach Vorgabe: MOMO → RIN → SORA → KIBO/YUZU → Executor → AKI | Tests, Doku | ⏳ |

### Phase D – Marktdaten und Börsen (nur lesend)
| AP | Inhalt | Abnahme | Status |
|---|---|---|---|
| AP-13 | Börsenregeln je Instrument (Preis-/Mengenschritt, Mindestgröße, Gebührenstufen) im Risiko- und Kostenmodell | Tests, Gegenprobe | ⏳ |
| AP-14 | Adapter für historische Kerzen und Funding (Coinbase Spot, Bitget Spot + Futures) mit lokalem Zwischenspeicher, Ratenbegrenzung, Datenprüfung | Tests mit aufgezeichneten Antworten; ein echter Abruf als technische Validierung | ⏳ |
| AP-15 | WebSocket-Marktüberwachung: Wiederverbindung, Lückenerkennung, Herzschlag, veraltete Daten → Kill Switch | Tests mit simuliertem Server | ⏳ |

### Phase E – Strategien und Forschung
| AP | Inhalt | Abnahme | Status |
|---|---|---|---|
| AP-16 | Strategie-Bibliothek: Trendfolge, Momentum, Mean Reversion, Breakout, Volatilität, Scalping; Benchmarks (Kaufen und Halten, Nicht-Handeln) | jede Strategie getestet, ohne Blick in die Zukunft | ⏳ |
| AP-17 | Marktphasen-Erkennung (Trend/Seitwärts, Volatilität) – Grundlage für KIBO und YUZU (regelbasiert) | Tests | ⏳ |
| AP-18 | Forschungsrahmen: Trainings-/Validierungs-/Testdaten strikt getrennt, Walk-forward, Kostenempfindlichkeit, Zählung aller Versuche gegen Überanpassung, Strategie-Register mit Freigabestatus | Tests; Test-Daten nur einmal je Strategie-Version | ⏳ |
| AP-19 | AKI: Bewertung von Strategien und Agenten (Nettoergebnis, Erwartungswert, Drawdown, Profit-Faktor, Stabilität über Marktphasen) | Tests | ⏳ |
| AP-20 | Erste ehrliche Untersuchung auf echten historischen Daten (technische Validierung), Bericht – auch wenn kein Vorteil gefunden wird | Bericht in `docs/` | ⏳ |

### Phase F – Laufzeit-Engine (bleibt deaktiviert)
| AP | Inhalt | Abnahme | Status |
|---|---|---|---|
| AP-21 | Ereignisgesteuerte Engine: schneller Regelpfad je Marktereignis, Agenten-Zyklus alle 1–5 min, ereignisbasierte Zusatzanalysen; Start nur mit ausdrücklicher Konfiguration + Freigabe; in Tests nur mit simulierter Uhr und synthetischem Markt | Tests (Tausende Ereignisse, Fehlerfälle, Neustart) | ⏳ |
| AP-22 | KI-Anbieter (z. B. Claude) mit Budget, Zeitlimit, Zwischenspeicher; deterministischer Ersatz ohne KI | Tests mit Attrappe | ⏳ (B-004 für echte Aufrufe) |

### Phase G – Dashboard
| AP | Inhalt | Abnahme | Status |
|---|---|---|---|
| AP-23 | FastAPI (nur lesend) + WebSocket-Aktualisierung; Modi BACKTEST / DEMO / LIVE (deaktiviert) | API-Tests | ⏳ |
| AP-24 | Web-Oberfläche: Kapital, Positionen, P&L, Kapitalkurve, Drawdown, Kennzahlen, Agenten/Strategien, Blockaden, Systemstatus, Fehler | lokal mit synthetischen Daten geprüft | ⏳ |

### Phase H – Betrieb und Server (vorbereitet, nicht ausgeführt)
| AP | Inhalt | Abnahme | Status |
|---|---|---|---|
| AP-25 | Strukturiertes Logging, Gesundheits-Endpunkte, Kennzahlen | Tests | ⏳ |
| AP-26 | Backups mit Rückspiel-Test (M14) | automatischer Rückspiel-Test | ⏳ |
| AP-27 | Produktions-Compose, Start-/Stopp-Skripte, Neustartmechanismen, Prozessüberwachung | lokal geprüft | ⏳ |
| AP-28 | Server-Härtung und Deployment-Skripte für Hetzner, Überwachung/Alarme | Trockenlauf lokal | ⏳ (B-005 für echten Server) |

### Phase I – Demo-Vorbereitung und Abschluss
| AP | Inhalt | Abnahme | Status |
|---|---|---|---|
| AP-29 | Order-Anbindung für Börsen-Demokonten (z. B. Bitget Demo), standardmäßig aus, nur gegen Attrappen getestet | Tests | ⛔ B-003 (Entscheidung) |
| AP-30 | Abschlussprüfung aller Teile, Abschlussbericht, „BEREIT FÜR DEMO-EINRICHTUNG“ | vollständige Prüfung dokumentiert | ⏳ |

## 3. Geplante stabile Stände

`stable/001-phase1-3-foundation` ✅ · `stable/002-stage1-core-backtesting` ✅ · `stable/003-autonomy-baseline` ✅ (AP-01) ·
`stable/004-safety-stage2` (AP-06…12) · `stable/005-market-data` (AP-13…15) · `stable/006-strategy-research` (AP-16…20) ·
`stable/007-trading-engine` (AP-21…22) · `stable/008-dashboard` (AP-23…24) · `stable/009-operations` (AP-25…28) ·
`stable/010-release-candidate` (AP-29…30). Die Nummern folgen dem tatsächlichen Verlauf.

## 4. „Technisch fertig“ heißt

Alle Arbeitspakete außer den blockierten sind abgeschlossen; alle Tests grün (Mac, Container, GitHub Actions); Gegenprobe
ohne unentdeckten Fehler; Doku vollständig; nicht prüfbare Teile (echte Börse, Server) sind ausdrücklich als
„nicht verifiziert“ gekennzeichnet; letzter stabiler Tag gesetzt und hochgeladen.
