# Untersuchung Open-Source-Systeme (AP-04)

Stand: 2026-10-15. Quellen: die offiziellen GitHub-Seiten und Lizenzdateien der Projekte sowie die CCXT-Dokumentation
(abgerufen am 2026-10-15). Angaben über Funktionen beruhen auf diesen Seiten, nicht auf eigenen Tests der Projekte.

## Kurzüberblick

| Projekt | Lizenz | Kern | Coinbase | Bitget | Stärken | Passt als … |
|---|---|---|---|---|---|---|
| **CCXT** | MIT | Python-/JS-Bibliothek, einheitliche REST- und WebSocket-Schnittstelle (CCXT Pro ist inzwischen kostenlos im Paket enthalten) | ja (`coinbase`) | ja (zertifiziert, WebSocket) | sehr viele Börsen, einheitliche Datenformate, aktiv gepflegt | **Börsen-Baustein** (Marktdaten, später Demo-Orders) |
| **Freqtrade** | **GPL-3.0** | komplette Bot-Anwendung auf Basis von CCXT, Backtesting, Hyperopt, FreqAI | nein (nicht gelistet) | ja (Spot + Futures) | ausgereiftes Backtesting, Werkzeuge gegen Look-ahead | **Ideengeber** – Code nicht übernehmen (GPL würde das ganze Projekt GPL-pflichtig machen) |
| **NautilusTrader** | LGPL-3.0 | Rust-Kern, ereignisgesteuert, gleicher Code für Backtest und Live, Python als Steuerungsebene | ja | **nein** | Leistung, Präzision, saubere Architektur | **Vorbild für die Architektur** – als Basis ungeeignet: kein Bitget, Python 3.13–3.14, vollständiger Neubau nötig |
| **Hummingbot** | Apache-2.0 | Bot-Anwendung mit Konnektoren (REST + WebSocket), Market Making, Strategy V2 (Controller, Executors) | ja (Advanced Trade, Spot) | ja | Market Making, Order-Lebenszyklus (Executors), viele Konnektoren | **Ideengeber** für Market Making und Executors; als Bibliothek schwer einzubinden |

## Entscheidung

**Der eigene Kern bleibt; Börsenzugriff über CCXT; die anderen Projekte dienen als Vorbild.** Begründung:

1. **Sicherheit vor Bequemlichkeit.** Unsere Risikorechnung, die Datenbank-Gurte, das unveränderliche Protokoll und der
   Paper-Executor sind auf die Regeln dieses Projekts zugeschnitten und mit 633 Tests und 193 Gegenproben abgesichert.
   Keines der Frameworks bietet z. B. ein prozessübergreifendes 2-%-Limit mit Datenbank-Gurt oder ein Stress-Limit „nach der
   Order“. Ein Wechsel würde diese Absicherung verlieren und neu erarbeiten lassen.
2. **Bitget und Coinbase zugleich** deckt nur CCXT (und Hummingbot als ganze Anwendung) ab. NautilusTrader hat kein Bitget.
3. **Lizenzen.** MIT (CCXT) und Apache-2.0 (Hummingbot) sind mit jeder späteren Nutzung vereinbar. GPL-3.0 (Freqtrade)
   würde bei Code-Übernahme das gesamte Projekt unter die GPL stellen – daher nur Ideen, kein Code. LGPL-3.0 (Nautilus) wäre
   als Bibliothek nutzbar, passt aber technisch nicht.
4. **Austauschbarkeit.** CCXT wird nur in einem eigenen Paket (`trading.adapters`) verwendet, hinter unseren Schnittstellen
   (`HistoricalDataSource`, Marktdaten-Feed). Der Kern bleibt ohne Netzwerk-Bibliotheken; `tests/test_no_real_trading.py`
   wird dafür sichtbar so angepasst, dass Netzwerkzugriff **nur** dort erlaubt ist.

## Übernommene Ideen (ohne Code-Übernahme)

* **Freqtrade:** eigene Prüfwerkzeuge gegen Look-ahead (Ergebnis darf sich nicht ändern, wenn Zukunftsdaten fehlen – bei uns
  bereits als Test vorhanden), Warnungen vor Überanpassung bei Parameter-Suchen.
* **NautilusTrader:** derselbe Strategie-Code für Backtest und Betrieb, deterministische ereignisgesteuerte Verarbeitung
  (bei uns: Backtest nutzt dieselbe Pipeline und denselben Executor), klare Trennung von Daten-, Ausführungs- und Risikoebene.
* **Hummingbot:** Executors für den Order-Lebenszyklus (Position, Raster, TWAP) und Market-Making-Logik als Vorbild für spätere
  Strategien; Konnektoren mit getrennten Daten- und Order-Teilen.

## Offene Prüfungen

* CCXT vor der Aufnahme: Sicherheitsprüfung des Pakets und seiner Abhängigkeiten (AP-05), feste Version mit Prüfsumme.
* Funktionsumfang je Börse prüfen (`exchange.has[...]`), z. B. liefert Coinbase per WebSocket Kerzen nur im 5-Minuten-Raster.
  Fehlende Funktionen werden je Instrument dokumentiert; fehlende Daten führen wie überall zur Blockade.

## Quellen

* https://github.com/ccxt/ccxt (MIT) · https://docs.ccxt.com/docs/pro-manual · https://docs.ccxt.com/docs/examples/py/coinbase-watch-ohlcv
* https://github.com/freqtrade/freqtrade · Lizenzdatei: GPL-3.0
* https://github.com/nautechsystems/nautilus_trader (LGPL-3.0)
* https://github.com/hummingbot/hummingbot (Apache-2.0)
