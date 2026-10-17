"""Pruefung vor einem STABLE-Checkpoint:  python scripts/preflight.py [--mutation] [--record]

Fuehrt alle Pruefungen aus, die fuer einen stabilen Stand verlangt sind, und meldet am Ende eindeutig
STABIL oder NICHT STABIL. Ein Stand ist nur STABIL, wenn
  1. der Geheimnis-Scan (Arbeitsstand UND gesamte Git-Geschichte) nichts findet,
  2. ruff (Codequalitaet) und mypy --strict (Typen) ohne Befund sind,
  3. ALLE Tests bestehen und KEINER uebersprungen wurde - also auch die Datenbank-Tests gelaufen sind
     (dafuer muessen TEST_ADMIN_URL und TEST_APP_PASSWORD gesetzt sein, siehe README),
  4. mit --mutation zusaetzlich: die Gegenprobe meldet keinen unentdeckten Fehler. Sie laeuft in fuenf gleichzeitigen
     Teilen (je eine eigene Arbeitskopie, Test-Datenbanken mit eindeutigen Namen) - dieselben Eintraege, nur schneller;
     seit ueber 500 Eintraegen dauerte der Lauf nacheinander in der Cloud laenger als das Zeitlimit von 2 Stunden.

--record haengt das Ergebnis mit Datum, Git-Stand und Python-Version an docs/TEST_RESULTS.md an.
Rueckgabewert: 0 = STABIL, 1 = NICHT STABIL.
"""

from __future__ import annotations

import datetime as dt
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import IO, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
# Gegenprobe in gleichzeitigen Teilen: (Gruppe aus scripts/mutation_check.py, Teil, Anzahl Teile). Die Kern-Gruppe ist
# geteilt, weil die meisten ihrer Eintraege die ganze Testsuite brauchen (allein ueber 2 Stunden in der Cloud).
MUTATION_JOBS = (("kern", 1, 3), ("kern", 2, 3), ("kern", 3, 3), ("backtest", 1, 1), ("datenbank", 1, 1))
MUTATION_TIMEOUT_S = 7200


LAST_OUTPUT: List[str] = []  # alle Zeilen des letzten Aufrufs (z. B. fuer die Namen fehlgeschlagener Tests)


def run(name: str, cmd: List[str], timeout: int = 3600) -> Tuple[bool, str, float]:
    start = time.monotonic()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env, timeout=timeout)
    lines = [line for line in (proc.stdout + proc.stderr).strip().splitlines() if line.strip()]
    LAST_OUTPUT[:] = lines
    return proc.returncode == 0, lines[-1] if lines else "", time.monotonic() - start


def _stop(signum: int, frame: object) -> None:
    raise SystemExit(128 + signum)  # damit "finally" die gleichzeitigen Laeufe beendet


def mutation_parallel(jobs: Tuple[Tuple[str, int, int], ...] = MUTATION_JOBS,
                      timeout: int = MUTATION_TIMEOUT_S) -> Tuple[bool, str, float]:
    """Gegenprobe in gleichzeitigen Teilen. STABIL nur, wenn jeder Teil fehlerfrei endet und sein Ergebnis meldet."""
    start = time.monotonic()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    outputs: Dict[str, IO[str]] = {}
    procs: Dict[str, "subprocess.Popen[str]"] = {}
    previous = signal.signal(signal.SIGTERM, _stop)
    try:
        for group, part, parts in jobs:  # Ausgabe in Dateien: volle Pipes wuerden einen Lauf anhalten
            label = group if parts == 1 else "%s %d/%d" % (group, part, parts)
            cmd = [PY, "scripts/mutation_check.py", group] + ([] if parts == 1 else ["--shard", "%d/%d" % (part, parts)])
            outputs[label] = tempfile.TemporaryFile("w+", encoding="utf-8")
            procs[label] = subprocess.Popen(cmd, cwd=ROOT, env=env, text=True, stdout=outputs[label],
                                            stderr=subprocess.STDOUT, start_new_session=True)
        for label, proc in procs.items():
            try:
                proc.wait(timeout=max(1.0, timeout - (time.monotonic() - start)))
            except subprocess.TimeoutExpired:
                return False, "Zeitlimit %d s ueberschritten (%s)" % (timeout, label), time.monotonic() - start
        ok = True
        totals: List[int] = [0, 0, 0]
        summary: List[str] = []
        missing: List[str] = []
        for label, proc in procs.items():
            outputs[label].seek(0)
            text = outputs[label].read()
            found = re.search(r"ERGEBNIS: (\d+) entdeckt, (\d+) nicht entdeckt, (\d+) nicht geprueft", text)
            if proc.returncode != 0 or found is None:
                ok = False
            if found is None:
                lines = [line for line in text.strip().splitlines() if line.strip()]
                summary.append("%s: %s" % (label, lines[-1] if lines else "keine Ausgabe"))
                continue
            counts = [int(n) for n in found.groups()]
            totals = [a + b for a, b in zip(totals, counts)]
            summary.append("%s %d/%d" % (label, counts[0], sum(counts)))
            missing += [line.strip() for line in text.splitlines() if line.startswith("  fehlender Test")]
        detail = "ERGEBNIS: %d entdeckt, %d nicht entdeckt, %d nicht geprueft (%s)" % (totals[0], totals[1], totals[2], ", ".join(summary))
        return ok, detail + ("; " + "; ".join(missing) if missing else ""), time.monotonic() - start
    finally:
        signal.signal(signal.SIGTERM, previous)
        for proc in procs.values():
            if proc.poll() is None:  # bei Zeitlimit oder Abbruch: ganze Prozessgruppe samt Testlaeufen beenden
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        for handle in outputs.values():
            handle.close()


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
        ok, last, secs = mutation_parallel()
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
