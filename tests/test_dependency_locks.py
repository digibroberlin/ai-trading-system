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
def test_every_input_line_is_pinned(name):
    """Jede Vorgabe ist exakt festgelegt (==). Sonst koennte eine Sperrdatei veralten, ohne dass ein Test es merkt."""
    for line in (REQ / ("%s.txt" % name)).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            assert _PIN.match(line), "nicht exakt festgelegt in %s.txt: %s" % (name, line)


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


_INSTALL = re.compile(r"\b(?:pip3?|python3?(?:\.\d+)? -m pip|uv pip)\s+install\b")


def _install_commands(text):
    """Jede Installation einzeln (auch mehrere in einer Zeile oder mit && verbunden), bis zum Ende des Befehls."""
    commands = []
    for m in _INSTALL.finditer(text):
        rest = text[m.start():]
        commands.append(re.split(r"&&|;|\|\||\n", rest, maxsplit=1)[0])
    return commands


@pytest.mark.parametrize("path", ["Dockerfile", ".github/workflows/ci.yml"])
def test_installs_only_from_hashed_locks(path):
    """Jede pip-Installation in Image und CI nutzt eine Sperrdatei mit --require-hashes."""
    text = (ROOT / path).read_text(encoding="utf-8")
    installs = _install_commands(text)
    assert installs
    for cmd in installs:
        assert "--require-hashes" in cmd and re.search(r"requirements/\w+\.lock", cmd), cmd


@pytest.mark.parametrize("text", [
    "RUN pip3 install foo",
    "RUN python -m pip install -r requirements/dev.txt",
    "RUN pip install --require-hashes -r requirements/dev.lock && pip install bar",
    "run: uv pip install baz",
    "RUN python3.12 -m pip install x; pip install --require-hashes -r requirements/dev.lock",
])
def test_install_check_catches_every_unchecked_form(text):
    assert any(not ("--require-hashes" in c and re.search(r"requirements/\w+\.lock", c))
               for c in _install_commands(text)), text


# ------------------------------------------------------------------ scripts/dependency_audit.py (ohne Netzwerk)


def _audit_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("dependency_audit", ROOT / "scripts" / "dependency_audit.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_covers_packages_for_other_pythons_and_platforms():
    """Auch Eintraege mit Markern (nur Python 3.9, nur Windows) werden geprueft - nicht nur die fuer das laufende Python."""
    audit = _audit_module()
    pins = audit.locked_versions(REQ / "py39.lock")
    assert ("iniconfig", "2.1.0") in pins and ("iniconfig", "2.3.1") in pins  # 3.9 UND neuer
    assert ("exceptiongroup", "1.3.1") in pins and ("colorama", "0.4.6") in pins
    for group in audit.batches(pins):
        names = [name for name, _ in group]
        assert len(names) == len(set(names))
    assert sorted(p for g in audit.batches(pins) for p in g) == pins


def test_audit_reports_not_runnable_instead_of_findings(monkeypatch, capsys):
    audit = _audit_module()
    monkeypatch.setattr(audit.shutil, "which", lambda name: None)
    monkeypatch.setattr(audit.importlib.util, "find_spec", lambda name: None)
    assert audit.main() == 2
    assert "NICHT VOLLSTAENDIG" in capsys.readouterr().out


def test_audit_treats_unparsable_output_as_not_runnable(monkeypatch):
    audit = _audit_module()

    class Proc:
        stdout, stderr, returncode = "", "ConnectionError: kein Netz", 1

    monkeypatch.setattr(audit.subprocess, "run", lambda *a, **k: Proc())
    with pytest.raises(audit.AuditUnavailable, match="kein Netz"):
        audit.audit_group(["pip-audit"], [("pytest", "9.0.3")])


def test_audit_separates_accepted_from_new_findings(monkeypatch):
    audit = _audit_module()
    report = {"dependencies": [
        {"name": "pytest", "version": "8.4.2", "vulns": [{"id": "PYSEC-2026-1845", "aliases": ["CVE-2025-71176"]}]},
        {"name": "pygments", "version": "2.21.0", "vulns": [{"id": "GHSA-neu", "aliases": []}]},
    ]}

    class Proc:
        stdout, stderr, returncode = __import__("json").dumps(report), "", 1

    monkeypatch.setattr(audit.subprocess, "run", lambda *a, **k: Proc())
    monkeypatch.setattr(audit, "locked_versions", lambda lock: [("pygments", "2.21.0"), ("pytest", "8.4.2")])
    findings, tolerated = audit.audit_lock(["pip-audit"], "py39")
    assert findings == ["pygments 2.21.0: GHSA-neu"]
    assert tolerated and tolerated[0].startswith("pytest 8.4.2")
    findings, _ = audit.audit_lock(["pip-audit"], "dev")  # dieselbe Luecke in einer anderen Sperrdatei: ein Fund
    assert any(f.startswith("pytest 8.4.2") for f in findings)
