"""Konten anlegen - nur durch einen Administrator (Migration 005, Audit M2).

    python -m trading.persistence.accounts list                       (DATABASE_URL)
    python -m trading.persistence.accounts create KONTO STARTKAPITAL  (ADMIN_DATABASE_URL)

Die Anwendung (Rolle `trading_app`) darf keine Konten anlegen: Jedes Konto hat ein eigenes 2-%-Risikobudget.
Ein Konto entsteht einmal, mit festem Startkapital, Modus PAPER und den festen Datenbank-Grenzen; es wird nie
geloescht. Die Anlage steht im Ereignisprotokoll (ACCOUNT_CREATED).
"""

from __future__ import annotations

import os
import re
import sys
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional, Sequence, Tuple

from trading.audit.events import EventType
from trading.clock import Clock, SystemClock
from trading.domain.numeric import is_positive_decimal
from trading.persistence.db import connect
from trading.persistence.kill_switch import log_event

_ACCOUNT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class AccountError(RuntimeError):
    pass


class AccountMissingError(AccountError):
    """Das Konto existiert nicht. Die Anwendung legt es nicht selbst an (fail closed)."""


def check_account_id(account_id: Any) -> str:
    if not isinstance(account_id, str) or not _ACCOUNT_ID.match(account_id):
        raise AccountError("Kontoname ungueltig (1-64 Zeichen: Buchstaben, Ziffern, '_', '.', '-'): %r" % (account_id,))
    return account_id


def create_account(admin_url: str, account_id: str, initial_equity: Decimal, clock: Optional[Clock] = None) -> bool:
    """Legt ein PAPER-Konto an. Rueckgabe: neu angelegt? (False: bestand schon - es wird NICHT veraendert,
    auch nicht bei anderem Startkapital.) Braucht eine Administrator-Verbindung."""
    check_account_id(account_id)
    if not is_positive_decimal(initial_equity):
        raise AccountError("Startkapital muss eine positive, endliche Decimal-Zahl sein.")
    clock = clock if clock is not None else SystemClock()
    conn = connect(admin_url)
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts (account_id, initial_equity, balance, equity, peak_equity, day, day_start_equity)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (account_id) DO NOTHING",
                (account_id, initial_equity, initial_equity, initial_equity, initial_equity,
                 clock.now().date(), initial_equity),
            )
            created = cur.rowcount == 1
            if created:  # in derselben Transaktion: ohne Protokolleintrag kein Konto
                log_event(cur, clock, EventType.ACCOUNT_CREATED,
                          {"account_id": account_id, "initial_equity": initial_equity, "mode": "paper"})
        return created
    finally:
        conn.close()


def list_accounts(url: str) -> List[Tuple[str, Decimal, Decimal]]:
    """(Konto, Startkapital, Equity) aller Konten."""
    conn = connect(url)
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT account_id, initial_equity, equity FROM accounts ORDER BY account_id")
            return [(r[0], r[1], r[2]) for r in cur.fetchall()]
    finally:
        conn.close()


def main(argv: Sequence[str]) -> int:
    if not argv or argv[0] not in ("list", "create") or (argv[0] == "create" and len(argv) != 3):
        print(__doc__, file=sys.stderr)
        return 2
    command = argv[0]
    variable = "ADMIN_DATABASE_URL" if command == "create" else "DATABASE_URL"
    url = os.environ.get(variable)
    if not url:
        print("%s fehlt." % variable, file=sys.stderr)
        return 2
    try:
        if command == "create":
            try:
                equity = Decimal(argv[2])
            except InvalidOperation:
                raise AccountError("Startkapital ist keine Zahl: %r" % argv[2]) from None
            created = create_account(url, argv[1], equity)
            print("Konto %s angelegt." % argv[1] if created else "Konto %s bestand schon (unveraendert)." % argv[1])
        else:
            for account_id, initial, equity in list_accounts(url):
                print("%s  Startkapital %s  Equity %s" % (account_id, initial, equity))
        return 0
    except Exception as exc:  # noqa: BLE001 - Kommandozeile: verstaendliche Meldung statt Stacktrace
        print("FEHLER: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
