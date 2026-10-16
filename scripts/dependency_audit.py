"""Sicherheitspruefung der Abhaengigkeiten:  python scripts/dependency_audit.py

Prueft JEDE Paketversion aus jeder Sperrdatei in requirements/ mit pip-audit gegen die oeffentlichen Luecken-Datenbanken
(PyPI/OSV) - unabhaengig von Plattform- und Python-Markern. (pip-audit selbst wertet Marker fuer das laufende Python aus;
unter 3.12 wuerden z. B. die nur fuer Python 3.9 gedachten Pakete aus py39.lock still uebersprungen.)
* Braucht Netzwerk (nur lesend) und pip-audit (Installation: pip install --require-hashes -r requirements/audit.lock).
* Installiert nichts (--disable-pip --no-deps): geprueft werden genau die eingetragenen Versionen.
* Bewusst hingenommene Luecken stehen in ACCEPTED - jede mit Begruendung und Verweis auf docs/DECISIONS.md.
  Sie gelten nur fuer die genannte Sperrdatei; dieselbe Luecke anderswo bleibt ein Fund.

Rueckgabewert: 0 = keine (unbegruendeten) Luecken, 1 = Luecken gefunden, 2 = Pruefung nicht (vollstaendig) ausfuehrbar
(pip-audit fehlt, kein Netz, Paket nicht pruefbar). 2 gilt nie als bestanden.
Nicht Teil von preflight.py, weil preflight ohne Netzwerk laufen muss; GitHub Actions fuehrt es bei jedem Lauf aus.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "requirements"
LOCKS = ("dev", "runtime", "py39", "audit")
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;\\]+)")

# Sperrdatei -> {Luecken-ID: Begruendung}
ACCEPTED: Dict[str, Dict[str, str]] = {
    "py39": {
        # CVE-2025-71176: pytest bis 9.0.2 nutzt /tmp/pytest-of-<user>; ein ANDERER lokaler Benutzer koennte das stoeren.
        # py39.lock laeuft nur auf einer frischen Wegwerf-Maschine von GitHub Actions ohne weitere Benutzer.
        # pytest 9 unterstuetzt Python 3.9 nicht mehr (D-010).
        "PYSEC-2026-1845": "nur Wegwerf-CI-Maschine, pytest 9 laeuft nicht auf 3.9 (D-010)",
    },
}


class AuditUnavailable(RuntimeError):
    """Die Pruefung konnte nicht (vollstaendig) laufen."""


def locked_versions(lock: Path) -> List[Tuple[str, str]]:
    """Alle (Paket, Version) der Sperrdatei - auch die, deren Marker fuer das laufende Python nicht gelten."""
    found: List[Tuple[str, str]] = []
    for line in lock.read_text(encoding="utf-8").splitlines():
        m = _PIN.match(line)
        if m:
            found.append((re.sub(r"[-_.]+", "-", m.group(1)).lower(), m.group(2)))
    return sorted(set(found))


def batches(pins: List[Tuple[str, str]]) -> List[List[Tuple[str, str]]]:
    """Teilt die Liste so, dass kein Paketname in einer Gruppe doppelt vorkommt (pip-audit verlangt das)."""
    groups: List[List[Tuple[str, str]]] = []
    for pin in pins:
        for group in groups:
            if all(name != pin[0] for name, _ in group):
                group.append(pin)
                break
        else:
            groups.append([pin])
    return groups


def pip_audit_cmd() -> List[str]:
    exe = shutil.which("pip-audit")
    if exe:
        return [exe]
    if importlib.util.find_spec("pip_audit") is not None:
        return [sys.executable, "-m", "pip_audit"]
    raise AuditUnavailable("pip-audit ist nicht installiert (pip install --require-hashes -r requirements/audit.lock).")


def audit_group(cmd: List[str], group: List[Tuple[str, str]]) -> Dict[Tuple[str, str], Set[str]]:
    """{(Paket, Version): {Luecken-IDs samt Aliasen}} fuer eine Gruppe."""
    with tempfile.TemporaryDirectory(prefix="dep-audit-") as tmp:
        req = Path(tmp) / "pins.txt"
        req.write_text("".join("%s==%s\n" % pin for pin in group), encoding="utf-8")
        try:
            proc = subprocess.run(
                cmd + ["--disable-pip", "--no-deps", "--progress-spinner", "off", "-f", "json", "-r", str(req)],
                capture_output=True, text=True, timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AuditUnavailable("pip-audit nicht ausfuehrbar: %s" % exc) from exc
    try:
        report = json.loads(proc.stdout)
        deps = report["dependencies"] if isinstance(report, dict) else report
    except (ValueError, KeyError, TypeError) as exc:
        detail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["keine Ausgabe"]
        raise AuditUnavailable("pip-audit lieferte kein Ergebnis (Netz?): %s" % detail[0]) from exc
    result: Dict[Tuple[str, str], Set[str]] = {}
    for dep in deps:
        key = (re.sub(r"[-_.]+", "-", str(dep.get("name", ""))).lower(), str(dep.get("version", "")))
        if dep.get("skip_reason"):
            raise AuditUnavailable("%s %s nicht pruefbar: %s" % (key[0], key[1], dep["skip_reason"]))
        ids: Set[str] = set()
        for vuln in dep.get("vulns", []):
            ids.add(str(vuln.get("id")))
            ids.update(str(a) for a in vuln.get("aliases", []))
        result[key] = ids
    missing = [pin for pin in group if pin not in result]
    if missing:
        raise AuditUnavailable("nicht geprueft: %s" % ", ".join("%s==%s" % p for p in missing))
    return result


def audit_lock(cmd: List[str], name: str) -> Tuple[List[str], List[str]]:
    """(unbegruendete Funde, hingenommene Funde) einer Sperrdatei als lesbare Zeilen."""
    lock = REQ / ("%s.lock" % name)
    if not lock.is_file():
        raise AuditUnavailable("Sperrdatei fehlt: %s" % lock.relative_to(ROOT))
    accepted = ACCEPTED.get(name, {})
    findings: List[str] = []
    tolerated: List[str] = []
    for group in batches(locked_versions(lock)):
        for (pkg, version), ids in sorted(audit_group(cmd, group).items()):
            if not ids:
                continue
            line = "%s %s: %s" % (pkg, version, ", ".join(sorted(ids)))
            (tolerated if ids & set(accepted) else findings).append(line)
    return findings, tolerated


def main(argv: Optional[List[str]] = None) -> int:
    try:
        cmd = pip_audit_cmd()
        failed = False
        for name in LOCKS:
            findings, tolerated = audit_lock(cmd, name)
            note = "; bewusst hingenommen: %s" % " | ".join(tolerated) if tolerated else ""
            if findings:
                failed = True
                print("[FUND] %-8s %s%s" % (name, " | ".join(findings), note))
            else:
                print("[OK ] %-8s keine unbegruendeten Luecken%s" % (name, note))
    except AuditUnavailable as exc:
        print("[NICHT AUSFUEHRBAR] %s" % exc)
        print("\nERGEBNIS: PRUEFUNG NICHT VOLLSTAENDIG - zaehlt nicht als bestanden.")
        return 2
    if failed:
        print("\nERGEBNIS: LUECKEN GEFUNDEN - Version anheben (requirements/*.txt, update_locks.sh) oder begruenden.")
        return 1
    print("\nERGEBNIS: keine unbegruendeten Luecken.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
