"""Globaler Not-Aus in PostgreSQL (Tabelle `trading_halt`, Migration 003) - gilt fuer alle Konten und Prozesse.

    python -m trading.persistence.kill_switch status                  (DATABASE_URL)
    python -m trading.persistence.kill_switch halt "Grund"            (DATABASE_URL)
    python -m trading.persistence.kill_switch release NAME "HANDEL FREIGEBEN"   (ADMIN_DATABASE_URL)

Aufheben geht nur mit einer Administrator-Verbindung (die Datenbank verweigert es der Anwendungsrolle), mit Namen
und dem Bestaetigungstext. Jede Aenderung steht im Ereignisprotokoll (HALT_ACTIVATED / HALT_RELEASED).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional, Sequence

from trading.audit.events import EventType, to_jsonable
from trading.clock import Clock, SystemClock
from trading.persistence.db import connect
from trading.persistence.pg_event_log import append_raw
from trading.safety.kill_switch import (
    RELEASE_CONFIRMATION,
    RUNNING,
    HaltReason,
    HaltStatus,
    KillSwitch,
    check_release,
    clean_note,
)


def read_halt(cur: Any, lock: bool = False) -> HaltStatus:
    """Liest den Not-Aus innerhalb der Transaktion des Aufrufers. Fehlt die Zeile oder ist sie unlesbar: gestoppt."""
    with cur.connection.cursor() as c:
        c.execute("SELECT halted, reason, since, note FROM trading_halt WHERE id = 1" + (" FOR UPDATE" if lock else ""))
        row = c.fetchone()
    if row is None:
        return HaltStatus(True, None, None, "Not-Aus-Zeile fehlt")
    halted, reason, since, note = row
    if not halted:
        return RUNNING
    try:
        parsed: Optional[HaltReason] = HaltReason(reason)
    except ValueError:
        parsed = None
    return HaltStatus(True, parsed, since, note or "")


def write_trip(cur: Any, status: HaltStatus) -> bool:
    """Traegt einen ausgeloesten Not-Aus ein (nur falls noch nicht ausgeloest). Rueckgabe: neu ausgeloest?"""
    assert status.halted and status.reason is not None and status.since is not None
    with cur.connection.cursor() as c:
        c.execute(
            "UPDATE trading_halt SET halted = true, reason = %s, since = %s, note = %s WHERE id = 1 AND NOT halted",
            (status.reason.value, status.since, clean_note(status.note)),
        )
        return bool(c.rowcount == 1)


def log_event(cur: Any, clock: Clock, event_type: EventType, payload: Any) -> None:
    payload_json = json.dumps(to_jsonable(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    append_raw(cur, clock.now().isoformat(), event_type.value, payload_json)


def trip_in_new_transaction(conn: Any, clock: Clock, reason: HaltReason, note: str) -> bool:
    """Loest den Not-Aus in einer EIGENEN Transaktion aus - z. B. nachdem die eigentliche Operation gescheitert ist.

    Der Protokolleintrag wird versucht (Sicherungspunkt); scheitert er - etwa weil gerade das Protokoll kaputt ist -,
    wird der Not-Aus trotzdem gespeichert. Stoppen ist wichtiger als Dokumentieren.
    """
    status = HaltStatus(True, reason, clock.now(), clean_note(note))
    with conn.transaction(), conn.cursor() as cur:
        newly = write_trip(cur, status)
        if newly:
            try:
                with conn.transaction():
                    log_event(cur, clock, EventType.HALT_ACTIVATED, {"reason": reason, "note": status.note})
            except Exception:  # noqa: BLE001 - Protokoll gestoert: Not-Aus bleibt trotzdem gesetzt
                pass
    return newly


class PostgresKillSwitch(KillSwitch):
    """Not-Aus ueber die Datenbank. `release` funktioniert nur mit einer Administrator-Verbindung."""

    def __init__(self, url: str, clock: Optional[Clock] = None) -> None:
        self._conn = connect(url)
        self._clock = clock if clock is not None else SystemClock()

    def close(self) -> None:
        self._conn.close()

    def status(self) -> HaltStatus:
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                return read_halt(cur)
        except Exception as exc:  # Datenbank nicht erreichbar -> gestoppt (fail closed)
            return HaltStatus(True, None, None, "Not-Aus nicht lesbar: %s" % type(exc).__name__)

    def trip(self, reason: HaltReason, note: str = "") -> bool:
        return trip_in_new_transaction(self._conn, self._clock, reason, note)

    def release(self, operator: str, confirmation: str) -> None:
        name = check_release(operator, confirmation)
        with self._conn.transaction(), self._conn.cursor() as cur:
            before = read_halt(cur, lock=True)
            if not before.halted:
                return
            cur.execute(
                "UPDATE trading_halt SET halted = false, released_by = %s, released_at = %s WHERE id = 1",
                (name, self._clock.now()),
            )
            log_event(cur, self._clock, EventType.HALT_RELEASED,
                      {"operator": name, "previous_reason": before.reason, "previous_since": before.since})


def main(argv: Sequence[str]) -> int:
    if not argv or argv[0] not in ("status", "halt", "release"):
        print(__doc__, file=sys.stderr)
        return 2
    command = argv[0]
    url = os.environ.get("ADMIN_DATABASE_URL" if command == "release" else "DATABASE_URL")
    if not url:
        print("%s fehlt." % ("ADMIN_DATABASE_URL" if command == "release" else "DATABASE_URL"), file=sys.stderr)
        return 2
    switch = PostgresKillSwitch(url)
    try:
        if command == "halt":
            newly = switch.trip(HaltReason.MANUAL, " ".join(argv[1:]) or "von Hand")
            print("Not-Aus ausgeloest." if newly else "Not-Aus war bereits ausgeloest.")
        elif command == "release":
            if len(argv) != 3:
                print('Aufruf: release NAME "%s"' % RELEASE_CONFIRMATION, file=sys.stderr)
                return 2
            switch.release(argv[1], argv[2])
            print("Not-Aus aufgehoben.")
        status = switch.status()
        print("Zustand: %s" % ("GESTOPPT (%s seit %s: %s)" % (
            status.reason.value if status.reason else "?", status.since, status.note) if status.halted else "frei"))
        return 1 if status.halted else 0
    finally:
        switch.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
