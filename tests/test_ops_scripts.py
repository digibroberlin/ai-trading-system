"""Betrieb (AP-27, D-029): Bedienskripte im Trockenlauf und die Produktions-Compose-Datei.

Die Skripte werden mit DRY_RUN=1 ausgefuehrt (es startet nichts); geprueft werden Reihenfolge, Schutz der Passwort-Datei
und dass nie ein Geheimnis ausgegeben wird. Die Compose-Datei wird als Text geprueft und - wenn Docker installiert ist -
zusaetzlich mit `docker compose config` gegen das offizielle Schema.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OPS = ROOT / "scripts" / "ops"
COMPOSE = ROOT / "docker-compose.prod.yml"
SCRIPTS = ("start.sh", "stop.sh", "status.sh", "backup.sh")


def env_file(tmp_path, mode=0o600, extra=""):
    path = tmp_path / "trading-secrets.env"
    lines = ["POSTGRES_PASSWORD=fake-admin-pw", "APP_DB_PASSWORD=fake-app-pw"]  # erfundene Testwerte
    path.write_text("\n".join(lines) + "\n" + extra)
    os.chmod(path, mode)
    return path


def run(script, *args, dry=True):
    env = dict(os.environ, DRY_RUN="1" if dry else "0")
    return subprocess.run(["bash", str(OPS / script), *map(str, args)], capture_output=True, text=True, env=env,
                          cwd=ROOT)


@pytest.mark.parametrize("script", SCRIPTS + ("common.sh",))
def test_scripts_are_valid_strict_bash(script):
    text = (OPS / script).read_text()
    assert text.startswith("#!/usr/bin/env bash") and "set -euo pipefail" in text
    assert subprocess.run(["bash", "-n", str(OPS / script)]).returncode == 0


def test_start_runs_backup_before_migration_before_the_service(tmp_path):
    done = run("start.sh", env_file(tmp_path))
    assert done.returncode == 0, done.stderr
    steps = [line for line in done.stdout.splitlines() if line.startswith("+ compose")]
    assert steps == ["+ compose up -d --build --wait db", "+ compose run --rm --build backup",
                     "+ compose run --rm --build migrate", "+ compose up -d --build trader"]
    assert "KEINE Freigabe" in done.stdout and "fake-admin-pw" not in done.stdout + done.stderr
    approved = run("start.sh", env_file(tmp_path, extra="TRADING_ENGINE_ENABLED=1\nTRADING_ENGINE_APPROVAL=x\n"))
    assert "Freigabe gesetzt" in approved.stdout and "kein echtes Geld" in approved.stdout


def test_stop_status_and_backup(tmp_path):
    secrets = env_file(tmp_path)
    assert run("stop.sh", secrets).stdout.splitlines()[0] == "+ compose stop --timeout 60 trader"
    assert "+ compose stop --timeout 60 db" in run("stop.sh", secrets, "--all").stdout
    assert run("status.sh", secrets).stdout.splitlines()[0] == "+ compose ps"
    assert run("backup.sh", secrets).stdout.splitlines() == ["+ mkdir -p -m 700 %s/backups" % ROOT,
                                                            "+ compose run --rm backup"]


def test_the_password_file_must_be_private_and_outside_the_project(tmp_path):
    assert "Rechte 644" in run("start.sh", env_file(tmp_path, mode=0o644)).stderr
    inside = ROOT / "scripts" / "ops" / "test-secrets.env"
    try:
        inside.write_text("POSTGRES_PASSWORD=fake\n")
        os.chmod(inside, 0o600)
        assert "Projektordner" in run("start.sh", inside).stderr
    finally:
        inside.unlink()
    assert run("start.sh").returncode != 0 and run("start.sh", tmp_path / "fehlt.env").returncode != 0


# ------------------------------------------------------------------ Compose-Datei


def service_block(name):
    text = COMPOSE.read_text()
    match = re.search(r"\n  %s:\n(.*?)(?=\n  [a-z]+:\n|\nvolumes:)" % name, text, re.S)
    assert match, name
    return match.group(1)


def test_production_compose_is_safe_by_default():
    text = COMPOSE.read_text()
    assert "ports:" not in service_block("db")  # Datenbank nie nach aussen
    trader = service_block("trader")
    assert '"127.0.0.1:8000:8000"' in trader and re.findall(r'^\s+- "(.+)"$', trader, re.M) == ["127.0.0.1:8000:8000"]
    assert "TRADING_ENGINE_ENABLED: ${TRADING_ENGINE_ENABLED:-0}" in trader
    assert "TRADING_ENGINE_APPROVAL: ${TRADING_ENGINE_APPROVAL:-}" in trader
    assert "restart: on-failure" in trader and "read_only: true" in trader and "- ALL" in trader
    assert "trading_app:" in trader and "postgresql://trading:" not in trader  # nur die eingeschraenkte Rolle
    assert "TRADING_MODE: paper" in trader and "healthcheck:" in trader
    for name in ("AI_BUDGET_PER_CALL_USD", "AI_BUDGET_DAILY_USD", "AI_BUDGET_MONTHLY_USD"):
        assert "%s: ${%s:-0}" % (name, name) in trader  # KI-Budget ohne Freigabe 0
    assert text.count(":?Bitte POSTGRES_PASSWORD setzen}") >= 3 and "latest" not in text
    secrets = re.findall(r"^\s+\w*(?:PASSWORD|SECRET|KEY)\w*:\s*(\S+)", text, re.M)
    in_urls = re.findall(r"://[^:@\s/]+:(.+?)@", text)
    assert secrets and in_urls and all(v.startswith("${") for v in secrets + in_urls)  # nur Variablen, nie Werte


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker-Kommandozeile nicht installiert - nur Textpruefung")
def test_production_compose_passes_the_official_schema(tmp_path):
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        pytest.skip("docker compose nicht verfuegbar - nur Textpruefung")
    ok = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "--env-file", str(env_file(tmp_path)), "config", "-q"],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    (tmp_path / "leer.env").write_text("")
    missing = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "--env-file", str(tmp_path / "leer.env"),
                              "config", "-q"], capture_output=True, text=True)
    assert missing.returncode != 0 and "Bitte" in missing.stderr  # ohne Passwoerter startet nichts
