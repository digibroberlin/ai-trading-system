"""Jeder Eintrag der Gegenprobe (scripts/mutation_check.py) muss genau einmal auf den aktuellen Code passen.

Sonst bricht die Gegenprobe erst nach langer Laufzeit ab - oder, schlimmer, ein Eintrag prueft unbemerkt nichts mehr.
Schnell, ohne Datenbank; laeuft deshalb in jedem Preflight mit.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mutation_check", ROOT / "scripts" / "mutation_check.py")
assert _spec is not None and _spec.loader is not None
mutation_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutation_check)

ENTRIES = [(key, path, old, new) for group in mutation_check.GROUPS.values()
           for key, _, edits, _ in group for path, old, new in edits]


def test_keys_are_unique():
    keys = [key for group in mutation_check.GROUPS.values() for key, _, _, _ in group]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("key, path, old, new", ENTRIES, ids=[e[0] for e in ENTRIES])
def test_entry_matches_the_code_exactly_once(key, path, old, new):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert text.count(old) == 1, "%s: Textstelle in %s %d-mal gefunden" % (key, path, text.count(old))
    assert old != new, key
