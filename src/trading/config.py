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
from typing import Mapping, Optional


class TradingMode(str, Enum):
    PAPER = "paper"
    # Bewusst KEIN "live". Ein Live-Modus waere eine eigene, ausdruecklich freigegebene Phase.


class ConfigError(RuntimeError):
    """Ungueltige oder unsichere Konfiguration - Programmstart wird abgebrochen."""


_FORBIDDEN_CREDENTIAL_ENV = re.compile(
    r"^(COINBASE|BITGET|CB|BG)[A-Z0-9_]*(KEY|SECRET|PASSPHRASE|TOKEN)[A-Z0-9_]*$"
)


@dataclass(frozen=True)
class Settings:
    mode: TradingMode
    database_url: Optional[str] = field(default=None, repr=False)  # enthaelt Passwort -> nie anzeigen
    event_log_path: Optional[str] = None


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    env = os.environ if env is None else env

    raw_mode = env.get("TRADING_MODE", TradingMode.PAPER.value).strip().lower()
    if raw_mode != TradingMode.PAPER.value:
        raise ConfigError(
            "TRADING_MODE=%r ist nicht erlaubt. In dieser Phase ist nur 'paper' moeglich." % raw_mode
        )

    offending = sorted(k for k, v in env.items() if v and _FORBIDDEN_CREDENTIAL_ENV.match(k))
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
