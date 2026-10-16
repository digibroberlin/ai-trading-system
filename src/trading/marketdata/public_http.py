"""Der EINZIGE Netzwerkzugang des Systems (AP-14, D-025): oeffentliche Marktdaten LESEN - sonst nichts.

* Nur GET und nur HTTPS, nur an fest eingetragene Hosts oeffentlicher Marktdaten-Schnittstellen (`ALLOWED_HOSTS`).
  Weiterleitungen werden NICHT verfolgt (sonst koennte eine Antwort auf einen anderen Host umlenken).
* Keine Zugangsdaten: ausser `User-Agent` und `Accept` gehen keine Kopfzeilen hinaus, keine Cookies, kein Inhalt.
* Zeitlimit je Anfrage, Hoechstgroesse der Antwort, Zahlen als `Decimal` (nie float - Kurse bleiben exakt).
* Fehler werden als `FetchError` gemeldet; `retryable` sagt, ob ein spaeterer Versuch sinnvoll ist (429, 5xx, Netz).
Eine Order-Schnittstelle gibt es hier nicht und darf es nie geben (siehe tests/test_no_real_trading.py).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional

ALLOWED_HOSTS = frozenset({"api.exchange.coinbase.com", "api.bitget.com"})
MAX_RESPONSE_BYTES = 4_000_000
DEFAULT_TIMEOUT_S = 15.0
USER_AGENT = "ai-trading-system-marketdata/1 (nur lesend)"

JsonGet = Callable[[str, Mapping[str, str]], Any]


class FetchError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None  # urllib meldet dann den 3xx-Status als Fehler, statt dem neuen Ziel zu folgen


def checked_url(url: str, params: Mapping[str, str]) -> str:
    """Vollstaendige Adresse - nur wenn sie erlaubt ist (vor jedem Netzwerkzugriff gepruefter Teil)."""
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.port not in (None, 443)
            or parts.username or parts.password or parts.query or parts.fragment):
        raise FetchError("Adresse nicht erlaubt: nur HTTPS-GET an %s ohne eigene Parameter." % sorted(ALLOWED_HOSTS))
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in params.items()):
        raise FetchError("Parameter muessen Text sein.")
    return url + ("?" + urllib.parse.urlencode(sorted(params.items())) if params else "")


class PublicHttpGet:
    """Ruft eine erlaubte Adresse per GET ab und liefert das JSON (Zahlen als Decimal)."""

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        if not 0 < timeout_s <= 60:
            raise ValueError("timeout_s muss zwischen 0 und 60 Sekunden liegen.")
        self._timeout = timeout_s
        self._opener = urllib.request.build_opener(_NoRedirect())

    def __call__(self, url: str, params: Mapping[str, str]) -> Any:
        request = urllib.request.Request(checked_url(url, params), method="GET",
                                         headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise FetchError("HTTP %d" % exc.code, exc.code, exc.code == 429 or exc.code >= 500) from None
        except (urllib.error.URLError, OSError) as exc:  # Zeitlimit, Verbindungsabbruch, DNS ...
            raise FetchError("Netzwerkfehler: %s" % type(exc).__name__, None, True) from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise FetchError("Antwort groesser als %d Bytes." % MAX_RESPONSE_BYTES)
        return parse_json(body)


def parse_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise FetchError("Keine gueltige JSON-Antwort.") from None


__all__ = ["ALLOWED_HOSTS", "FetchError", "JsonGet", "PublicHttpGet", "checked_url", "parse_json"]
