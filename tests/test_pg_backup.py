"""Sicherung mit Rueckspiel-Test (AP-26, Audit M14): echte PostgreSQL-Datenbank, echtes pg_dump/pg_restore."""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import shutil

import pytest
from conftest import ADMIN_URL, requires_db

psycopg = pytest.importorskip("psycopg", reason="psycopg nicht installiert - Datenbank-Tests uebersprungen")

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("backup", ROOT / "scripts" / "backup.py")
backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup)

T0 = datetime(2026, 10, 16, 21, 0, tzinfo=timezone.utc)
requires_tools = pytest.mark.skipif(shutil.which("pg_dump") is None or shutil.which("pg_restore") is None,
                                    reason="pg_dump/pg_restore nicht installiert (PostgreSQL-Client-Werkzeuge)")


def fill(pg):
    """Ein paar echte Eintraege: Konto, Protokoll (Hash-Kette), Not-Aus."""
    from trading.audit.events import EventType
    from trading.persistence.pg_event_log import PostgresEventLog

    pg.create_account("main", Decimal(10000))
    log = PostgresEventLog(pg.app_url)
    try:
        for i in range(5):
            log.append(EventType.SYSTEM_ERROR, {"n": i, "text": "Probe %d" % i})
    finally:
        log.close()


@requires_db
@requires_tools
def test_backup_restores_completely_and_checks_the_event_chain(pg, tmp_path, monkeypatch):
    fill(pg)
    dump = backup.create(pg.admin_url, tmp_path, T0)
    assert dump.name == "trading-20261016T210000Z.dump"
    meta = json.loads(dump.with_suffix(".json").read_text())
    assert meta["tables"]["event_log"] == 5 and meta["tables"]["accounts"] == 1 and meta["schema_version"] >= 6
    assert meta["sha256"] == backup.sha256_of(dump) and dump.with_suffix(".sha256").read_text().startswith(meta["sha256"])
    for path in (dump, dump.with_suffix(".json"), dump.with_suffix(".sha256")):
        assert os.stat(path).st_mode & 0o077 == 0  # nur fuer den Eigentuemer
    assert "password" not in json.dumps(meta).lower() and not list(tmp_path.glob("*.tmp"))
    made = []  # Namen der Wegwerf-Datenbanken DIESES Laufs - parallele Laeufe legen eigene an und stoeren nicht
    real_uuid4 = backup.uuid.uuid4

    def spy():
        made.append(real_uuid4())
        return made[-1]

    monkeypatch.setattr(backup.uuid, "uuid4", spy)
    assert backup.verify(dump, ADMIN_URL) == []
    names = ["restore_check_" + u.hex[:10] for u in made]
    with psycopg.connect(ADMIN_URL) as conn:
        left = conn.execute("SELECT count(*) FROM pg_database WHERE datname = ANY(%s)", (names,)).fetchone()[0]
    assert len(names) == 1 and left == 0  # Wegwerf-Datenbank wieder entfernt
    with pytest.raises(backup.BackupError):
        backup.create(pg.admin_url, tmp_path, T0)  # nie eine Sicherung ueberschreiben


@requires_db
@requires_tools
def test_damaged_or_incomplete_backups_fail_the_restore_test(pg, tmp_path):
    fill(pg)
    dump = backup.create(pg.admin_url, tmp_path, T0)
    data = bytearray(dump.read_bytes())
    data[len(data) // 2] ^= 0xFF
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / dump.name).write_bytes(bytes(data))
    (broken / dump.with_suffix(".json").name).write_text(dump.with_suffix(".json").read_text())
    assert backup.verify(broken / dump.name, ADMIN_URL) == ["Pruefsumme passt nicht - Datei beschaedigt oder veraendert"]
    meta = json.loads(dump.with_suffix(".json").read_text())
    meta["tables"]["event_log"] = 50  # beim Sichern waren angeblich mehr Eintraege da
    dump.with_suffix(".json").write_text(json.dumps(meta))
    findings = backup.verify(dump, ADMIN_URL)
    assert findings and "event_log" in findings[0]
    meta["tables"]["event_log"] = 5
    meta["schema_version"] = 99  # Sicherung stammt angeblich von einem neueren Schema
    dump.with_suffix(".json").write_text(json.dumps(meta))
    assert any("Schema-Stand" in f for f in backup.verify(dump, ADMIN_URL))
    assert backup.verify(tmp_path / "fehlt.dump", ADMIN_URL)


@requires_db
@requires_tools
def test_restore_detects_a_broken_event_chain(pg, tmp_path):
    fill(pg)
    with psycopg.connect(pg.admin_url) as conn:  # Manipulation an der Quelle (wie ein Angreifer mit Admin-Rechten)
        conn.execute("ALTER TABLE event_log DISABLE TRIGGER USER")
        conn.execute("UPDATE event_log SET payload_json = replace(payload_json, 'Probe 2', 'Probe X') WHERE seq = 3")
        conn.execute("ALTER TABLE event_log ENABLE TRIGGER USER")
    dump = backup.create(pg.admin_url, tmp_path, T0)
    findings = backup.verify(dump, ADMIN_URL)
    assert any("Ereigniskette ungueltig" in f for f in findings)


def test_password_goes_through_the_environment_not_the_command_line(monkeypatch):
    seen = {}

    def fake_run(cmd, env, capture_output, text):
        seen["cmd"], seen["env"] = cmd, env

        class Done:
            returncode = 0
            stderr = ""

        return Done()

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    env = backup.libpq_env("postgresql://trading:fakepw99@db:5432/trading")
    backup._run(["pg_dump", "--file", "x"], env)
    assert "fakepw99" not in " ".join(seen["cmd"]) and seen["env"]["PGPASSWORD"] == "fakepw99"
    assert env == {"PGHOST": "db", "PGPORT": "5432", "PGUSER": "trading", "PGPASSWORD": seen["env"]["PGPASSWORD"],
                   "PGDATABASE": "trading"}
    with pytest.raises(backup.BackupError):
        backup.libpq_env("postgresql://trading@db:5432")


def test_prune_keeps_the_newest(tmp_path):
    for i in range(5):
        stem = "trading-202610%02dT000000Z" % (10 + i)
        for suffix in (".dump", ".sha256", ".json"):
            (tmp_path / (stem + suffix)).write_text("x")
    removed = backup.prune(tmp_path, keep=2)
    assert len(removed) == 9 and sorted(p.name for p in tmp_path.glob("*.dump")) == [
        "trading-20261013T000000Z.dump", "trading-20261014T000000Z.dump"]
    with pytest.raises(backup.BackupError):
        backup.prune(tmp_path, keep=0)


def test_backups_never_enter_the_repository():
    spec = importlib.util.spec_from_file_location("secret_scan_for_backup", ROOT / "scripts" / "secret_scan.py")
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    assert scan.check_file_name("backups/trading-20261016T210000Z.dump")  # der Geheimnis-Scan haelt sie auf


def test_tools_older_than_the_server_are_refused(monkeypatch):
    monkeypatch.setattr(backup, "tool_major", lambda tool: 15)
    with pytest.raises(backup.BackupError, match="aelter als der Datenbank-Server 16"):
        backup.check_tools("16.4 (Debian 16.4-1.pgdg120+2)")
    monkeypatch.setattr(backup, "tool_major", lambda tool: 17)
    backup.check_tools("16.4")  # neuere Werkzeuge koennen aeltere Server sichern


def test_missing_tools_are_reported(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("pg_dump")

    monkeypatch.setattr(backup.subprocess, "run", missing)
    with pytest.raises(backup.BackupError, match="nicht installiert"):
        backup.tool_major("pg_dump")


@requires_db
@requires_tools
def test_cycle_prunes_old_backups_only_after_a_passed_restore_test(pg, tmp_path, monkeypatch, capsys):
    fill(pg)
    monkeypatch.setenv("ADMIN_DATABASE_URL", pg.admin_url)
    for stem in ("trading-20190101T000000Z", "trading-20200101T000000Z"):
        for suffix in (".dump", ".json", ".sha256"):
            (tmp_path / (stem + suffix)).write_text("alt")
    real_verify = backup.verify
    monkeypatch.setattr(backup, "verify", lambda dump, url: ["simulierter Fehler"])
    assert backup.main(["cycle", "--out", str(tmp_path), "--keep", "1"]) == 1
    assert (tmp_path / "trading-20190101T000000Z.dump").exists()  # nicht bestanden: nichts geloescht
    assert (tmp_path / "trading-20200101T000000Z.dump").exists()
    for dump in tmp_path.glob("trading-2026*"):
        dump.unlink()  # die eben erzeugte Sicherung entfernen (sonst gleicher Zeitstempel)
    monkeypatch.setattr(backup, "verify", real_verify)
    assert backup.main(["cycle", "--out", str(tmp_path), "--keep", "1"]) == 0
    assert "RUECKSPIEL-TEST: BESTANDEN" in capsys.readouterr().out
    assert [p.name for p in tmp_path.glob("*.dump")] != ["trading-20200101T000000Z.dump"]
    old = [tmp_path / (stem + suffix) for stem in ("trading-20190101T000000Z", "trading-20200101T000000Z")
           for suffix in (".dump", ".json", ".sha256")]
    assert not any(p.exists() for p in old) and len(list(tmp_path.glob("*.dump"))) == 1
