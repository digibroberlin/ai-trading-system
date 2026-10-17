"""Gegenprobe im Preflight: Teile laufen gleichzeitig - STABIL nur, wenn jeder Teil vollstaendig und ohne unentdeckten
Fehler endet; haengt ein Teil, beendet das Zeitlimit alle (Stellvertreter statt echter Gegenprobe). Die Teile decken
jeden Eintrag genau einmal ab."""

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

    calls = []

    def popen(cmd, **kwargs):
        assert cmd[1] == "scripts/mutation_check.py"
        calls.append(cmd[2:])
        kwargs.pop("cwd")
        return real([sys.executable, "-c", GROUPS[cmd[2]]], **kwargs)

    monkeypatch.setattr(preflight.subprocess, "Popen", popen)
    return calls


def jobs(*names):
    return tuple((name, 1, 1) for name in names)


def test_all_groups_detected_is_stable_and_counts_are_summed(monkeypatch):
    calls = stand_in(monkeypatch)
    ok, detail, _ = preflight.mutation_parallel(jobs("gut", "viel") + (("gut", 2, 3),), 30)
    assert ok and detail == ("ERGEBNIS: 8 entdeckt, 0 nicht entdeckt, 0 nicht geprueft "
                             "(gut 3/3, viel 2/2, gut 2/3 3/3)")
    assert calls == [["gut"], ["viel"], ["gut", "--shard", "2/3"]]


def test_an_undetected_mutant_or_a_broken_group_is_not_stable(monkeypatch):
    stand_in(monkeypatch)
    ok, detail, _ = preflight.mutation_parallel(jobs("gut", "luecke"), 30)
    assert not ok and "1 nicht entdeckt" in detail and "Z9 Beispiel" in detail
    ok, detail, _ = preflight.mutation_parallel(jobs("gut", "abbruch"), 30)
    assert not ok and "abbruch: Grundprobe fehlgeschlagen" in detail


def test_a_hanging_group_hits_the_time_limit_and_everything_is_stopped(monkeypatch):
    stand_in(monkeypatch)
    start = time.monotonic()
    ok, detail, _ = preflight.mutation_parallel(jobs("gut", "haengt"), 2)
    assert not ok and "Zeitlimit" in detail and "haengt" in detail and time.monotonic() - start < 15


def test_the_parts_cover_every_entry_of_every_group_exactly_once():
    spec = importlib.util.spec_from_file_location("mutation_groups", ROOT / "scripts" / "mutation_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert {group for group, _, _ in preflight.MUTATION_JOBS} == set(module.GROUPS)
    for group, mutants in module.GROUPS.items():
        parts = [(part, count) for g, part, count in preflight.MUTATION_JOBS if g == group]
        assert sorted(parts) == [(i, parts[0][1]) for i in range(1, parts[0][1] + 1)]  # 1/n .. n/n, keiner doppelt
        keys = [m[0] for part, count in parts for m in module.shard(mutants, part, count)]
        assert sorted(keys) == sorted(m[0] for m in mutants)
