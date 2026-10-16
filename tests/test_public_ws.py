"""WebSocket-Zugang der Marktueberwachung (AP-15, D-026) gegen ein echtes WebSocket-Gegenstueck (wsproto, Serverseite)
ueber ein lokales Socket-Paar - ohne Netz. wsproto braucht Python >= 3.10; unter 3.9 werden diese Tests uebersprungen."""

from __future__ import annotations

import socket
import threading

import pytest

pytest.importorskip("wsproto", reason="wsproto nicht installiert (Python < 3.10) - WebSocket-Tests uebersprungen")

from wsproto import ConnectionType, WSConnection  # noqa: E402
from wsproto.events import AcceptConnection, CloseConnection, Ping, Pong, Request, TextMessage  # noqa: E402

from trading.marketdata.public_ws import ALLOWED_ENDPOINTS, PublicWebSocket  # noqa: E402
from trading.marketdata.stream import ConnectionLost  # noqa: E402


class WsServer(threading.Thread):
    """Gegenstueck mit wsproto (Serverseite) auf einem Socket-Paar - kein Netz."""

    def __init__(self, sock, actions):
        super().__init__(daemon=True)
        self.sock, self.actions, self.received, self.pongs = sock, actions, [], 0
        self.ws = WSConnection(ConnectionType.SERVER)

    def events(self):
        data = self.sock.recv(65536)
        self.ws.receive_data(data or None)
        return list(self.ws.events())

    def run(self):
        for ev in self.events():
            if isinstance(ev, Request):
                self.sock.sendall(self.ws.send(AcceptConnection()))
        for action in self.actions:
            kind, value = action
            if kind == "text":
                self.sock.sendall(self.ws.send(TextMessage(data=value)))
            elif kind == "parts":
                for i, part in enumerate(value):
                    self.sock.sendall(self.ws.send(TextMessage(data=part, message_finished=i == len(value) - 1)))
            elif kind == "ping":
                self.sock.sendall(self.ws.send(Ping(payload=b"x")))
            elif kind == "expect_pong":
                while not self.pongs:
                    for ev in self.events():
                        if isinstance(ev, Pong):
                            self.pongs += 1
            elif kind == "expect":
                while len(self.received) < value:
                    for ev in self.events():
                        if isinstance(ev, TextMessage):
                            self.received.append(ev.data)
                        elif isinstance(ev, Pong):
                            self.pongs += 1
            elif kind == "close":
                self.sock.sendall(self.ws.send(CloseConnection(code=1000)))


def ws_pair(actions):
    client_sock, server_sock = socket.socketpair()
    server = WsServer(server_sock, actions)
    server.start()
    opened = []

    def factory(host, port, timeout):
        opened.append((host, port))
        return client_sock

    return PublicWebSocket(socket_factory=factory), server, opened


def test_websocket_handshake_messages_ping_and_close():
    ws, server, opened = ws_pair([("text", "hallo"), ("parts", ["ge", "teilt"]), ("ping", None),
                                  ("expect_pong", None), ("expect", 1), ("close", None)])
    ws.connect("wss://ws.bitget.com/v2/ws/public")
    assert opened == [("ws.bitget.com", 443)]
    assert ws.recv(2) == "hallo" and ws.recv(2) == "geteilt"
    assert ws.recv(0.5) is None  # Ping des Servers: beantwortet, keine Textnachricht
    ws.send("ping")
    with pytest.raises(ConnectionLost):
        for _ in range(10):
            ws.recv(2)
    server.join(5)
    assert server.received == ["ping"] and server.pongs == 1
    ws.close()


def test_websocket_refuses_oversized_messages(monkeypatch):
    from trading.marketdata import public_ws

    monkeypatch.setattr(public_ws, "MAX_MESSAGE_BYTES", 5)
    ws, server, opened = ws_pair([("parts", ["hallo ", "welt"])])
    with pytest.raises(ConnectionLost, match="zu gross"):  # schon beim Verbinden oder beim ersten Lesen
        ws.connect("wss://ws.bitget.com/v2/ws/public")
        for _ in range(5):
            ws.recv(1)
    server.join(5)


def test_websocket_returns_none_on_silence_and_refuses_other_addresses():
    ws, server, opened = ws_pair([("expect", 1)])
    ws.connect("wss://ws-feed.exchange.coinbase.com")
    assert ws.recv(0.05) is None
    ws.send("x")
    server.join(5)
    ws.close()
    for bad in ("wss://example.com/", "ws://ws.bitget.com/v2/ws/public", "wss://ws.bitget.com/v2/ws/private"):
        other = PublicWebSocket(socket_factory=lambda *a: pytest.fail("darf nicht verbinden"))
        with pytest.raises(ConnectionLost):
            other.connect(bad)
    assert ALLOWED_ENDPOINTS == {"wss://ws.bitget.com/v2/ws/public", "wss://ws-feed.exchange.coinbase.com"}
