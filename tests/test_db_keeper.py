"""ConnectionKeeper ohne Datenbank (AP-11): wann neu verbunden wird, wie oft, und dass es nie endlos wartet."""

from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg", reason="psycopg nicht installiert - Datenbank-Tests uebersprungen")
from trading.persistence.db import ConnectionKeeper, DatabaseUnavailableError  # noqa: E402


class FakeConn:
    def __init__(self) -> None:
        self.closed = False
        self.broken = False

    def close(self) -> None:
        self.closed = True


class Server:
    """Liefert Verbindungen; `down` Versuche lang ist er nicht erreichbar."""

    def __init__(self) -> None:
        self.down = 0
        self.calls = 0
        self.made = []

    def __call__(self, url, autocommit):
        self.calls += 1
        if self.down > 0:
            self.down -= 1
            raise psycopg.OperationalError("nicht erreichbar")
        self.made.append(FakeConn())
        return self.made[-1]


def keeper(server, waits):
    return ConnectionKeeper("postgresql://test", connector=server, sleep=waits.append)


def test_healthy_connection_is_reused():
    server, waits = Server(), []
    k = keeper(server, waits)
    assert k.get() is k.get() is server.made[0]
    assert server.calls == 1 and k.reconnects == 0 and waits == []


@pytest.mark.parametrize("flag", ["broken", "closed"])
def test_lost_connection_is_replaced(flag):
    server, waits = Server(), []
    k = keeper(server, waits)
    setattr(server.made[0], flag, True)
    assert k.get() is server.made[1]
    assert k.reconnects == 1 and waits == []


def test_waits_between_attempts_until_the_server_is_back():
    server, waits = Server(), []
    k = keeper(server, waits)
    server.made[0].broken = True
    server.down = 2
    assert k.get() is server.made[1]
    assert waits == [0.5, 1.0] and k.reconnects == 1


def test_gives_up_after_bounded_attempts():
    server, waits = Server(), []
    k = keeper(server, waits)
    server.made[0].broken = True
    server.down = 100
    with pytest.raises(DatabaseUnavailableError):
        k.get()
    assert waits == [0.5, 1.0, 2.0] and server.calls == 1 + 4
    server.down = 0  # spaeter wieder erreichbar: der naechste Aufruf verbindet neu
    assert k.get() is server.made[1]


def test_start_fails_fast_without_waiting():
    """Beim Start (falsche Adresse, falsches Passwort) nicht warten - sofort melden."""
    server, waits = Server(), []
    server.down = 1
    with pytest.raises(psycopg.OperationalError):
        keeper(server, waits)
    assert waits == [] and server.calls == 1


def test_closed_keeper_never_reconnects():
    server, waits = Server(), []
    k = keeper(server, waits)
    k.close()
    with pytest.raises(DatabaseUnavailableError):
        k.get()
    assert server.calls == 1
