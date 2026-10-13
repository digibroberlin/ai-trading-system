"""Vergleich mehrerer Backtest-Ergebnisse.

Vergleichbar sind Ergebnisse nur, wenn sie auf DEMSELBEN Datensatz und unter DENSELBEN Annahmen
(Startkapital, Kosten, Sicherheitslimits) entstanden sind. Alles andere wird abgelehnt - ein Vergleich
von Aepfeln mit Birnen sieht in einer Tabelle sonst taeuschend serioes aus.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from trading.backtest.engine import BacktestResult

COLUMNS = (
    "strategy", "label", "return_pct", "net_result", "max_drawdown_pct", "sharpe", "sortino",
    "trades", "win_rate", "profit_factor", "avg_win", "avg_loss", "total_fees", "total_funding",
    "open_positions", "result_hash",
)


def comparison_table(results: Sequence[BacktestResult]) -> List[Dict[str, Any]]:
    """Eine Zeile je Ergebnis, beste Rendite zuerst (bei Gleichstand nach Name - stabile Reihenfolge)."""
    if not results:
        return []
    first = results[0]
    for r in results[1:]:
        if r.dataset_fingerprint != first.dataset_fingerprint:
            raise ValueError("Nicht vergleichbar: %s und %s liefen auf verschiedenen Datensaetzen."
                             % (first.strategy, r.strategy))
        if r.assumptions_fingerprint != first.assumptions_fingerprint:
            raise ValueError("Nicht vergleichbar: %s und %s liefen mit verschiedenem Startkapital, "
                             "Kosten oder Limits." % (first.strategy, r.strategy))
    rows = [{k: r.summary()[k] for k in COLUMNS} for r in results]
    return sorted(rows, key=lambda row: (-row["return_pct"], row["strategy"], row["label"]))
