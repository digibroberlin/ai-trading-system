"""Strukturiertes Logging fuer den Betrieb (AP-25, D-027): eine JSON-Zeile je Meldung, nie ein Geheimnis.

* Jede Zeile: Zeit (UTC), Stufe, Quelle, Meldung und Zusatzfelder (`extra={...}`), bei Fehlern der Ablauf (Traceback).
* Vor dem Schreiben wird JEDER Text geschwaerzt: Passwoerter in Datenbank-Adressen, Zuweisungen an Namen mit Key,
  Secret, Passwort, Passphrase, Token oder Credential, Authorization-/Cookie-Kopfzeilen und Schluessel im Format von
  KI-Anbietern. Lieber zu viel schwaerzen als ein Geheimnis im Protokoll.
* Das Ereignisprotokoll (Hash-Kette, Audit) bleibt davon unberuehrt - dieses Logging ist nur fuer den Betrieb
  (Fehlersuche, Alarme), nicht der Nachweis.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import IO, Any, Dict, List, Optional, Pattern, Tuple

HANDLER_NAME = "trading-json"
_STANDARD = set(logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None).__dict__) | {"message", "asctime"}
_REDACTIONS: List[Tuple[Pattern[str], str]] = [
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://[^:/\s@]+:)[^@\s]+@"), r"\1***@"),  # Passwort in einer Adresse
    (re.compile(r"(?i)\b(authorization|cookie)(\s*[:=]\s*)[^\r\n]+"), r"\1\2***"),
    (re.compile(r"(?i)\b([a-z0-9_\-]*(?:api[_\-]?key|secret|passwor[dt]|passphrase|token|credential))"
                r"(\"?\s*[:=]\s*\"?)[^\s\",;}]+"), r"\1\2***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-***"),  # Schluesselformat von KI-Anbietern
]


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _plain(value: Any) -> Any:
    if isinstance(value, (bool, int)) or value is None:
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return redact(str(value))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: Dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                data[key] = _plain(value)
        if record.exc_info:
            data["error"] = redact(self.formatException(record.exc_info))
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO", stream: Optional[IO[str]] = None) -> logging.Handler:
    """JSON-Ausgabe fuer alle `trading.*`-Logger. Mehrfacher Aufruf ersetzt den eigenen Handler (keine Doppelzeilen)."""
    logger = logging.getLogger("trading")
    for handler in list(logger.handlers):
        if handler.get_name() == HANDLER_NAME:
            logger.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return handler


__all__ = ["JsonFormatter", "configure_logging", "redact"]
