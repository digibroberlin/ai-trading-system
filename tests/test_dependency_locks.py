"""Die Sperrdateien in requirements/ muessen vollstaendig, mit Pruefsummen und passend zu den Vorgaben sein (Audit M12).

Laeuft ohne Netzwerk. Die Pruefung auf bekannte Luecken (pip-audit) braucht Netzwerk und steht in
scripts/dependency_audit.py; sie laeuft in GitHub Actions.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "requirements"
NAMES = ("dev", "runtime", "py39", "audit")

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==([^\s;\\]+)")


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(path):
    """{Paketname: Version} aus einer .txt- oder .lock-Datei (Zeilen am Zeilenanfang)."""
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _PIN.match(line)
        if m:
            pins.setdefault(_norm(m.group(1)), set()).add(m.group(3))
    return pins


def _lock_entries(path):
    """Liste von (Kopfzeile, Anzahl sha256-Pruefsummen) je Paket-Eintrag der Sperrdatei."""
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            entries.append([line, 0])
        elif "--hash=" in line:
            assert entries, "Pruefsumme ohne Paket in %s" % path.name
            assert re.search(r"--hash=sha256:[0-9a-f]{64}\b", line), line
            entries[-1][1] += 1
    return entries


@pytest.mark.parametrize("name", NAMES)
def test_every_lock_entry_is_pinned_and_hashed(name):
    entries = _lock_entries(REQ / ("%s.lock" % name))
    assert entries
    for head, hashes in entries:
        assert _PIN.match(head), "nicht exakt festgelegt: %s" % head
        assert hashes >= 1, "keine Pruefsumme: %s" % head


@pytest.mark.parametrize("name", NAMES)
def test_lock_matches_its_input(name):
    wanted = _pins(REQ / ("%s.txt" % name))
    locked = _pins(REQ / ("%s.lock" % name))
    assert wanted, "keine festen Versionen in %s.txt" % name
    for pkg, versions in wanted.items():
        assert locked.get(pkg) == versions, "%s: Vorgabe %s, Sperrdatei %s -> update_locks.sh" % (
            pkg, versions, locked.get(pkg))


def test_no_unlocked_requirement_files():
    txt = {p.stem for p in REQ.glob("*.txt")}
    lock = {p.stem for p in REQ.glob("*.lock")}
    assert txt == lock == set(NAMES)
    assert not list(ROOT.glob("requirements*.txt")), "Abhaengigkeiten gehoeren nach requirements/"


def test_runtime_uses_same_database_driver_as_tests():
    assert _pins(REQ / "runtime.lock")["psycopg"] == _pins(REQ / "dev.lock")["psycopg"]


def test_core_has_no_dependencies():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r"^dependencies = \[\]$", text, re.M), "der Kern muss ohne externe Pakete auskommen"


@pytest.mark.parametrize("path", ["Dockerfile", ".github/workflows/ci.yml"])
def test_installs_only_from_hashed_locks(path):
    """Jede pip-Installation in Image und CI nutzt eine Sperrdatei mit --require-hashes."""
    text = (ROOT / path).read_text(encoding="utf-8")
    installs = re.findall(r"pip install\b[^\n]*", text)
    assert installs
    for cmd in installs:
        assert "--require-hashes" in cmd and re.search(r"requirements/\w+\.lock", cmd), cmd
