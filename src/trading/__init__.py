"""Autonomer KI-Krypto-Trading-Bot - ausschliesslich PAPER-Modus."""

from __future__ import annotations

from trading.config import enforce_paper_process as _enforce_paper_process

__version__ = "0.1.0"

# Zentraler PAPER-Schutz (Audit M3): Jeder Prozess, der das Paket laedt, prueft zuerst Modus und Umgebung.
# Schlaegt die Pruefung fehl, ist das Paket nicht benutzbar (ConfigError beim Import).
_enforce_paper_process()
