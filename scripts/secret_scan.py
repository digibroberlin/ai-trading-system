"""Sucht nach versehentlich eingecheckten Geheimnissen:  python scripts/secret_scan.py [--history]

Geprueft werden alle Dateien, die Git verwaltet (also alles, was auf GitHub landen wuerde), mit `--history`
zusaetzlich jede jemals eingecheckte Zeile. Gefunden werden:
  * verbotene Dateien (.env, private Schluessel, Zertifikate, Datenbank-Abzuege),
  * typische Schluesselformate (GitHub-, Anthropic-, OpenAI-, AWS-, Slack-Token, private Schluessel),
  * Zuweisungen wie `password = "..."` / `API_KEY=...` mit einem Wert, der nicht wie ein Platzhalter aussieht,
  * Verbindungsadressen mit eingebautem Passwort (postgresql://<nutzer>:<passwort>@...).

Ergebnis: 0 = nichts gefunden, 1 = Fund(e). Ausgegeben werden Datei, Zeile und Art des Fundes - NIE der Wert selbst.

Grenzen: Ein Musterabgleich findet keine Geheimnisse ohne erkennbare Form (z. B. ein nacktes Zufallswort).
Deshalb liegen echte Passwoerter grundsaetzlich ausserhalb des Projektordners (siehe README).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_FILES = [
    (re.compile(r"(^|/)\.env(\.[^/]*)?$"), "Umgebungsdatei (.env)"),
    (re.compile(r"(^|/)id_(rsa|dsa|ecdsa|ed25519)$"), "privater SSH-Schluessel"),
    (re.compile(r"\.(pem|key|p12|pfx|keystore|jks)$"), "Schluessel-/Zertifikatsdatei"),
    (re.compile(r"(^|/)backups/.*\.(sql|dump|gz|tar)$"), "Datenbank-Abzug / Sicherung"),
    (re.compile(r"(^|/)(credentials|secrets?)(\.[a-z]+)?$", re.IGNORECASE), "Zugangsdaten-Datei"),
]
ALLOWED_FILES = {".env.example"}

TOKEN_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "privater Schluessel"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "GitHub-Token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"), "GitHub-Token"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"), "Anthropic-API-Schluessel"),
    (re.compile(r"\bsk-(proj-)?[A-Za-z0-9]{32,}\b"), "OpenAI-API-Schluessel"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS-Zugangsschluessel"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), "Slack-Token"),
]
# `name = wert` / `name: wert` / `NAME=wert` fuer sensible Namen (z. B. auch HCLOUD_TOKEN=...)
ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<name>[a-z0-9_\-]*(password|passwort|passwd|secret|api[_\-]?key|access[_\-]?key|private[_\-]?key|
             passphrase|token|bearer)[a-z0-9_\-]*)
    \s*[:=]\s*
    (?P<quote>["']?)(?P<value>[^\s"'#,;)]{8,})(?P=quote)
    """
)
URL_WITH_PASSWORD = re.compile(r"[a-z][a-z0-9+.\-]*://[^\s:/@]+:(?P<value>[^\s@/]{6,})@")

# Werte, die erkennbar keine echten Geheimnisse sind (Platzhalter, Verweise auf Variablen, Code).
PLACEHOLDER = re.compile(
    r"""(?ix)
    ^( \$\{?[a-z_][a-z0-9_]*\}?(:?[?\-].*)?    # ${VAR}, $VAR, ${VAR:?Hinweis}
     | [?\-+=:].*                               # Shell-Operator wie in ${VAR:?Hinweis}
     | %\(?[a-z_]*\)?s | \{[a-z_]*\}             # Formatplatzhalter
     | <[^>]*> | x{4,} | \*{3,}
     | (bitte|please|changeme|example|beispiel|dummy|placeholder|your|dein|ihr|test|fake|redacted|none|null)[a-z0-9_\-]*
     | [a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+(\(.*)?   # Attributzugriff / Funktionsaufruf
     | [a-z_][a-z0-9_]*\(.*                        # Funktionsaufruf
     | (os\.)?environ.* | getenv.*
    )$
    """
)
# Token-ANZAHLEN der KI-Kostenmessung (input_tokens, tokens_out ...) sind keine Zugangs-Token: "tokens" in der Mehrzahl,
# ohne ein weiteres heikles Wort im Namen. Echte Token-Variablen (GH_TOKEN, access_token, bearer_token) bleiben erfasst.
TOKEN_COUNT_NAME = re.compile(r"(?i)tokens")
OTHER_SENSITIVE = re.compile(r"(?i)password|passwort|passwd|secret|api[_\-]?key|access[_\-]?key|private[_\-]?key|passphrase|bearer")


def is_token_count(name: str) -> bool:
    return bool(TOKEN_COUNT_NAME.search(name)) and not OTHER_SENSITIVE.search(name)


# Name einer Konstanten oder Umgebungsvariablen (GROSS_MIT_UNTERSTRICH) - Gross-/Kleinschreibung zaehlt hier.
CONSTANT_NAME = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")


def is_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER.match(value) or CONSTANT_NAME.match(value))


SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def tracked_files() -> List[str]:
    """Alle Dateien, die Git verwaltet. Ohne Git (z. B. im Test-Container) werden alle Dateien geprueft."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
        return [p for p in out.stdout.decode("utf-8").split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        return sorted(str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
                      if p.is_file() and not SKIP_DIRS.intersection(p.relative_to(ROOT).parts))


def check_file_name(path: str) -> List[str]:
    if Path(path).name in ALLOWED_FILES:
        return []
    return [label for pattern, label in FORBIDDEN_FILES if pattern.search(path)]


def check_line(line: str) -> List[str]:
    found = [label for pattern, label in TOKEN_PATTERNS if pattern.search(line)]
    for m in ASSIGNMENT.finditer(line):
        value = m.group("value")
        if not is_placeholder(value) and not is_token_count(m.group("name")):
            found.append("Zuweisung an '%s'" % m.group("name"))
    for m in URL_WITH_PASSWORD.finditer(line):
        if not is_placeholder(m.group("value")):
            found.append("Adresse mit eingebautem Passwort")
    return found


ALLOWLIST = ROOT / "scripts" / "secret_scan_allow.txt"


def line_fingerprint(line: str) -> str:
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()


def allowed_fingerprints() -> Set[str]:
    """Von einem Menschen gepruefte Fehlalarme (z. B. absichtlich erfundene Werte in Tests).
    Die Datei enthaelt nur Fingerabdruecke der Zeilen und eine Begruendung - nie die Werte selbst."""
    if not ALLOWLIST.is_file():
        return set()
    return {line.split()[0] for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")}


def scan_lines(lines: Iterable[Tuple[str, int, str]]) -> List[str]:
    findings = []
    allowed = allowed_fingerprints()
    for where, number, line in lines:
        if line_fingerprint(line) in allowed:
            continue
        for label in check_line(line):
            findings.append("%s:%d: %s" % (where, number, label))
    return findings


def scan_working_tree() -> List[str]:
    findings: List[str] = []
    for path in tracked_files():
        findings += ["%s: verbotene Datei (%s)" % (path, label) for label in check_file_name(path)]
        file = ROOT / path
        if not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # Binaerdatei
        findings += scan_lines((path, i, line) for i, line in enumerate(text.splitlines(), 1))
    return findings


def scan_history() -> List[str]:
    names = subprocess.run(["git", "log", "--all", "--name-only", "--format="], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.split()
    findings = ["Geschichte: %s: verbotene Datei (%s)" % (p, label)
                for p in sorted(set(names)) for label in check_file_name(p)]
    log = subprocess.run(["git", "log", "--all", "-p", "--format=commit %H"], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8", errors="replace")
    commit, added = "?", []
    for line in log.splitlines():
        if line.startswith("commit "):
            commit = line[7:15]
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(("Geschichte %s" % commit, 0, line[1:]))
    return findings + scan_lines(added)


def main(argv: List[str]) -> int:
    findings = scan_working_tree()
    if "--history" in argv:
        findings += scan_history()
    if findings:
        print("GEHEIMNIS-SCAN: %d Fund(e) - NICHT hochladen, erst pruefen:" % len(findings))
        for f in findings:
            print("  " + f)
        return 1
    print("GEHEIMNIS-SCAN: keine Funde (%s)." % ("Arbeitsstand und gesamte Geschichte" if "--history" in argv
                                                  else "Arbeitsstand"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
