"""Der Geheimnis-Scanner muss echte Funde melden und Platzhalter in Ruhe lassen.

Die Beispiel-Schluessel werden erst zur Laufzeit zusammengesetzt, damit diese Datei selbst keinen Fund ausloest.
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "secret_scan.py"
_spec = importlib.util.spec_from_file_location("secret_scan", SCRIPT)
assert _spec is not None and _spec.loader is not None
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)

RANDOM = "q7Vx2Lm9Kp4Rt8Zw"  # sieht aus wie ein echtes Passwort


@pytest.mark.parametrize("line", [
    "token = '" + "ghp_" + "A1b2C3d4" * 5 + "'",
    "ANTHROPIC_API_KEY=" + "sk-ant-" + "api03-" + "x9Y8z7W6" * 4,
    "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
    "aws = " + "AKIA" + "ABCDEFGHIJKLMNOP",
    "POSTGRES_PASSWORD=" + RANDOM,
    'db_password: "' + RANDOM + '"',
    "HCLOUD_TOKEN=" + RANDOM * 4,
    "url = 'postgresql://trading:" + RANDOM + "@db:5432/trading'",
])
def test_real_looking_secrets_are_found(line):
    assert scan.check_line(line), line


@pytest.mark.parametrize("line", [
    "POSTGRES_PASSWORD=bitte-zufaellig-erzeugen",
    "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Bitte POSTGRES_PASSWORD setzen}",
    "DATABASE_URL: postgresql://trading_app:${APP_DB_PASSWORD}@db:5432/trading",
    'app_url = make_conninfo(ADMIN_URL, dbname=name, user="trading_app", password=APP_PASSWORD)',
    "password = os.environ.get('APP_DB_PASSWORD')",
    "api_key = settings.api_key",
    "token = next(tokens)",
    "# Passwort-Datei ausserhalb des Projektordners ablegen",
])
def test_placeholders_and_code_are_not_reported(line):
    assert scan.check_line(line) == [], line


@pytest.mark.parametrize("path, forbidden", [
    (".env", True), ("config/.env.local", True), (".env.example", False),
    ("backups/datenbank_2026-10-13.sql", True), ("keys/server.pem", True), ("id_ed25519", True),
    ("src/trading/config.py", False), ("docs/SECRETS_HANDLING.md", False),
])
def test_forbidden_file_names(path, forbidden):
    assert bool(scan.check_file_name(path)) is forbidden


def test_findings_never_print_the_secret_value():
    findings = scan.scan_lines([("datei.txt", 3, "POSTGRES_PASSWORD=" + RANDOM)])
    assert findings and all(RANDOM not in f for f in findings)


def test_the_repository_itself_is_clean():
    assert scan.scan_working_tree() == []


def test_without_git_all_files_are_scanned(monkeypatch):
    """Im Test-Container gibt es weder Git noch .git: Dann werden alle Dateien des Ordners geprueft."""
    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(scan.subprocess, "run", no_git)
    files = scan.tracked_files()
    assert "scripts/secret_scan.py" in files and "src/trading/config.py" in files
    assert not [f for f in files if f.startswith(".git/") or "__pycache__" in f]
    assert scan.scan_working_tree() == [] or all("backups/" in f for f in scan.scan_working_tree())


@pytest.mark.parametrize("line", [
    "        tokens_in: Dict[str, int] = defaultdict(int)",
    "    usage = TokenUsage(cache_write_tokens=prompt_bytes + REQUEST_OVERHEAD_TOKENS, output_tokens=max_out)",
    "    input_tokens_by_agent: Mapping[str, int]  # inkl. Cache",
    "TokenUsage(input_tokens=prompt_bytes_total, output_tokens=40)",
])
def test_token_counts_of_ai_cost_metering_are_not_secrets(line):
    assert scan.check_line(line) == [], line


@pytest.mark.parametrize("line", [
    "GH_TOKEN=" + RANDOM * 2,
    "access_token = '" + RANDOM + "'",
    "bearer_tokens = '" + RANDOM + "'",  # Mehrzahl, aber mit weiterem heiklen Wort
    "api_key_tokens: " + RANDOM,
    "secret_tokens = '" + RANDOM + "'",
])
def test_real_tokens_are_still_found_even_with_plural_names(line):
    assert scan.check_line(line), line
