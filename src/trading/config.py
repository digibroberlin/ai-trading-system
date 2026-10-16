"""Konfiguration aus Umgebungsvariablen.

Sicherheitsprinzipien:
* Es gibt nur den Modus PAPER. Jeder andere Wert bricht den Start ab.
* Boersen-Schluessel duerfen in dieser Phase nicht gesetzt sein; das System verweigert
  dann den Start (lieber ein Fehlalarm als ein versehentlich vorhandener Handelszugang).
* Es wird nichts fest im Code gespeichert. Geheimnisse kommen nur aus der Umgebung.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Mapping, Optional


class TradingMode(str, Enum):
    PAPER = "paper"
    # Bewusst KEIN "live". Ein Live-Modus waere eine eigene, ausdruecklich freigegebene Phase.


class ConfigError(RuntimeError):
    """Ungueltige oder unsichere Konfiguration - Programmstart wird abgebrochen."""


_FORBIDDEN_CREDENTIAL_ENV = re.compile(
    r"^(COINBASE|BITGET|CB|BG)[A-Z0-9_]*(KEY|SECRET|PASSPHRASE|TOKEN)[A-Z0-9_]*$"
)

# Namensteile, die auf eine Boerse oder eine Boersen-Bibliothek deuten (Audit M3: auch CDP = Coinbase Developer Platform,
# weitere grosse Boersen und CCXT). Geprueft wird JEDER Teil des Namens (MY_BINANCE_API_KEY, FREQTRADE__EXCHANGE__KEY);
# lange Namen auch als Anfang eines Teils (BINANCEUS_..., KRAKENFUTURES_..., BINANCEAPIKEY).
_EXCHANGE_PREFIXES = frozenset({
    "COINBASE", "CDP", "CB", "BITGET", "BG", "BINANCE", "KRAKEN", "BYBIT", "OKX", "OKEX", "KUCOIN", "GATE", "GATEIO",
    "MEXC", "HTX", "HUOBI", "BITFINEX", "BITMEX", "DERIBIT", "BITSTAMP", "GEMINI", "CRYPTOCOM", "HYPERLIQUID",
    "BITVAVO", "CCXT", "EXCHANGE",
})
_LONG_EXCHANGE_PREFIXES = tuple(sorted(n for n in _EXCHANGE_PREFIXES if len(n) >= 5))
_SECRET_PARTS = ("KEY", "SECRET", "PASSPHRASE", "TOKEN", "PASSWORD", "PRIVATE", "PEM", "UID", "CREDENTIAL")
# Allgemeine Namen, unter denen Boersen-Schluessel oft abgelegt werden. Bewusst KEINE breite Regel wie "enthaelt SECRET":
# Cloud- und Entwicklungsumgebungen haben eigene Zugaenge (z. B. AWS_SECRET_ACCESS_KEY, GH_TOKEN), die mit Handel nichts
# zu tun haben; sie wuerden sonst jeden Start verhindern.
_GENERIC_NAMES = frozenset({
    "API_KEY", "APIKEY", "API_SECRET", "APISECRET", "API_PASSPHRASE", "PASSPHRASE", "SECRET_KEY", "PRIVATE_KEY",
    "API_KEY_NAME", "API_KEY_ID", "API_PRIVATE_KEY", "TRADING_KEY", "TRADING_SECRET",
})
# Inhalte, die nach einem Boersen-Schluessel aussehen, egal unter welchem Namen (Coinbase-CDP-Schluesselname bzw. EC-Schluessel).
_CREDENTIAL_VALUE = re.compile(r"organizations/[0-9A-Za-z-]+/apiKeys/|BEGIN EC PRIVATE KEY")


def _normalized(name: str) -> str:
    """Grossschrift, Teile durch "_" getrennt - auch bei camelCase (binanceApiSecret -> BINANCE_API_SECRET)."""
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return re.sub(r"[^A-Z0-9]+", "_", split.upper()).strip("_")


def _mentions_exchange(parts: List[str]) -> bool:
    return any(p in _EXCHANGE_PREFIXES or p.startswith(_LONG_EXCHANGE_PREFIXES) for p in parts)


def _is_credential_name(name: str) -> bool:
    norm = _normalized(name)
    if _FORBIDDEN_CREDENTIAL_ENV.match(norm) or norm in _GENERIC_NAMES or "PASSPHRASE" in norm:
        return True
    return _mentions_exchange(norm.split("_")) and any(p in norm for p in _SECRET_PARTS)


def credential_variables(env: Mapping[str, str]) -> List[str]:
    """Namen aller gesetzten Umgebungsvariablen, die nach Boersen-Zugangsdaten aussehen (nie die Werte)."""
    found = []
    for name, value in env.items():
        if not value or not value.strip():
            continue
        if _is_credential_name(name) or _CREDENTIAL_VALUE.search(value):
            found.append(name)
    return sorted(found)


@dataclass(frozen=True)
class Settings:
    mode: TradingMode
    database_url: Optional[str] = field(default=None, repr=False)  # enthaelt Passwort -> nie anzeigen
    event_log_path: Optional[str] = None


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    env = os.environ if env is None else env

    # Jede Schreibweise zaehlt (trading_mode, Trading_Mode ...): sonst koennte eine zweite Variable unbemerkt bleiben.
    modes = [v for k, v in env.items() if _normalized(k) == "TRADING_MODE"] or [TradingMode.PAPER.value]
    for raw in modes:
        raw_mode = raw.strip().lower()
        if raw_mode != TradingMode.PAPER.value:
            raise ConfigError(
                "TRADING_MODE=%r ist nicht erlaubt. In dieser Phase ist nur 'paper' moeglich." % raw_mode
            )

    offending = credential_variables(env)
    if offending:
        raise ConfigError(
            "Boersen-Zugangsdaten gefunden (%s). Im PAPER-Modus duerfen keine "
            "Handelsschluessel gesetzt sein." % ", ".join(offending)
        )

    return Settings(
        mode=TradingMode.PAPER,
        database_url=env.get("DATABASE_URL") or None,
        event_log_path=env.get("EVENT_LOG_PATH") or None,
    )


def enforce_paper_process(env: Optional[Mapping[str, str]] = None) -> None:
    """Zentraler PAPER-Schutz fuer JEDEN Prozess (Audit M3).

    Wird beim ersten `import trading` ausgefuehrt (siehe `trading/__init__.py`). Damit prueft jeder Prozess, der irgendeinen
    Teil des Systems laedt - Vorfuehrung, Backtest, Migration, Pruefskripte, Test-Worker und kuenftige Programme -, vor allem
    anderen Modus und Umgebung. Bei Verstoss bricht der Import mit `ConfigError` ab: lieber kein Start als ein unsicherer.
    """
    load_settings(env)
