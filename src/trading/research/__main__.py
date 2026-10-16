"""Vorfuehrung:  python3.12 -m trading.research

Prueft jede Strategie der Bibliothek nach den Regeln des Forschungsrahmens (AP-18) auf ERFUNDENEN Daten: Validierung mit
Versuchszaehlung und Kosten-Stresstest, Test nur fuer Bestandene, dazu die Bewertung je Marktphase (AP-19). Auf
Zufallsdaten ist "verworfen" das erwartete, ehrliche Ergebnis. Das Register liegt nur im Speicher.
"""

from __future__ import annotations

import functools
from datetime import timedelta

from trading.backtest import BacktestConfig, BacktestEngine
from trading.config import load_settings
from trading.dashboard.__main__ import synthetic_dataset
from trading.research.evaluation import assess
from trading.research.experiment import Research
from trading.research.registry import StrategyRegistry
from trading.research.splits import split_periods
from trading.strategies import STRATEGY_TYPES, build_strategy


def main() -> None:
    settings = load_settings()
    print("Modus: %s - Forschung auf erfundenen Daten, nichts wird gehandelt." % settings.mode.value.upper())
    dataset = synthetic_dataset(120)
    split = split_periods(dataset.start, dataset.end, gap=timedelta(days=1))
    research = Research(dataset, split, StrategyRegistry())
    print("Training %s-%s | Validierung %s-%s | Test %s-%s" % (
        split.train.start.date(), split.train.end.date(), split.validation.start.date(), split.validation.end.date(),
        split.test.start.date(), split.test.end.date()))
    for kind in sorted(k for k in STRATEGY_TYPES if k != "no_trade"):
        factory = functools.partial(build_strategy, kind)
        rep = research.validate(kind, {"standard": "1"}, factory)
        line = "%-15s Validierung: %-9s Netto %10s | Trades %3d" % (
            kind, "bestanden" if rep.passed else "verworfen", rep.validation.net_result.quantize(1), rep.validation.trades)
        if rep.passed:
            test = research.test(kind, {"standard": "1"}, factory)
            line += " | Test: %s (Netto %s, Kaufen-und-Halten %.2f %%)" % (
                "nicht widerlegt" if test.passed else "durchgefallen", test.test.net_result.quantize(1),
                test.buy_and_hold_return_pct)
        else:
            line += " | Gruende: %s" % "; ".join(rep.reasons)[:120]
        print(line)
    whole = BacktestEngine(dataset.window(split.train.start, split.validation.end), build_strategy("breakout"),
                           BacktestConfig()).run()
    a = assess(whole, dataset)
    print("Bewertung breakout (Training+Validierung) je Marktphase: " + ", ".join(
        "%s %d Trades %s" % (r.regime, r.trades, r.net.quantize(1)) for r in a.by_regime))


if __name__ == "__main__":
    main()
