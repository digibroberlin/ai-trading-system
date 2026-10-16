"""Untersuchung auf Knopfdruck (Vorbereitung AP-20, D-032): Forschungsregeln eingehalten, Testdaten unberuehrt bis zum
einmaligen Test, ehrlicher Bericht - hier mit erfundenen Daten (die echte Untersuchung braucht B-008)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading.backtest.data import MarketDataset, SyntheticDataSource, SyntheticSpec
from trading.domain.instrument import Exchange, InstrumentId, MarketType
from trading.research.splits import split_periods
from trading.research.study import run_study

D = Decimal
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 16, 21, 0, tzinfo=timezone.utc)


def dataset(days=80):
    spot = InstrumentId(Exchange.SYNTHETIC, MarketType.SPOT, "BTC-USD")
    source = SyntheticDataSource(seed=11, specs={spot: SyntheticSpec(start_price=D(30000), volatility=0.006)})
    return MarketDataset.build([source.load(spot, 3600, START, START + timedelta(days=days))])


def test_study_follows_the_research_rules_and_reports_honestly():
    data = dataset()
    result = run_study(data, "erfundene Testdaten", NOW, strategies=("breakout", "momentum"),
                       wf_train=timedelta(days=15), wf_test=timedelta(days=7))
    split = split_periods(data.start, data.end, gap=timedelta(days=2))
    assert result.folds and all(f.test.end <= split.validation.end for f in result.folds)  # nie in den Testzeitraum
    assert [r.name for r in result.rows] == ["breakout", "momentum"]
    for row in result.rows:
        assert (row.test is not None) == row.validation.passed  # Test nur fuer Bestandene
        assert row.wf_folds == len(result.folds) and row.wf_positive is not None
    md = result.markdown
    for part in ("# Untersuchung auf echten historischen Daten (AP-20)", data.fingerprint, "## Strategien",
                 "## Nutzen von KIBO/YUZU", "KI-Zweitmeinungen: nicht gerechnet", "## Ergebnis", "Grenzen:",
                 "Nur Paper-Forschung"):
        assert part in md, part
    if not result.winners:
        assert "Kein belastbarer Vorteil" in md
    assert result.agents is not None and result.agents.samples == len(result.folds)


def test_short_periods_skip_walk_forward_instead_of_inventing_it():
    result = run_study(dataset(days=20), "kurz", NOW, strategies=("breakout",))
    assert result.folds == () and result.agents is None and result.rows[0].wf_positive is None
    assert "zu kurzer Zeitraum" in result.markdown


def test_command_line_refuses_broken_data_and_writes_the_report(tmp_path, monkeypatch):
    from test_marketdata_adapters import FakeBitget

    from trading.research import study

    lines = []
    out = tmp_path / "bericht.md"
    code = study.main(["--exchange", "bitget", "--symbols", "BTC-USDT", "--start", "2026-08-25", "--end", "2026-09-05",
                       "--cache", str(tmp_path / "cache"), "--out", str(out)], http=FakeBitget(), out=lines.append)
    assert code == 0 and out.exists() and lines[0].startswith("Bericht: ")
    text = out.read_text()
    assert "bitget:spot:BTC-USDT" in text and "oeffentliche Schnittstelle (nur lesend)" in text
    broken = FakeBitget()
    broken.code = "40034"
    code = study.main(["--exchange", "bitget", "--symbols", "BTC-USDT", "--start", "2026-08-25", "--end", "2026-09-05",
                       "--cache", str(tmp_path / "leer"), "--out", str(tmp_path / "nie.md")], http=broken,
                      out=lines.append)
    assert code == 2 and lines[-1].startswith("ABGEBROCHEN") and not (tmp_path / "nie.md").exists()
