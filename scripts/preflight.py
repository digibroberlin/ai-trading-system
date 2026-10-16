"""Pruefung vor einem STABLE-Checkpoint:  python scripts/preflight.py [--mutation] [--record]

Fuehrt alle Pruefungen aus, die fuer einen stabilen Stand verlangt sind, und meldet am Ende eindeutig
STABIL oder NICHT STABIL. Ein Stand ist nur STABIL, wenn
  1. der Geheimnis-Scan (Arbeitsstand UND gesamte Git-Geschichte) nichts findet,
  2. ruff (Codequalitaet) und mypy --strict (Typen) ohne Befund sind,
  3. ALLE Tests bestehen und KEINER uebersprungen wurde - also auch die Datenbank-Tests gelaufen sind
     (dafuer muessen TEST_ADMIN_URL und TEST_APP_PASSWORD gesetzt sein, siehe README),
  4. mit --mutation zusaetzlich: die Gegenprobe meldet keinen unentdeckten Fehler.

--record haengt das Ergebnis mit Datum, Git-Stand und Python-Version an docs/TEST_RESULTS.md an.
Rueckgabewert: 0 = STABIL, 1 = NICHT STABIL.
"""

from __future__ import annotations

import datetime as dt
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


LAST_OUTPUT: List[str] = []  # alle Zeilen des letzten Aufrufs (z. B. fuer die Namen fehlgeschlagener Tests)


def run(name: str, cmd: List[str], timeout: int = 3600) -> Tuple[bool, str, float]:
    start = time.monotonic()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env, timeout=timeout)
    lines = [line for line in (proc.stdout + proc.stderr).strip().splitlines() if line.strip()]
    LAST_OUTPUT[:] = lines
    return proc.returncode == 0, lines[-1] if lines else "", time.monotonic() - start


def pytest_summary(line: str) -> Tuple[int, int, int]:
    """(bestanden, uebersprungen, fehlgeschlagen+Fehler) aus der Schlusszeile von pytest."""
    def count(word: str) -> int:
        m = re.search(r"(\d+) %s" % word, line)
        return int(m.group(1)) if m else 0
    return count("passed"), count("skipped"), count("failed") + count("error")


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "?"


def main(argv: List[str]) -> int:
    results: List[Tuple[str, bool, str]] = []

    ok, last, secs = run("secret", [PY, "scripts/secret_scan.py", "--history"])
    results.append(("Geheimnis-Scan (inkl. Geschichte)", ok, last))
    ok, last, secs = run("ruff", [PY, "-m", "ruff", "check", "--no-cache", "src", "tests", "scripts"])
    results.append(("ruff", ok, last))
    ok, last, secs = run("mypy", [PY, "-m", "mypy", "--strict", "--no-incremental", "src"])
    results.append(("mypy --strict", ok, last))

    have_db = bool(os.environ.get("TEST_ADMIN_URL") and os.environ.get("TEST_APP_PASSWORD"))
    ok, last, secs = run("pytest", [PY, "-m", "pytest", "-q", "-rs", "-rfE", "-p", "no:cacheprovider"])
    passed, skipped, failed = pytest_summary(last)
    tests_ok = ok and failed == 0 and skipped == 0 and passed > 0
    # Namen fehlgeschlagener Tests mitnennen - auch seltene Ausreisser muessen nachvollziehbar sein.
    failed_names = [line.split(" - ")[0] for line in LAST_OUTPUT if line.startswith(("FAILED ", "ERROR "))]
    detail = "%d bestanden, %d uebersprungen, %d fehlgeschlagen (%.0f s)%s%s" % (
        passed, skipped, failed, secs, "" if have_db else " - KEINE Datenbank konfiguriert",
        "; " + ", ".join(failed_names) if failed_names else "")
    results.append(("Tests", tests_ok, detail))

    if "--mutation" in argv:
        ok, last, secs = run("mutation", [PY, "scripts/mutation_check.py"], timeout=7200)
        results.append(("Gegenprobe", ok, "%s (%.0f s)" % (last, secs)))

    stable = all(ok for _, ok, _ in results)
    print("\nPRUEFUNG VOR CHECKPOINT")
    for name, ok, detail in results:
        print("  [%s] %-34s %s" % ("OK " if ok else "!! ", name, detail))
    verdict = "STABIL" if stable else "NICHT STABIL"
    print("\nERGEBNIS: %s" % verdict)

    if "--record" in argv:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = ["", "### %s - %s" % (stamp, verdict),
                 "Git: `%s` auf `%s` · Python %s · %s" % (git("rev-parse", "--short", "HEAD"),
                                                          git("rev-parse", "--abbrev-ref", "HEAD"),
                                                          platform.python_version(), platform.system()), ""]
        lines += ["- %s %s: %s" % ("✅" if ok else "❌", name, detail) for name, ok, detail in results]
        with open(ROOT / "docs" / "TEST_RESULTS.md", "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return 0 if stable else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
