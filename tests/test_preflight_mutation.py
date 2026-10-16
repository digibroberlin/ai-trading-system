"""Gegenprobe im Preflight: Die Gruppen laufen gleichzeitig - STABIL nur, wenn jede Gruppe vollstaendig und ohne
unentdeckten Fehler endet; haengt eine Gruppe, beendet das Zeitlimit alle (Stellvertreter statt echter Gegenprobe)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("preflight_under_test", ROOT / "scripts" / "preflight.py")
preflight = importlib.util.module_from_spec(_spec)
sys.modules["preflight_under_test"] = preflight
_spec.loader.exec_module(preflight)

GROUPS = {
    "gut": "print('[A1] x => ENTDECKT'); print(); print('ERGEBNIS: 3 entdeckt, 0 nicht entdeckt, 0 nicht geprueft')",
    "luecke": "print('ERGEBNIS: 1 entdeckt, 1 nicht entdeckt, 0 nicht geprueft'); "
              "print('  fehlender Test fuer: Z9 Beispiel'); raise SystemExit(1)",
    "abbruch": "raise SystemExit('Grundprobe fehlgeschlagen')",
    "haengt": "import time; time.sleep(60)",
    "viel": "print('x' * 200000); print('ERGEBNIS: 2 entdeckt, 0 nicht entdeckt, 0 nicht geprueft')",
}


def stand_in(monkeypatch):
    real = subprocess.Popen

    def popen(cmd, **kwargs):
        assert cmd[1:] == ["scripts/mutation_check.py", cmd[-1]]
        kwargs.pop("cwd")
        return real([sys.executable, "-c", GROUPS[cmd[-1]]], **kwargs)

    monkeypatch.setattr(preflight.subprocess, "Popen", popen)


def test_all_groups_detected_is_stable_and_counts_are_summed(monkeypatch):
    stand_in(monkeypatch)
    ok, detail, _ = preflight.mutation_parallel(("gut", "viel"), 30)
    assert ok and detail == "ERGEBNIS: 5 entdeckt, 0 nicht entdeckt, 0 nicht geprueft (gut 3/3, viel 2/2)"


def test_an_undetected_mutant_or_a_broken_group_is_not_stable(monkeypatch):
    stand_in(monkeypatch)
    ok, detail, _ = preflight.mutation_parallel(("gut", "luecke"), 30)
    assert not ok and "1 nicht entdeckt" in detail and "Z9 Beispiel" in detail
    ok, detail, _ = preflight.mutation_parallel(("gut", "abbruch"), 30)
    assert not ok and "abbruch: Grundprobe fehlgeschlagen" in detail


def test_a_hanging_group_hits_the_time_limit_and_everything_is_stopped(monkeypatch):
    stand_in(monkeypatch)
    start = time.monotonic()
    ok, detail, _ = preflight.mutation_parallel(("gut", "haengt"), 2)
    assert not ok and "Zeitlimit" in detail and "haengt" in detail and time.monotonic() - start < 15


def test_the_groups_are_the_groups_of_the_mutation_check():
    spec = importlib.util.spec_from_file_location("mutation_groups", ROOT / "scripts" / "mutation_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert tuple(module.GROUPS) == preflight.MUTATION_GROUPS
