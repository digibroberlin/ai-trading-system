# Backtesting-Engine

Stand: 2026-10-13 · ausschließlich PAPER · ausschließlich synthetische (erfundene) Daten · keine Börsenverbindung

Ein Backtest spielt eine Strategie auf einem festen Datensatz durch und zeigt, was dabei herausgekommen **wäre**.
Er ist ein Messgerät, kein Gewinnversprechen. Ergebnisse auf erfundenen Daten prüfen nur die Technik.

## Was die Engine garantiert

| Zusage | Wie sie eingehalten wird | Nachweis (Test) |
|---|---|---|
| **Kein Blick in die Zukunft** | Die Strategie sieht nur Kerzen, die bereits abgeschlossen sind. Ausgeführt wird zum Eröffnungskurs der **nächsten** Kerze. | `test_strategy_only_ever_sees_closed_candles_up_to_now`, `test_results_up_to_a_point_do_not_depend_on_later_data`, `test_changing_future_prices_does_not_change_the_past` |
| **Keine geschönten Ausführungen** | Kauf zum Briefkurs plus Slippage, Verkauf zum Geldkurs minus Slippage. Stop mit zusätzlicher Slippage. Kurslücke über den Stop: schlechterer Eröffnungskurs. Stop und Ziel in derselben Kerze: der Stop gilt. Am letzten Zeitpunkt des Datensatzes wird nicht mehr gehandelt. | `test_gap_below_the_stop_fills_at_the_worse_open`, `test_stop_wins_when_one_bar_touches_stop_and_target`, `test_orders_at_the_very_last_bar_cannot_be_executed` |
| **Dieselben Regeln wie im Paper-Betrieb** | Die Engine benutzt die echte Pipeline (RIN, SORA, Risikomanager, Executor). Es gibt keine zweite, vereinfachte Rechnung. | `test_every_fill_respects_all_limits_at_once_and_everything_is_logged` |
| **Wiederholbar** | Gleiche Daten + gleiche Einstellungen + gleiche Strategie = bit-identisches Ergebnis, auch in einem anderen Prozess. Jeder Lauf hat einen Fingerabdruck (`result_hash`). | `test_same_inputs_give_bit_identical_results`, `test_result_is_identical_in_a_separate_process_with_another_hash_seed` |
| **Vollständig protokolliert** | Jeder Vorschlag, jede Blockade (mit Grund), jede Ausführung, jede Schließung und jedes Funding steht im Ereignisprotokoll (Hash-Kette). Ist das Protokoll beschädigt, gilt der ganze Lauf als ungültig. | `test_a_damaged_event_log_invalidates_the_whole_run` |
| **Abstimmung der Buchhaltung** | Endkapital = Startkapital + realisiertes Ergebnis + unrealisiertes Ergebnis – sonst bricht der Bericht ab. | `test_report_reconciles_and_trades_cover_long_short_spot_and_futures` |
| **Nur saubere Daten** | Ein Datensatz mit Lücken, doppelten, falsch sortierten oder in sich widersprüchlichen Kerzen wird abgelehnt – mit einer Liste **aller** Befunde. Futures brauchen Funding-Daten. | `tests/test_backtest_data.py` |
| **Fairer Vergleich** | Zwei Ergebnisse lassen sich nur vergleichen, wenn Datensatz, Startkapital, Kosten und Limits gleich waren. | `test_results_from_different_data_or_assumptions_are_not_comparable` |

## Ablauf je Zeitpunkt

```
1. Kerzen, die JETZT enden, werden verarbeitet   → Stop-Loss, Take-Profit, Liquidation, Bewertung
2. Funding-Abrechnungen von JETZT werden gebucht  (nur Futures)
3. Ein Punkt der Kapitalkurve wird festgehalten   (bewertet zum Schlusskurs, nach allen Kosten)
4. Die Strategie entscheidet                      (sie sieht nur abgeschlossene Kerzen)
5. Ausführung zum Eröffnungskurs der Kerze, die JETZT beginnt – erst Ausstiege, dann Einstiege
   (gibt es keine solche Kerze, wird nicht gehandelt und das Setup als blockiert protokolliert)
```

## Kosten (einstellbar)

`CostModel` in `src/trading/execution/costs.py`: Taker-Gebühr 0,06 %, Maker-Gebühr 0,02 %, Slippage 2 bp (Einstieg und
Ausstieg auf Signal) bzw. 5 bp (Stop). Der Bid/Ask-Spread wird je Instrument im Datensatz angegeben. Funding kommt als
Ereignis aus den Daten (Satz und Kurs), nie als stiller Standardwert. Das sind **Schätzwerte**, keine echten Tarife.

Alle Kennzahlen sind netto, also nach Gebühren, Slippage, Spread und Funding.

## Kennzahlen im Ergebnis

Endkapital, Nettoergebnis, Rendite, realisiertes und unrealisiertes Ergebnis, Anzahl Trades, Gewinnrate, Profit-Faktor,
durchschnittlicher Gewinn und Verlust, Erwartungswert je Trade, größter Gewinn/Verlust, längste Verlustserie,
maximaler Drawdown (aus der laufend bewerteten Kapitalkurve), Sharpe- und Sortino-Kennzahl, Zeit im Markt,
höchste und mittlere Auslastung (Exposure), Summe der Gebühren und des Fundings, Ausstiege nach Grund,
blockierte Setups nach Grund.

## Benutzung

Vorführung auf erfundenen Daten:

```bash
PYTHONPATH=src python3.12 -m trading.backtest
```

Fertige Strategien (AP-16): `trading.strategies` – Trendfolge, Momentum, Mean Reversion, Breakout, Volatilität,
Scalping, dazu `NoTradeStrategy` und `buy_and_hold(...)` als Vergleichsmaßstäbe. `default_strategies()` liefert je eine
mit Standardeinstellungen, `build_strategy("breakout")` eine bestimmte.

Eigene Strategie (Beispiel in `src/trading/backtest/reference.py`):

```python
from trading.backtest import BacktestEngine, BacktestConfig, Strategy, StrategyDecision, comparison_table

class MeineStrategie(Strategy):
    name = "meine-strategie"

    def on_bar(self, view):          # view: nur abgeschlossene Kerzen und offene Positionen
        return StrategyDecision()    # Einstiege (mit Stop und Ziel) und Ausstiege vorschlagen

ergebnis = BacktestEngine(datensatz, MeineStrategie(), BacktestConfig()).run()
print(ergebnis.summary())
```

Eine Strategie **schlägt nur vor**. Größe, Hebel und Freigabe bestimmen SORA und der unabhängige Risikomanager;
eine Strategie kann Sicherheitslimits weder sehen noch verändern.

## Vorbereitet für historische Börsendaten

`HistoricalDataSource` in `src/trading/backtest/data.py` ist die Schnittstelle für spätere Coinbase- und Bitget-Daten.
Sie ist **nur lesend** (Kerzen und Funding laden) und kennt keine Orders. Jedes Instrument trägt Börse, Markttyp und
Symbol (`InstrumentId`), dadurch bleiben z. B. BTC-Spot bei Coinbase und BTC-Futures bei Bitget getrennt.
Ein Adapter für echte Daten ist noch **nicht** gebaut (das wäre ein Netzwerkzugriff und braucht Ihre Freigabe).

## Grenzen (ehrlich)

* **Erfundene Daten sagen nichts über echte Märkte.** Die Referenz-Strategie (gleitende Durchschnitte) ist ein Technik-Test.
* **Ausführung nur zum Eröffnungskurs der nächsten Kerze.** Keine Limit-Orders, keine Teilausführungen, kein Orderbuch;
  Slippage ist ein fester Wert und hängt nicht von der Ordergröße ab.
* **Innerhalb einer Kerze ist die Reihenfolge unbekannt.** Deshalb gilt die vorsichtige Annahme „Stop zuerst“.
* **Keine Börsenregeln je Handelspaar** (Mindestgröße, Preis- und Mengenschritte) – kommt mit den Börsen-Adaptern.
* **KIBO und YUZU sind neutrale Platzhalter** (stimmen immer zu, ohne Urteil). Das Ergebnis nennt sie ausdrücklich.
* **Vereinfachte Futures-Mathematik:** isolierte Margin, einfache Liquidationsformel; bei Liquidation gilt die gesamte Margin als verloren.
* **Ein Backtest über PostgreSQL braucht eine frische Datenbank.** Die Datenbank führt je Instrument eine einzige
  Marktzeitleiste; ein zweiter Lauf über denselben Zeitraum wird abgebrochen (getestet). Standard ist der Lauf im Speicher.
* **Gefahr der Überanpassung:** Wer viele Varianten auf demselben Datensatz probiert, findet zufällig eine „gute“.
  Vor jeder Aussage über eine Strategie: getrennte Zeiträume zum Entwickeln und Prüfen (Walk-forward).
