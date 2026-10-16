"""Sicherung der Datenbank mit Rueckspiel-Test (AP-26, Audit M14).

  python scripts/backup.py create  --out backups/            # Sicherung (braucht ADMIN_DATABASE_URL)
  python scripts/backup.py verify  backups/trading-....dump  # Rueckspiel-Test in eine Wegwerf-Datenbank
  python scripts/backup.py prune   --out backups/ --keep 14  # aeltere Sicherungen loeschen (die neuesten bleiben)

Was "create" tut:
  * `pg_dump` im eigenen Format (komprimiert, mit Rechten); das Passwort geht ueber die Umgebung (PGPASSWORD), nie
    ueber die Befehlszeile (sonst stuende es in der Prozessliste).
  * Daneben `.sha256` (Pruefsumme) und `.json` (Zeitpunkt, Server-Version, Schema-Stand, Zeilen je Tabelle, Ende der
    Ereigniskette). Dateien nur fuer den Eigentuemer lesbar (0600); geschrieben wird atomar.
Was "verify" tut (der eigentliche Nachweis, dass die Sicherung etwas taugt):
  * Pruefsumme vergleichen, dann in eine NEUE Wegwerf-Datenbank zurueckspielen (`pg_restore --exit-on-error`),
    Zeilenzahlen und Schema-Stand mit der Beschreibung vergleichen und die Hash-Kette des Ereignisprotokolls samt
    Kettenende pruefen. Die Wegwerf-Datenbank wird immer wieder entfernt.
Die Zieldatenbank des Betriebs wird von "verify" nie angefasst. `backups/` ist nicht im Repository (nur README).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

TABLES = ("event_log", "event_log_head", "accounts", "positions", "closed_trades", "orders", "market_state",
          "instrument_state", "trading_halt", "ai_budget_limits", "ai_budget_entries", "schema_migrations")


class BackupError(RuntimeError):
    pass


def libpq_env(url: str) -> Dict[str, str]:
    """Verbindungsangaben als libpq-Umgebung (PGHOST ... PGPASSWORD) - damit nichts Geheimes in der Befehlszeile steht."""
    from psycopg.conninfo import conninfo_to_dict

    info = conninfo_to_dict(url)
    names = {"host": "PGHOST", "port": "PGPORT", "user": "PGUSER", "password": "PGPASSWORD", "dbname": "PGDATABASE",
             "sslmode": "PGSSLMODE"}
    env = {names[k]: str(v) for k, v in info.items() if k in names and v is not None}
    if "PGDATABASE" not in env:
        raise BackupError("Die Adresse nennt keine Datenbank.")
    return env


def _run(cmd: Sequence[str], env: Mapping[str, str]) -> None:
    full_env = dict(os.environ)
    full_env.update(env)
    done = subprocess.run(list(cmd), env=full_env, capture_output=True, text=True)
    if done.returncode != 0:
        raise BackupError("%s fehlgeschlagen: %s" % (cmd[0], done.stderr.strip()[-500:]))


def tool_major(tool: str) -> int:
    """Hauptversion von pg_dump/pg_restore (z. B. 16). Fehlt das Werkzeug -> BackupError."""
    try:
        out = subprocess.run([tool, "--version"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        raise BackupError("%s ist nicht installiert (PostgreSQL-Client-Werkzeuge noetig)." % tool) from None
    digits = [part for part in out.split() if part[:1].isdigit()]
    if not digits:
        raise BackupError("Version von %s nicht lesbar: %r" % (tool, out.strip()))
    return int(digits[0].split(".")[0])


def check_tools(server_version: str) -> None:
    """pg_dump/pg_restore duerfen nicht aelter sein als der Server (sonst bricht pg_dump ab oder fehlt etwas)."""
    server = int(server_version.split(".")[0])
    for tool in ("pg_dump", "pg_restore"):
        major = tool_major(tool)
        if major < server:
            raise BackupError("%s %d ist aelter als der Datenbank-Server %d." % (tool, major, server))


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def describe(url: str) -> Dict[str, Any]:
    """Zeilen je Tabelle, Schema-Stand, Ende der Ereigniskette - zum Vergleich nach dem Rueckspielen."""
    import psycopg
    from psycopg import sql

    with psycopg.connect(url) as conn:
        counts = {}
        for table in TABLES:
            exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()
            if exists and exists[0]:
                row = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()
                counts[table] = int(row[0]) if row else 0
        version = conn.execute("SELECT max(version) FROM schema_migrations").fetchone()
        head = conn.execute("SELECT seq, hash FROM event_log_head WHERE id = 1").fetchone()
        server = conn.execute("SHOW server_version").fetchone()
    return {"tables": counts, "schema_version": int(version[0]) if version and version[0] is not None else 0,
            "event_log_head": [int(head[0]), str(head[1])] if head else None,
            "server_version": str(server[0]) if server else ""}


def create(admin_url: str, out_dir: Path, now: Optional[datetime] = None) -> Path:
    now = now or datetime.now(timezone.utc)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = "trading-%s" % now.strftime("%Y%m%dT%H%M%SZ")
    final = out_dir / (stem + ".dump")
    if final.exists():
        raise BackupError("%s gibt es schon." % final.name)
    tmp = out_dir / (stem + ".dump.tmp")
    env = libpq_env(admin_url)
    meta = describe(admin_url)  # vor dem Abzug: was mindestens drin sein muss
    check_tools(meta["server_version"])
    _run(["pg_dump", "--format=custom", "--compress=6", "--file", str(tmp)], env)
    os.chmod(tmp, 0o600)
    os.replace(tmp, final)
    checksum = sha256_of(final)
    meta.update(created_at=now.isoformat(), file=final.name, sha256=checksum, database=env["PGDATABASE"])
    _write_private(out_dir / (stem + ".sha256"), "%s  %s\n" % (checksum, final.name))
    _write_private(out_dir / (stem + ".json"), json.dumps(meta, indent=1, sort_keys=True))
    return final


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def verify(dump: Path, admin_url: str) -> List[str]:
    """Rueckspiel-Test. Rueckgabe: Liste der Befunde (leer = Sicherung in Ordnung)."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    meta_path = dump.with_suffix(".json")
    if not dump.exists() or not meta_path.exists():
        return ["Sicherung oder Beschreibung fehlt (%s)" % dump.name]
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if sha256_of(dump) != meta.get("sha256"):
        return ["Pruefsumme passt nicht - Datei beschaedigt oder veraendert"]
    name = "restore_check_" + uuid.uuid4().hex[:10]
    findings: List[str] = []
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    target = make_conninfo(admin_url, dbname=name)
    try:
        _run(["pg_restore", "--exit-on-error", "--dbname", name, str(dump)], dict(libpq_env(target)))
        restored = describe(target)
        for table, count in sorted(meta["tables"].items()):
            got = restored["tables"].get(table)
            if got is None or got < count:
                findings.append("Tabelle %s: %s Zeilen statt mindestens %d" % (table, got, count))
        if restored["schema_version"] != meta["schema_version"]:
            findings.append("Schema-Stand %s statt %s" % (restored["schema_version"], meta["schema_version"]))
        from trading.persistence.pg_event_log import PostgresEventLog

        log = PostgresEventLog(target)
        try:
            result = log.verify()
        finally:
            log.close()
        if not result.ok:
            findings.append("Ereigniskette ungueltig: %s" % result.message)
        head = restored["event_log_head"]
        if meta["event_log_head"] is not None and (head is None or head[0] < meta["event_log_head"][0]):
            findings.append("Ereigniskette kuerzer als beim Sichern")
    except BackupError as exc:
        findings.append(str(exc))
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    return findings


def prune(out_dir: Path, keep: int) -> List[Path]:
    if keep < 1:
        raise BackupError("Mindestens eine Sicherung bleibt.")
    dumps = sorted(out_dir.glob("trading-*.dump"))
    removed = []
    for dump in dumps[:-keep]:
        for path in (dump, dump.with_suffix(".sha256"), dump.with_suffix(".json")):
            if path.exists():
                path.unlink()
                removed.append(path)
    return removed


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Datenbank sichern und Sicherungen pruefen (AP-26).")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create")
    c.add_argument("--out", type=Path, default=ROOT / "backups")
    v = sub.add_parser("verify")
    v.add_argument("dump", type=Path)
    p = sub.add_parser("prune")
    p.add_argument("--out", type=Path, default=ROOT / "backups")
    p.add_argument("--keep", type=int, default=14)
    args = parser.parse_args(argv)
    try:
        if args.command == "prune":
            print("Entfernt: %d Dateien" % len(prune(args.out, args.keep)))
            return 0
        admin_url = os.environ.get("ADMIN_DATABASE_URL")
        if not admin_url:
            raise BackupError("ADMIN_DATABASE_URL fehlt (Administrator der Datenbank).")
        if args.command == "create":
            path = create(admin_url, args.out)
            print("SICHERUNG: %s" % path)
            return 0
        findings = verify(args.dump, admin_url)
    except BackupError as exc:
        print("FEHLER: %s" % exc)
        return 2
    if findings:
        print("RUECKSPIEL-TEST: NICHT BESTANDEN")
        for finding in findings:
            print("  - %s" % finding)
        return 1
    print("RUECKSPIEL-TEST: BESTANDEN (%s)" % args.dump.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
