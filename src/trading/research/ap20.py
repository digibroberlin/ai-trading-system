"""AP-20 auf Knopfdruck: historische Profitabilitaetsanalyse mit lokalen, oeffentlichen Daten - ohne Netz, ohne Handel.

  PYTHONPATH=src python -m trading.research.ap20 run --period training --scenarios F,S --variants V0,A,B,C,D,S
  PYTHONPATH=src python -m trading.research.ap20 run --period validierung ... --strategies breakout,trend
  PYTHONPATH=src python -m trading.research.ap20 freeze --cells "F|breakout|C,S|trend|V0" --note "..."
  PYTHONPATH=src python -m trading.research.ap20 run --period test ...       # nur eingefrorene Zellen, je einmal
  PYTHONPATH=src python -m trading.research.ap20 report --out docs/ap20/ergebnisse.md

Datenquelle (Standard): Bitstamp BTC/USD, 1-Minuten-Kerzen, oeffentliche Kopie `ff137/bitstamp-btcusd-minute-data`
(taeglich per GitHub Action aus der oeffentlichen Bitstamp-Schnittstelle erzeugt; Altbestand aus dem Kaggle-Datensatz
"Bitcoin Historical Data" von Zielak), Stand Commit 163afe6, Lizenz CC BY-SA 4.0 (Altbestand) bzw. laut Repository.
Abruf (einmalig, ausserhalb dieses Pakets, siehe docs/AP20_PROTOKOLL.md):
  data/external/bitstamp-btcusd-1m/<commit>/btcusd_bitstamp_1min_2012-2025.csv.gz, btcusd_bitstamp_1min_latest.csv
Mehrere Prozesse duerfen gleichzeitig laufen (je Zelle eine eigene Ergebnisdatei).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from trading.backtest.engine import MONTHLY_REVIEW
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.marketdata.localfile import build_series
from trading.research.campaign import (
    PERIODS,
    SCENARIOS,
    STANDARD_KEYS,
    VARIANTS,
    Bundle,
    Freeze,
    StrategySpec,
    Variant,
    cell_key,
    cell_path,
    guard_test,
    run_cell,
    save_cell,
    standard_strategies,
)

COMMIT = "163afe666e556b8a556103565eb1515acb06b92b"
DATA_DIR = Path("data/external/bitstamp-btcusd-1m") / COMMIT
FILES = ("btcusd_bitstamp_1min_2012-2025.csv.gz", "btcusd_bitstamp_1min_latest.csv")
DESCRIPTION = ("Bitstamp BTC/USD 1-Minuten-Kerzen (Spot), oeffentliche Kopie github.com/ff137/bitstamp-btcusd-minute-data "
               "@ %s: taeglich per GitHub Action aus der oeffentlichen Bitstamp-OHLC-Schnittstelle; Altbestand bis "
               "2025-01-07 aus dem Kaggle-Datensatz 'Bitcoin Historical Data' (Zielak, Version 706)" % COMMIT[:7])
LICENSE = "CC BY-SA 4.0 (Altbestand, Zielak/Kaggle); Aktualisierungen laut Repository (CC BY 4.0, Code MIT)"
INSTRUMENT = InstrumentId(Exchange.BITSTAMP, MarketType.SPOT, "BTC-USD")
DATA_START = datetime(2016, 12, 1, tzinfo=timezone.utc)  # Vorlauf vor dem Training
DATA_END = datetime(2026, 10, 16, tzinfo=timezone.utc)
RESULTS = Path("data/ap20/results")
RESULTS_STRICT = Path("data/ap20/results-streng")
_CENT = Decimal("0.01")
FREEZE = Path("docs/ap20/freeze.json")

# Variante E (optimiert) - wird NACH dem Training und VOR der Validierung festgelegt (docs/AP20_PROTOKOLL.md, Abschnitt 6).
EXTRA_STRATEGIES: Dict[str, Callable[[], StrategySpec]] = {}
EXTRA_VARIANTS: Dict[str, Variant] = {}


def load_bundle(data_dir: Path = DATA_DIR, interval_s: int = 3600, cache_dir: Optional[Path] = Path("data/derived")
                ) -> Bundle:
    paths = [data_dir / name for name in FILES]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise SystemExit("Quelldateien fehlen: %s (Abruf siehe docs/AP20_PROTOKOLL.md)" % ", ".join(missing))
    spot = build_series(paths, INSTRUMENT, interval_s, DATA_START, DATA_END, DESCRIPTION, LICENSE, cache_dir=cache_dir)
    return Bundle(spot)


def strategies(keys: Sequence[str]) -> List[StrategySpec]:
    out: List[StrategySpec] = []
    for key in keys:
        if key in STANDARD_KEYS:
            out += standard_strategies([key])
        elif key in EXTRA_STRATEGIES:
            out.append(EXTRA_STRATEGIES[key]())
        else:
            raise SystemExit("Unbekannte Strategie %r" % key)
    return out


def variants(keys: Sequence[str]) -> List[Variant]:
    found = dict(VARIANTS, **EXTRA_VARIANTS)
    unknown = [k for k in keys if k not in found]
    if unknown:
        raise SystemExit("Unbekannte Variante(n): %s" % ", ".join(unknown))
    return [found[k] for k in keys]


def _split(text: str, default: Sequence[str]) -> List[str]:
    if text in ("", "all", "alle"):
        return list(default)
    return [t.strip() for t in text.split(",") if t.strip()]


def cmd_run(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    period = PERIODS[args.period]
    bundles: Dict[int, Bundle] = {}
    keys = _split(args.strategies, STANDARD_KEYS)
    for scenario_key in _split(args.scenarios, sorted(SCENARIOS)):
        scenario = SCENARIOS[scenario_key]
        for spec in strategies(keys):
            for variant in variants(_split(args.variants, sorted(VARIANTS))):
                key = cell_key(period.name, scenario.key, spec.key, variant.key)
                path = cell_path(Path(args.results), key)
                if path.exists() and period.name != "test":
                    out("vorhanden: %s" % key)
                    continue
                guard_test(Path(args.freeze), Path(args.results), key)  # Test: nur eingefroren und nur einmal
                started = time.perf_counter()
                if spec.interval_s not in bundles:
                    bundles[spec.interval_s] = load_bundle(Path(args.data), spec.interval_s)
                cell = run_cell(bundles[spec.interval_s], period, spec, variant, scenario,
                                halt_review="" if args.mode == "streng" else MONTHLY_REVIEW)
                save_cell(Path(args.results), cell)
                c = cell.card
                out("%s  %5.1f s  Trades %4d  Netto(A) %10s  Netto(B) %10s  DD %5.1f %%" % (
                    key, time.perf_counter() - started, c.trades, c.trading_net.quantize(_CENT),
                    c.economic_net.quantize(_CENT), c.max_drawdown_pct))
    return 0


def cmd_freeze(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    cells = tuple(sorted(set(_split(args.cells, ()))))
    if not cells:
        raise SystemExit("Keine Zellen angegeben.")
    for c in cells:
        if c.count("|") != 2:
            raise SystemExit("Zelle %r: Form Szenario|Strategie|Variante" % c)
    freeze = Freeze(datetime.now(timezone.utc), cells, args.note)
    freeze.save(Path(args.freeze))
    out("eingefroren: %d Zellen, SHA-256 %s" % (len(cells), freeze.digest))
    return 0


def cmd_report(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    from trading.research.campaign_report import write_report

    path = write_report(Path(args.results), Path(args.out), load_bundle(Path(args.data)))
    out("Bericht: %s" % path)
    return 0


def main(argv: Optional[Sequence[str]] = None, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(prog="python -m trading.research.ap20", description=__doc__.splitlines()[0])
    parser.add_argument("--data", default=str(DATA_DIR))
    parser.add_argument("--results", default=str(RESULTS))
    parser.add_argument("--freeze", default=str(FREEZE))
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--period", choices=sorted(PERIODS), required=True)
    run.add_argument("--scenarios", default="F,S")
    run.add_argument("--strategies", default="all")
    run.add_argument("--variants", default="V0,A,B,C,D,S")
    run.add_argument("--mode", choices=("monatlich", "streng"), default="monatlich",
                     help="Not-Aus: simulierte monatliche Pruefung des Eigners (Standard) oder streng ohne Freigabe")
    fr = sub.add_parser("freeze")
    fr.add_argument("--cells", required=True)
    fr.add_argument("--note", required=True)
    rep = sub.add_parser("report")
    rep.add_argument("--out", default="docs/ap20/ergebnisse.md")
    args = parser.parse_args(argv)
    if getattr(args, "mode", "") == "streng" and args.results == str(RESULTS):
        args.results = str(RESULTS_STRICT)  # strenge Laeufe nie mit den Hauptergebnissen vermischen
    commands = {"run": cmd_run, "freeze": cmd_freeze, "report": cmd_report}
    return commands[args.command](args, out)


if __name__ == "__main__":
    sys.exit(main())
