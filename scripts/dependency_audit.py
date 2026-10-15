"""Sicherheitspruefung der Abhaengigkeiten:  python scripts/dependency_audit.py

Prueft jede Sperrdatei in requirements/ mit pip-audit gegen die oeffentlichen Luecken-Datenbanken (PyPI/OSV).
* Braucht Netzwerk (nur lesend) und pip-audit (Installation: pip install --require-hashes -r requirements/audit.lock).
* Installiert nichts: --disable-pip prueft nur die festen Versionen und Pruefsummen aus der Sperrdatei.
* Bewusst hingenommene Luecken stehen in ACCEPTED - jede mit Begruendung und Verweis auf docs/DECISIONS.md.
  Sie gelten nur fuer die genannte Sperrdatei; dieselbe Luecke anderswo bleibt ein Fund.

Rueckgabewert: 0 = keine (unbegruendeten) Luecken, 1 = Luecken gefunden, 2 = Pruefung nicht ausfuehrbar.
Nicht Teil von preflight.py, weil preflight ohne Netzwerk laufen muss; GitHub Actions fuehrt es bei jedem Lauf aus.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "requirements"
LOCKS = ("dev", "runtime", "py39", "audit")

# Sperrdatei -> {Luecken-ID: Begruendung}
ACCEPTED: Dict[str, Dict[str, str]] = {
    "py39": {
        # CVE-2025-71176: pytest bis 9.0.2 nutzt /tmp/pytest-of-<user>; ein ANDERER lokaler Benutzer koennte das stoeren.
        # py39.lock laeuft nur auf einer frischen Wegwerf-Maschine von GitHub Actions ohne weitere Benutzer.
        # pytest 9 unterstuetzt Python 3.9 nicht mehr (D-010).
        "PYSEC-2026-1845": "nur Wegwerf-CI-Maschine, pytest 9 laeuft nicht auf 3.9 (D-010)",
    },
}


def pip_audit_cmd() -> List[str]:
    exe = shutil.which("pip-audit")
    if exe:
        return [exe]
    return [sys.executable, "-m", "pip_audit"]


def main() -> int:
    cmd = pip_audit_cmd()
    failed = False
    for name in LOCKS:
        lock = REQ / ("%s.lock" % name)
        if not lock.is_file():
            print("[FEHLT] %s" % lock.relative_to(ROOT))
            return 2
        args = cmd + ["--disable-pip", "--require-hashes", "--progress-spinner", "off", "-r", str(lock)]
        for vuln_id in sorted(ACCEPTED.get(name, {})):
            args += ["--ignore-vuln", vuln_id]
        try:
            proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print("[NICHT AUSFUEHRBAR] %s: %s" % (name, exc))
            return 2
        out = (proc.stdout + proc.stderr).strip()
        if proc.returncode == 0:
            accepted = ", ".join(sorted(ACCEPTED.get(name, {}))) or "keine"
            print("[OK ] %-8s keine bekannten Luecken (bewusst hingenommen: %s)" % (name, accepted))
        else:
            failed = True
            print("[FUND] %s\n%s" % (name, out))
    if failed:
        print("\nERGEBNIS: LUECKEN GEFUNDEN - Version anheben (requirements/*.txt, update_locks.sh) oder begruenden.")
        return 1
    print("\nERGEBNIS: keine unbegruendeten Luecken.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
