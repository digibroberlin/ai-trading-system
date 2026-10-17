"""Tabellen der Untersuchungsreihe AP-20 aus den Ergebnisdateien (Markdown) - reine Auswertung, keine neuen Laeufe.

Ausnahme: Kaufen-und-Halten wird je Abschnitt und Szenario frisch gerechnet (analytisch, gleiche Kosten).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from trading.research.campaign import PERIODS, SCENARIOS, Bundle
from trading.research.robust import (
    Interval,
    block_bootstrap_mean,
    daily_equity,
    daily_pnl,
    deflated_sharpe_ok,
    max_drawdown,
    paired_difference,
    returns_of,
    sharpe,
)
from trading.strategies.benchmarks import buy_and_hold

Row = Dict[str, Any]
PERIOD_ORDER = ("training", "validierung", "test")
VARIANT_ORDER = ("V0", "A", "B", "C", "D", "S")
INITIAL = 10000.0


def load_results(root: Path) -> List[Row]:
    rows: List[Row] = []
    for path in sorted(root.glob("*/*/*.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


def _f(value: Any, digits: int = 2, pct: bool = False) -> str:
    if value is None or value == "":
        return "–"
    number = float(value)
    return ("%." + str(digits) + "f%s") % (number, " %" if pct else "")


def _money(value: Any) -> str:
    return "–" if value is None else "{:,.0f}".format(float(value)).replace(",", ".")


def _order(rows: Iterable[Row]) -> List[Row]:
    def key(r: Row) -> Tuple[int, str, str, int, str]:
        v = r["variant"]
        return (PERIOD_ORDER.index(r["period"]) if r["period"] in PERIOD_ORDER else 9, r["scenario"], r["strategy"],
                VARIANT_ORDER.index(v) if v in VARIANT_ORDER else 9, v)
    return sorted(rows, key=key)


def daily_of(row: Row) -> List[Tuple[date, float]]:
    return [(date.fromisoformat(d), float(e)) for d, e in row["daily"]]


def pnl_of(row: Row) -> List[Tuple[date, float]]:
    return daily_pnl(daily_of(row), INITIAL)


# ---------------------------------------------------------------------------------------------- Tabellen

def table(headers: Sequence[str], body: Iterable[Sequence[str]]) -> List[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(cells) + " |" for cells in body]
    return out


def strategy_table(rows: Sequence[Row]) -> List[str]:
    body = []
    for r in _order(rows):
        c = r["card"]
        body.append((r["strategy"], r["variant"], str(c["trades"]), _money(c["trading_net"]), _money(c["economic_net"]),
                     _f(c["cagr_pct"], 1), _f(c["max_drawdown_pct"], 1), _f(c["sharpe"]), _f(c["calmar"]),
                     _f(c["profit_factor"]), _f(c["win_rate"] * 100, 0), _f(c["expectancy"]), _f(c["avg_win"]),
                     _f(c["avg_loss"])))
    return table(("Strategie", "Var.", "Trades", "Netto A", "Netto B", "CAGR %", "max DD %", "Sharpe", "Calmar", "PF",
                  "Treffer %", "Ø je Trade", "Ø Gewinn", "Ø Verlust"), body)


def agent_table(rows: Sequence[Row]) -> List[str]:
    body = []
    for r in _order(rows):
        c = r["card"]
        st = c["by_stage"]
        lat = c["latency_us"].get("ENTSCHEIDUNG_GESAMT", {})
        body.append((r["strategy"], r["variant"], str(c["signals"]), str(st.get("FILLED", 0)), str(st.get("RIN", 0)),
                     str(st.get("SORA", 0)), str(st.get("KIBO", 0)), str(st.get("YUZU", 0)),
                     str(st.get("EXECUTOR", 0) + st.get("PIPELINE", 0)), str(c["ai_reviews"]),
                     _f(c["ai_cost_typical"], 3), _f(c["ai_cost_ceiling"], 3), _f(lat.get("mean_us"), 0),
                     "ja" if c["halted_at"] else "nein"))
    return table(("Strategie", "Var.", "Signale", "ausgeführt", "RIN", "SORA", "KIBO", "YUZU", "Executor",
                  "KI-Anfragen", "KI typ. $", "KI max. $", "Ø Latenz µs", "Not-Aus"), body)


def cost_table(rows: Sequence[Row]) -> List[str]:
    body = []
    for r in _order(rows):
        c = r["card"]
        body.append((r["strategy"], r["variant"], _money(c["gross_before_costs"]), _money(c["fees"]),
                     _money(c["spread_slippage"]), _money(c["funding"]), _money(c["trading_net"]),
                     _money(c["operating_cost"]), _f(c["ai_cost_typical"], 3), _money(c["economic_net"])))
    return table(("Strategie", "Var.", "vor Kosten", "Gebühren", "Spread+Slippage*", "Funding", "Netto A", "Betrieb",
                  "KI", "Netto B"), body)


def side_table(rows: Sequence[Row]) -> List[str]:
    body = []
    for r in _order(rows):
        c = r["card"]
        body.append((r["strategy"], r["variant"], str(c["long_trades"]), _money(c["long_net"]), str(c["short_trades"]),
                     _money(c["short_net"])))
    return table(("Strategie", "Var.", "Long Trades", "Long Netto", "Short Trades", "Short Netto"), body)


def regime_table(rows: Sequence[Row]) -> List[str]:
    regimes = sorted({k for r in rows for k in r["card"]["by_regime"]})
    body = []
    for r in _order(rows):
        cells = [r["strategy"], r["variant"]]
        for k in regimes:
            n, net = r["card"]["by_regime"].get(k, (0, "0"))
            cells.append("%s (%d)" % (_money(net), n) if n else "–")
        body.append(cells)
    return table(["Strategie", "Var."] + regimes, body)


def year_table(rows: Sequence[Row]) -> List[str]:
    years = sorted({k for r in rows for k in r["card"]["by_year"]})
    body = []
    for r in _order(rows):
        cells = [r["strategy"], r["variant"]]
        for y in years:
            n, net = r["card"]["by_year"].get(y, (0, "0"))
            cells.append("%s (%d)" % (_money(net), n) if n else "–")
        body.append(cells)
    return table(["Strategie", "Var."] + years, body)


def _ci(interval: Optional[Interval]) -> str:
    if interval is None:
        return "zu wenig Daten"
    return "%+.2f [%+.2f; %+.2f]" % (interval.estimate, interval.low, interval.high)


def paired_table(rows: Sequence[Row]) -> List[str]:
    """Variante gegen V0: mittlerer Unterschied des Tagesergebnisses (USD/Tag) mit 95-%-Block-Bootstrap."""
    by = {(r["strategy"], r["variant"]): r for r in rows}
    body = []
    for strategy in sorted({r["strategy"] for r in rows}):
        base = by.get((strategy, "V0"))
        if base is None:
            continue
        cells = [strategy]
        for v in ("A", "B", "C"):
            other = by.get((strategy, v))
            cells.append("–" if other is None else _ci(paired_difference(pnl_of(other), pnl_of(base))))
        body.append(cells)
    return table(("Strategie", "A − V0 (USD/Tag)", "B − V0", "C − V0"), body)


def shadow_table(rows: Sequence[Row]) -> List[str]:
    """Variante S: Netto der Trades, die KIBO bzw. YUZU verhindert haetten (negativ = Filter haette geholfen)."""
    body = []
    for r in _order([r for r in rows if r["variant"] == "S"]):
        trades = r["trades"]
        cells = [r["strategy"], str(len(trades))]
        for field in ("kibo_would_approve", "yuzu_would_approve"):
            blocked = [float(t["net"]) for t in trades if t[field] is False]
            kept = [float(t["net"]) for t in trades if t[field] is True]
            mean_blocked = block_bootstrap_mean(blocked, block=1) if len(blocked) >= 2 else None
            cells += [str(len(blocked)), _money(sum(blocked)) if blocked else "–",
                      _ci(mean_blocked) if blocked else "–",
                      _f(sum(kept) / len(kept)) if kept else "–"]
        body.append(cells)
    return table(("Strategie", "Trades", "KIBO hätte blockiert", "deren Netto", "Ø je Trade [95 %]", "Ø übrige",
                  "YUZU hätte blockiert", "deren Netto", "Ø je Trade [95 %]", "Ø übrige"), body)


def benchmark_table(bundle: Bundle, period_name: str, scenario_keys: Sequence[str]) -> List[str]:
    period = PERIODS[period_name]
    body = []
    for key in scenario_keys:
        scenario = SCENARIOS[key]
        data = bundle.dataset(scenario).window(period.start, period.end)
        hold = buy_and_hold(data, Decimal(int(INITIAL)), scenario.costs)
        daily = daily_equity(hold.equity_curve)
        rets = returns_of(daily, INITIAL)
        body.append((key, scenario.title, _f(hold.return_pct, 1), _f(max_drawdown(daily, INITIAL), 1), _f(sharpe(rets)),
                     _money(hold.final_equity - Decimal(int(INITIAL))), _money(hold.funding)))
    return table(("Szenario", "Kaufen-und-Halten", "Rendite %", "max DD %", "Sharpe", "Netto", "Funding"), body)


def validation_table(rows: Sequence[Row], trials: int) -> List[str]:
    """Kriterien aus docs/AP20_PROTOKOLL.md (Abschnitt 8) fuer jede Validierungszelle der Szenarien F und S."""
    by = {(r["scenario"], r["strategy"], r["variant"]): r for r in rows}
    body = []
    for r in _order([r for r in rows if r["scenario"] in ("F", "S") and r["variant"] != "S"]):
        c = r["card"]
        stress = by.get((r["scenario"] + "x", r["strategy"], r["variant"]))
        reasons = _criteria(r, stress, trials)
        body.append((r["scenario"], r["strategy"], r["variant"], _money(c["trading_net"]), _money(c["economic_net"]),
                     "–" if stress is None else _money(stress["card"]["trading_net"]), str(c["trades"]),
                     "**Kandidat**" if not reasons else "; ".join(reasons)))
    return table(("Sz.", "Strategie", "Var.", "Netto A", "Netto B", "Stress A", "Trades", "Ergebnis"), body)


POST_CUTOFF = date(2026, 7, 1)  # Testteil nach dem Wissensstand des Entwicklers (Protokoll, Abschnitt 2)


def _criteria(row: Row, stress: Optional[Row], trials: int, min_trades: int = 30) -> List[str]:
    c = row["card"]
    reasons = []
    if not (float(c["trading_net"]) > 0 and float(c["economic_net"]) > 0):
        reasons.append("Netto ≤ 0")
    if c["trades"] < min_trades:
        reasons.append("< %d Trades" % min_trades)
    if stress is None:
        reasons.append("Stress fehlt")
    elif float(stress["card"]["trading_net"]) <= 0:
        reasons.append("Stress ≤ 0")
    ok, sr, required = deflated_sharpe_ok(returns_of(daily_of(row), INITIAL), trials)
    if not ok:
        reasons.append("Sharpe/Tag %s < %s" % (_f(sr, 4), _f(required, 4)))
    return reasons


def classification_table(rows: Sequence[Row], trials: int) -> List[str]:
    """Einstufung nach Protokoll Abschnitt 8 je Szenario (F, S), Strategie und Variante (ohne Schatten)."""
    by = {(r["period"], r["scenario"], r["strategy"], r["variant"]): r for r in rows}
    keys = sorted({(r["scenario"], r["strategy"], r["variant"]) for r in rows
                   if r["scenario"] in ("F", "S") and r["variant"] != "S" and r["period"] in ("validierung", "test")})
    body = []
    for scenario, strategy, variant in keys:
        val = by.get(("validierung", scenario, strategy, variant))
        test = by.get(("test", scenario, strategy, variant))
        val_stress = by.get(("validierung", scenario + "x", strategy, variant))
        test_stress = by.get(("test", scenario + "x", strategy, variant))
        cand = val is not None and not _criteria(val, val_stress, trials)
        test_ok = (test is not None and float(test["card"]["trading_net"]) > 0 and float(test["card"]["economic_net"]) > 0
                   and test_stress is not None and float(test_stress["card"]["trading_net"]) > 0)
        both = [r for r in (val, test) if r is not None]
        ci = combined_interval(both) if len(both) == 2 else None
        post = None
        if test is not None:
            post = sum(v for d, v in pnl_of(test) if d >= POST_CUTOFF)
        if val is None or test is None:
            level = "noch nicht überprüft"
        elif cand and test_ok and ci is not None and ci.low > 0:
            level = "**statistisch ausreichend belastbar**"
        elif float(val["card"]["economic_net"]) > 0 and float(test["card"]["economic_net"]) > 0:
            level = "historisch profitabel (Validierung und Test), nicht belastbar"
        elif float(test["card"]["economic_net"]) > 0 or float(val["card"]["economic_net"]) > 0:
            level = "nur in einem Abschnitt profitabel"
        else:
            level = "nicht profitabel"
        body.append((scenario, strategy, variant,
                     "–" if val is None else _money(val["card"]["economic_net"]),
                     "ja" if cand else "nein",
                     "–" if test is None else _money(test["card"]["economic_net"]),
                     "–" if test_stress is None else _money(test_stress["card"]["trading_net"]),
                     _ci(ci), "–" if post is None else _money(post), level))
    return table(("Sz.", "Strategie", "Var.", "Netto B Validierung", "Kandidat", "Netto B Test", "Stress A Test",
                  "Ø USD/Tag Val+Test [95 %]", "Test ab 2026-07-01", "Einstufung"), body)


def write_report(results: Path, out: Path, bundle: Bundle) -> Path:
    rows = load_results(results)
    lines: List[str] = ["# AP-20 – Ergebnistabellen (automatisch erzeugt)", "",
                        "Erzeugt aus `%s` mit `python -m trading.research.ap20 report`. Geldbeträge in USD bei 10 000 USD "
                        "Startkapital je Abschnitt; *Spread+Slippage geschätzt aus dem Kostenmodell (in den Kursen "
                        "enthalten)." % results, "",
                        "Datenquelle:", ""] + ["* " + line for line in bundle.spot.provenance.lines()] + [""]
    validation_trials = len([r for r in rows if r["period"] == "validierung" and r["scenario"] in ("F", "S")
                             and r["variant"] != "S"])
    grouped: Dict[Tuple[str, str], List[Row]] = defaultdict(list)
    for r in rows:
        grouped[(r["period"], r["scenario"])].append(r)
    for period in PERIOD_ORDER:
        scenarios = sorted({s for p, s in grouped if p == period})
        if not scenarios:
            continue
        p = PERIODS[period]
        lines += ["## Abschnitt %s (%s bis %s)" % (period, p.start.date(), p.end.date()), "",
                  "### Vergleichsmaßstab Kaufen-und-Halten", ""]
        lines += benchmark_table(bundle, period, [s for s in scenarios if s in SCENARIOS]) + [""]
        for scenario in scenarios:
            part = grouped[(period, scenario)]
            lines += ["### Szenario %s – %s" % (scenario, SCENARIOS[scenario].title if scenario in SCENARIOS else ""),
                      "", "**Ergebnis je Strategie und Variante**", ""] + strategy_table(part) + [""]
            lines += ["**Agenten: Signale, Blockaden je Stufe, KI-Anfragen (Szenario D), Latenz**", ""]
            lines += agent_table(part) + [""]
            lines += ["**Kosten**", ""] + cost_table(part) + [""]
            lines += ["**Long/Short**", ""] + side_table(part) + [""]
            lines += ["**Marktphase beim Einstieg: Netto (Trades)**", ""] + regime_table(part) + [""]
            lines += ["**Jahre: Netto (Trades)**", ""] + year_table(part) + [""]
            lines += ["**Agentennutzen gepaart (gleiche Tage), Mittel und 95-%-Block-Bootstrap**", ""]
            lines += paired_table(part) + [""]
            if any(r["variant"] == "S" for r in part):
                lines += ["**Schatten (Variante S): Trades, die KIBO/YUZU verhindert hätten**", ""]
                lines += shadow_table(part) + [""]
        if period == "validierung":
            lines += ["### Validierungskriterien (N = %d Versuche)" % max(1, validation_trials), ""]
            lines += validation_table([r for r in rows if r["period"] == period], max(1, validation_trials)) + [""]
    if any(r["period"] == "test" for r in rows):
        lines += ["## Einstufung nach Protokoll (Validierung + Test, N = %d)" % max(1, validation_trials), ""]
        lines += classification_table(rows, max(1, validation_trials)) + [""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def combined_interval(rows: Sequence[Row], block: int = 20) -> Optional[Interval]:
    """Mittleres Tagesergebnis ueber mehrere Abschnitte (z. B. Validierung + Test) mit Block-Bootstrap."""
    values = [v for r in rows for _, v in pnl_of(r)]
    return block_bootstrap_mean(values, block=block)


__all__ = ["combined_interval", "load_results", "write_report"]
