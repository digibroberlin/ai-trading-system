"""Untersuchung auf echten historischen Daten auf Knopfdruck (Vorbereitung AP-20, D-032).

  PYTHONPATH=src python3.12 -m trading.research.study --exchange bitget --market spot --symbols BTC-USDT,ETH-USDT \
      --interval 3600 --start 2025-01-01 --end 2026-09-01 --out docs/UNTERSUCHUNG_AP20.md

Ablauf - streng nach dem Forschungsrahmen (AP-18/19), ohne jede Optimierung:
  1. Daten laden (AP-14, nur lesend, Zwischenspeicher) und streng pruefen - fehlerhafte Daten: Abbruch, kein Bericht.
  2. Zeitraeume: Training | Validierung | Test (60/20/20 %, 2 Tage Sperrabstand).
  3. Jede Strategie: Validierung mit Versuchszaehlung und Kosten-Stress; den Test sieht nur, wer besteht - einmal.
  4. Walk-forward-Stabilitaet und Nutzen von KIBO/YUZU (gepaart je Fenster gegen neutrale Platzhalter, AP-33) - beides
     NUR auf Training+Validierung: Die Testdaten bleiben bis zum einmaligen Test unberuehrt.
  5. Bericht als Markdown - auch wenn nichts besteht: "kein belastbarer Vorteil" ist ein gueltiges Ergebnis.
KI-Kosten: Ohne Budget (B-004) wird keine KI-Variante gerechnet; der Bericht sagt das.
"""

from __future__ import annotations

import argparse
import functools
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple

from trading.audit.ai_value import MethodComparison
from trading.backtest.data import DataQualityError, MarketDataset
from trading.backtest.engine import BacktestConfig
from trading.research.evaluation import agent_value, walk_forward_stability
from trading.research.experiment import Research, TestReport, ValidationReport
from trading.research.registry import StrategyRegistry
from trading.research.splits import DataSplit, Fold, split_periods, walk_forward
from trading.strategies import STRATEGY_TYPES, build_strategy

D = Decimal
STUDY_STRATEGIES = tuple(sorted(k for k in STRATEGY_TYPES if k != "no_trade"))


@dataclass(frozen=True)
class StrategyRow:
    name: str
    validation: ValidationReport
    test: Optional[TestReport]
    wf_folds: int
    wf_positive: Optional[Decimal]  # Anteil positiver Walk-forward-Fenster (Training+Validierung)


@dataclass(frozen=True)
class StudyResult:
    rows: Tuple[StrategyRow, ...]
    agents: Optional[MethodComparison]
    folds: Tuple[Fold, ...]
    markdown: str

    @property
    def winners(self) -> Tuple[str, ...]:
        return tuple(r.name for r in self.rows if r.test is not None and r.test.passed)


def run_study(dataset: MarketDataset, source_note: str, now: datetime, config: Optional[BacktestConfig] = None,
              strategies: Sequence[str] = STUDY_STRATEGIES, gap: timedelta = timedelta(days=2),
              wf_train: timedelta = timedelta(days=90), wf_test: timedelta = timedelta(days=30)) -> StudyResult:
    config = config or BacktestConfig()
    split = split_periods(dataset.start, dataset.end, gap=gap)
    research = Research(dataset, split, StrategyRegistry(), config)
    try:  # nur Training+Validierung - nie in den Testzeitraum hinein
        folds: Tuple[Fold, ...] = tuple(walk_forward(split.train.start, split.validation.end, wf_train, wf_test))
    except ValueError:
        folds = ()
    rows: List[StrategyRow] = []
    for kind in strategies:
        factory = functools.partial(build_strategy, kind)
        validation = research.validate(kind, {"standard": "1"}, factory)
        test = research.test(kind, {"standard": "1"}, factory) if validation.passed else None
        wf_positive: Optional[Decimal] = None
        if folds:
            _, wf_positive = walk_forward_stability(dataset, folds, factory, config)
        rows.append(StrategyRow(kind, validation, test, len(folds), wf_positive))
    agents = agent_value(dataset, folds, functools.partial(build_strategy, "breakout"), config) if folds else None
    markdown = render(dataset, source_note, now, split, tuple(rows), agents, folds)
    return StudyResult(tuple(rows), agents, folds, markdown)


def _q(value: Optional[Decimal], places: str = "0.01") -> str:
    return "–" if value is None else str(value.quantize(D(places)))


def render(dataset: MarketDataset, source_note: str, now: datetime, split: DataSplit, rows: Tuple[StrategyRow, ...],
           agents: Optional[MethodComparison], folds: Tuple[Fold, ...]) -> str:
    s = split
    out = ["# Untersuchung auf echten historischen Daten (AP-20)", "",
           "Erstellt %s mit `python -m trading.research.study`. **Nur Paper-Forschung – nichts wurde gehandelt.**"
           % now.strftime("%Y-%m-%d %H:%M UTC"), "",
           "## Daten", "",
           "* Quelle: %s" % source_note,
           "* Instrumente: %s" % ", ".join(i.key for i in dataset.instruments),
           "* Zeitraum: %s bis %s; Kerzen: %d; Warnungen der Datenprüfung: %d" % (
               dataset.start.date(), dataset.end.date(), sum(len(x.candles) for x in dataset.series),
               len(dataset.warnings)),
           "* Fingerabdruck: `%s`" % dataset.fingerprint,
           "* Training %s–%s · Validierung %s–%s · Test %s–%s (Sperrabstand dazwischen)" % (
               s.train.start.date(), s.train.end.date(), s.validation.start.date(),
               s.validation.end.date(), s.test.start.date(), s.test.end.date()),
           "", "## Strategien (Standard-Einstellungen, nicht optimiert)", "",
           "| Strategie | Validierung | Netto Validierung | Trades | Sharpe/Schwelle | Test | Netto Test | "
           "Kaufen-und-Halten (Test) | Walk-forward positiv |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        v, t = r.validation, r.test
        out.append("| %s | %s | %s | %d | %s / %s | %s | %s | %s | %s |" % (
            r.name, "bestanden" if v.passed else "verworfen", _q(v.validation.net_result), v.validation.trades,
            _q(v.validation.sharpe_per_step, "0.0001"), _q(v.required_sharpe, "0.0001"),
            "–" if t is None else ("nicht widerlegt" if t.passed else "durchgefallen"),
            "–" if t is None else _q(t.test.net_result), "–" if t is None else "%s %%" % _q(t.buy_and_hold_return_pct),
            "–" if r.wf_positive is None else "%s von %d Fenstern" % (_q(r.wf_positive * 100, "1") + " %", r.wf_folds)))
    out += ["", "Gründe der Ablehnungen:", ""]
    for r in rows:
        if not r.validation.passed:
            out.append("* **%s:** %s" % (r.name, "; ".join(r.validation.reasons)))
        elif r.test is not None and not r.test.passed:
            out.append("* **%s (Test):** %s" % (r.name, "; ".join(r.test.reasons)))
    out += ["", "## Nutzen von KIBO/YUZU (Wirtschaftlichkeitsvergleich, AP-33)", ""]
    if agents is None:
        out.append("Nicht gerechnet: zu kurzer Zeitraum für Walk-forward-Fenster.")
    else:
        out.append("Breakout mit regelbasierten KIBO/YUZU gegen neutrale Platzhalter, gepaart je Fenster (%d Fenster): "
                   "Mittel ohne Filter %s, mit Filter %s, Unterschied %s (95 %%: %s bis %s) → **%s**." % (
                       agents.samples, _q(agents.mean_net_cheap), _q(agents.mean_net_expensive),
                       _q(agents.mean_difference), _q(agents.ci_low), _q(agents.ci_high), agents.verdict.value))
    out.append("KI-Zweitmeinungen: nicht gerechnet – ohne freigegebenes KI-Budget (B-004) gibt es keine bezahlten "
               "Aufrufe; ein Vergleich Regeln gegen Regeln+KI folgt erst danach.")
    winners = [r.name for r in rows if r.test is not None and r.test.passed]
    out += ["", "## Ergebnis", ""]
    if winners:
        out.append("Validierung **und** einmaliger Test nach allen Kosten bestanden: %s. Das ist ein Hinweis, kein "
                   "Beweis: Vor einem Paper-Betrieb entscheidet der Eigner; im Register wird nichts automatisch "
                   "freigegeben." % ", ".join(winners))
    else:
        out.append("**Kein belastbarer Vorteil:** Keine Strategie hat Validierung und Test nach allen Kosten "
                   "bestanden. Das ist ein gültiges, ehrliches Ergebnis – es spricht gegen Erwartungen an Gewinne im "
                   "Paper-Betrieb mit diesen Standard-Einstellungen.")
    out += ["", "Grenzen: Standard-Einstellungen ohne Optimierung; Kosten nach dem Kostenmodell (Gebühren, Spread, "
            "Slippage, Funding) ohne Server- und KI-Kosten; Funding-Kurs als Näherung (Kerzen-Schluss).", ""]
    return "\n".join(out)


def _day(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main(argv: Optional[Sequence[str]] = None, http: Optional[Callable[..., Any]] = None,
         out: Callable[[str], None] = print) -> int:
    from trading.clock import SystemClock
    from trading.config import load_settings
    from trading.domain.instrument import Exchange, InstrumentId, MarketType
    from trading.marketdata.bitget import BitgetHistorySource
    from trading.marketdata.coinbase import CoinbaseHistorySource
    from trading.marketdata.daycache import DayCache
    from trading.marketdata.history import ExchangeHistorySource
    from trading.marketdata.public_http import FetchError, PublicHttpGet

    load_settings()
    parser = argparse.ArgumentParser(prog="python -m trading.research.study", description=__doc__.splitlines()[0])
    parser.add_argument("--exchange", choices=["bitget", "coinbase"], required=True)
    parser.add_argument("--market", choices=[m.value for m in MarketType], default="spot")
    parser.add_argument("--symbols", required=True, help="z. B. BTC-USDT,ETH-USDT")
    parser.add_argument("--interval", type=int, default=3600)
    parser.add_argument("--start", type=_day, required=True)
    parser.add_argument("--end", type=_day, required=True)
    parser.add_argument("--cache", type=Path, default=Path("data/marketdata"))
    parser.add_argument("--out", type=Path, default=Path("docs/UNTERSUCHUNG_AP20.md"))
    args = parser.parse_args(argv)
    clock = SystemClock()
    getter = http or PublicHttpGet()
    source: ExchangeHistorySource
    if args.exchange == "bitget":
        source = BitgetHistorySource(getter, clock, DayCache(args.cache))
    else:
        source = CoinbaseHistorySource(getter, clock, DayCache(args.cache))
    instruments = [InstrumentId(Exchange(args.exchange), MarketType(args.market), s.strip())
                   for s in args.symbols.split(",") if s.strip()]
    try:
        dataset = MarketDataset.build([source.load(i, args.interval, args.start, args.end) for i in instruments])
    except (ValueError, FetchError) as exc:  # DataQualityError ist ein ValueError
        out("ABGEBROCHEN - Daten unbrauchbar: %s" % exc)
        return 1 if isinstance(exc, DataQualityError) else 2
    note = "%s %s, %d s, oeffentliche Schnittstelle (nur lesend), Abrufe: %d" % (
        args.exchange, args.market, args.interval, source.requests)
    result = run_study(dataset, note, clock.now())
    args.out.write_text(result.markdown, encoding="utf-8")
    out("Bericht: %s - %s" % (args.out, "bestanden: " + ", ".join(result.winners) if result.winners
                              else "kein belastbarer Vorteil"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
