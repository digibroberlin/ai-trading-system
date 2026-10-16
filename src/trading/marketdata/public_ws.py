"""WebSocket-Zugang fuer die Marktueberwachung (AP-15, D-026) - zweite und letzte bewusste Netz-Ausnahme.

* Verbindet sich NUR mit fest eingetragenen, oeffentlichen Marktdaten-Adressen (`ALLOWED_ENDPOINTS`), immer ueber TLS
  mit Zertifikatspruefung. Keine Zugangsdaten, keine Anmeldung, keine Cookies.
* Gesendet werden nur Abos oeffentlicher Kanaele und "ping" - zur Laufzeit durch `allowed_message` erzwungen (AP-34) -
  sowie die vom WebSocket-Standard verlangten Antworten (Pong, Schliessen).
* Hoechstgroesse je Nachricht; Zeitlimit beim Verbinden; ein Abbruch wird als `ConnectionLost` gemeldet.
Die WebSocket-Rahmen erzeugt und liest `wsproto` (ohnehin Abhaengigkeit des Dashboards).
"""

from __future__ import annotations

import json
import socket
import ssl
from typing import Any, Callable, List, Optional

from wsproto import ConnectionType, WSConnection
from wsproto.events import AcceptConnection, CloseConnection, Ping, RejectConnection, Request, TextMessage

from trading.marketdata.stream import ConnectionLost

ALLOWED_ENDPOINTS = frozenset({"wss://ws.bitget.com/v2/ws/public", "wss://ws-feed.exchange.coinbase.com"})
MAX_MESSAGE_BYTES = 1_000_000
CONNECT_TIMEOUT_S = 10.0

SocketFactory = Callable[[str, int, float], Any]


def _tls_socket(host: str, port: int, timeout_s: float) -> Any:
    raw = socket.create_connection((host, port), timeout=timeout_s)
    return ssl.create_default_context().wrap_socket(raw, server_hostname=host)


PUBLIC_CHANNELS = frozenset({"matches", "heartbeat"})  # Coinbase; Bitget: Kerzen-Kanaele "candle..."


def allowed_message(text: str) -> bool:
    """Einzige Nachrichten, die je hinausgehen duerfen: "ping" oder ein Abo oeffentlicher Marktdaten-Kanaele."""
    if text == "ping":
        return True
    try:
        message = json.loads(text)
    except ValueError:
        return False
    if not isinstance(message, dict):  # die erlaubten Felder sind unten EXAKT vorgegeben - nichts darueber hinaus
        return False
    if message.get("op") == "subscribe" and set(message) == {"op", "args"} and isinstance(message["args"], list):
        return all(isinstance(a, dict) and set(a) == {"instType", "channel", "instId"}
                   and str(a["channel"]).startswith("candle") for a in message["args"])
    if message.get("type") == "subscribe" and set(message) == {"type", "product_ids", "channels"}:
        return isinstance(message["channels"], list) and set(message["channels"]) <= PUBLIC_CHANNELS
    return False


def split_endpoint(url: str) -> "tuple[str, str]":
    if url not in ALLOWED_ENDPOINTS:
        raise ConnectionLost("Adresse nicht erlaubt: %s" % url)
    rest = url[len("wss://"):]
    host, _, path = rest.partition("/")
    return host, "/" + path


class PublicWebSocket:
    def __init__(self, socket_factory: SocketFactory = _tls_socket, timeout_s: float = CONNECT_TIMEOUT_S) -> None:
        self._factory = socket_factory
        self._timeout = timeout_s
        self._sock: Any = None
        self._ws: Optional[WSConnection] = None
        self._texts: List[str] = []
        self._partial: List[str] = []

    def connect(self, url: str) -> None:
        host, target = split_endpoint(url)
        self.close()
        try:
            self._sock = self._factory(host, 443, self._timeout)
        except OSError as exc:
            raise ConnectionLost("%s nicht erreichbar (%s)" % (host, type(exc).__name__)) from None
        self._ws = WSConnection(ConnectionType.CLIENT)
        self._write(self._ws.send(Request(host=host, target=target)))
        accepted = False
        while not accepted:
            for event in self._read(self._timeout):
                if accepted:
                    self._on_event(event)  # kam im selben Paket wie die Bestaetigung - nicht verlieren
                elif isinstance(event, AcceptConnection):
                    accepted = True
                elif isinstance(event, RejectConnection):
                    self.close()
                    raise ConnectionLost("Verbindung abgelehnt (HTTP %s)" % event.status_code)

    def send(self, text: str) -> None:
        if not allowed_message(text):  # zur Laufzeit: nie etwas anderes als Abo oder Lebenszeichen (AP-34)
            raise ValueError("Nachricht nicht erlaubt: nur Abos oeffentlicher Kanaele und 'ping'.")
        if self._ws is None:
            raise ConnectionLost("nicht verbunden")
        self._write(self._ws.send(TextMessage(data=text)))

    def recv(self, timeout_s: float) -> Optional[str]:
        if self._ws is None:
            raise ConnectionLost("nicht verbunden")
        if not self._texts:
            try:
                events = self._read(timeout_s)
            except socket.timeout:
                return None
            for event in events:
                self._on_event(event)
        return self._texts.pop(0) if self._texts else None

    def close(self) -> None:
        sock, self._sock, self._ws = self._sock, None, None
        self._texts, self._partial = [], []
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------------ intern

    def _on_event(self, event: Any) -> None:
        assert self._ws is not None
        if isinstance(event, TextMessage):
            self._partial.append(event.data)
            if sum(len(p) for p in self._partial) > MAX_MESSAGE_BYTES:
                self.close()
                raise ConnectionLost("Nachricht zu gross")
            if event.message_finished:
                self._texts.append("".join(self._partial))
                self._partial = []
        elif isinstance(event, Ping):
            self._write(self._ws.send(event.response()))
        elif isinstance(event, CloseConnection):
            try:
                self._write(self._ws.send(event.response()))
            finally:
                self.close()
            raise ConnectionLost("Gegenstelle schliesst (%s)" % event.code)
        # Binaerdaten und Pongs auf Protokoll-Ebene werden ignoriert

    def _read(self, timeout_s: float) -> List[Any]:
        assert self._ws is not None and self._sock is not None
        self._sock.settimeout(timeout_s)
        try:
            data = self._sock.recv(65536)
        except socket.timeout:
            raise
        except OSError as exc:
            self.close()
            raise ConnectionLost("Lesefehler (%s)" % type(exc).__name__) from None
        if not data:
            self.close()
            raise ConnectionLost("Gegenstelle hat die Verbindung beendet")
        self._ws.receive_data(data)
        return list(self._ws.events())

    def _write(self, data: bytes) -> None:
        try:
            self._sock.sendall(data)
        except OSError as exc:
            self.close()
            raise ConnectionLost("Schreibfehler (%s)" % type(exc).__name__) from None


__all__ = ["ALLOWED_ENDPOINTS", "PublicWebSocket", "allowed_message", "split_endpoint"]
