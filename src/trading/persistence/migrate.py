"""Schema-Migrationen: nummerierte SQL-Dateien unter db/migrations, jede genau einmal, in Reihenfolge.

    python -m trading.persistence.migrate               # ausstehende Migrationen anwenden
    python -m trading.persistence.migrate --status      # nur anzeigen
    python -m trading.persistence.migrate --baseline-existing
        # einmalig fuer eine Datenbank, die noch OHNE Versionstabelle angelegt wurde (Stand = Migration 001)

Benoetigt ADMIN_DATABASE_URL (Datenbank-Administrator; die Anwendung selbst nutzt ihn nie).
MIGRATIONS_DIR ueberschreibt bei Bedarf das Verzeichnis der SQL-Dateien (im Docker-Image gesetzt).

Sicherheiten:
* Jede Migration laeuft in EINER Transaktion zusammen mit ihrem Eintrag in `schema_migrations` -
  sie wirkt ganz oder gar nicht.
* Eine Advisory-Sperre verhindert zwei gleichzeitige Laeufe.
* Die Pruefsumme jeder angewandten Datei wird gespeichert. Wurde eine bereits angewandte Datei
  nachtraeglich veraendert, bricht der Lauf ab (Migrationen aendert man nicht, man schreibt neue).
* Eine Datenbank mit Tabellen, aber ohne Versionstabelle, wird nicht angeruehrt, solange nicht
  ausdruecklich `--baseline-existing` angegeben ist.
* VOR jeder Migration einer Datenbank mit Daten: Backup (pg_dump). Der Lauf selbst loescht nichts.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import psycopg

LOCK_KEY = 7002
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[3] / "db" / "migrations"
_NAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def load_migrations(directory: Optional[Path] = None) -> List[Migration]:
    directory = directory or DEFAULT_DIRECTORY
    if not directory.is_dir():
        raise MigrationError("Migrationsverzeichnis fehlt: %s" % directory)
    found: List[Migration] = []
    for path in sorted(directory.iterdir()):
        match = _NAME.match(path.name)
        if match is None:
            if path.suffix == ".sql":
                raise MigrationError("Dateiname passt nicht zum Muster NNN_name.sql: %s" % path.name)
            continue
        sql = path.read_text(encoding="utf-8")
        found.append(Migration(int(match.group(1)), match.group(2), sql,
                               hashlib.sha256(sql.encode("utf-8")).hexdigest()))
    if [m.version for m in found] != list(range(1, len(found) + 1)):
        raise MigrationError("Migrationen muessen lueckenlos ab 001 nummeriert sein.")
    return found


def applied_versions(conn: "psycopg.Connection[tuple[object, ...]]") -> Dict[int, str]:
    rows = conn.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()
    return {int(str(r[0])): str(r[1]) for r in rows}


def migrate(
    admin_url: str,
    directory: Optional[Path] = None,
    target: Optional[int] = None,
    baseline_existing: bool = False,
) -> List[int]:
    """Wendet ausstehende Migrationen bis `target` (Standard: alle) an. Rueckgabe: neu angewandte Versionen."""
    migrations = load_migrations(directory)
    applied_now: List[int] = []
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL,"
            " note TEXT NOT NULL DEFAULT '', applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = applied_versions(conn)
        if not done:
            has_tables = conn.execute("SELECT to_regclass('public.event_log') IS NOT NULL").fetchone()
            if has_tables is not None and has_tables[0]:
                if not baseline_existing:
                    raise MigrationError(
                        "Die Datenbank enthaelt bereits Tabellen, aber keine Versionstabelle. "
                        "Nach einem Backup einmalig mit --baseline-existing starten."
                    )
                first = migrations[0]
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, checksum, note) VALUES (%s, %s, %s, %s)",
                    (first.version, first.name, first.checksum, "baseline: bestand bereits"),
                )
                done = {first.version: first.checksum}

        for m in migrations:
            if m.version in done and done[m.version] != m.checksum:
                raise MigrationError(
                    "Migration %03d wurde nach dem Anwenden veraendert (Pruefsumme passt nicht)." % m.version
                )
        if done and max(done) > len(migrations):
            raise MigrationError("Die Datenbank ist neuer als dieser Code (Version %d)." % max(done))

        for m in migrations:
            if m.version in done or (target is not None and m.version > target):
                continue
            with conn.transaction():
                conn.execute(m.sql.encode("utf-8"))  # festes SQL aus der Migrationsdatei
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                    (m.version, m.name, m.checksum),
                )
            applied_now.append(m.version)
    return applied_now


def status(admin_url: str, directory: Optional[Path] = None) -> List[str]:
    migrations = load_migrations(directory)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        exists = conn.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL").fetchone()
        done = applied_versions(conn) if exists is not None and exists[0] else {}
    return ["%03d %-40s %s" % (m.version, m.name, "angewandt" if m.version in done else "AUSSTEHEND")
            for m in migrations]


def main(argv: Sequence[str]) -> int:
    url = os.environ.get("ADMIN_DATABASE_URL")
    if not url:
        print("ADMIN_DATABASE_URL fehlt.", file=sys.stderr)
        return 2
    directory = Path(os.environ["MIGRATIONS_DIR"]) if os.environ.get("MIGRATIONS_DIR") else None
    try:
        if "--status" in argv:
            print("\n".join(status(url, directory)))
            return 0
        applied = migrate(url, directory, baseline_existing="--baseline-existing" in argv)
    except MigrationError as exc:
        print("ABBRUCH: %s" % exc, file=sys.stderr)
        return 1
    print("Neu angewandt: %s" % (", ".join("%03d" % v for v in applied) or "nichts (Schema ist aktuell)"))
    print("\n".join(status(url, directory)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
