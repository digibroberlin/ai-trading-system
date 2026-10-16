"""Prueft vorhandene API-Konfigurationen auf Handelsberechtigungen (AP-34, D-033) - gibt NIE einen Wert aus.

  python scripts/check_api_config.py                                   # Umgebung dieses Prozesses
  python scripts/check_api_config.py --env-file ~/trading-secrets.env   # zusaetzlich Passwort-Dateien (mehrfach moeglich)

Geprueft wird (ausgegeben werden nur NAMEN und die Einordnung - nie Werte, nicht einmal deren Laenge):
  * Boersen-Zugangsdaten - nach denselben Regeln wie der PAPER-Schutz (`trading.config.credential_variables`, auch an
    der Form des Wertes erkannt) -> VERBOTEN. Mit ihnen startet das System nicht. Ob ein solcher Schluessel Handel oder
    Abhebungen erlaubt, laesst sich nur bei der Boerse selbst pruefen; das kommt erst mit AP-29 (Freigabe B-003).
    Bis dahin gilt: Es darf gar kein Boersen-Schluessel vorhanden sein.
  * TRADING_MODE (jede Schreibweise) muss `paper` sein -> sonst VERBOTEN.
  * Schalter, die nach Echtgeld klingen (LIVE, MAINNET, REAL_MONEY ...), eingeschaltet -> VERBOTEN; vorhanden -> HINWEIS.
  * Freigaben fuer Paper-Handel (TRADING_ENGINE_ENABLED + TRADING_ENGINE_APPROVAL) und KI (TRADING_AI_APPROVAL,
    ANTHROPIC_API_KEY) -> HINWEIS (erlaubt, aber bewusst).
  * Passwort-Dateien: nur fuer den Eigentuemer lesbar (600/400) und nicht im Projektordner -> sonst DATEI.
Rueckgabe: 0 in Ordnung, 1 etwas VERBOTENES gefunden, 2 Datei-Problem (nicht lesbar, Rechte, Ort).

Das Skript laedt `src/trading/config.py` direkt (ohne `import trading`): Der PAPER-Schutz des Pakets wuerde bei
vorhandenen Boersen-Schluesseln sofort abbrechen - genau dann soll diese Pruefung aber noch berichten koennen.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
TRUTHY = {"1", "true", "yes", "on", "ja"}
LIVE_PARTS = frozenset({"LIVE", "MAINNET", "REALMONEY"})  # ganze Namensteile (KEEPALIVES ist kein LIVE)
APPROVALS = ("TRADING_ENGINE_ENABLED", "TRADING_ENGINE_APPROVAL", "TRADING_AI_APPROVAL")
AI_KEYS = ("ANTHROPIC_API_KEY",)
# Zugaenge, die das System kennt und braucht (Datenbank, KI, Alarm-Kanal) - alles andere mit Key/Secret/Token im Namen
# wird als HINWEIS gemeldet: Es koennte ein Boersen-Schluessel unter einem allgemeinen Namen sein.
KNOWN_ACCESS = frozenset({"DATABASE_URL", "ADMIN_DATABASE_URL", "POSTGRES_PASSWORD", "APP_DB_PASSWORD", "TEST_ADMIN_URL",
                          "TEST_APP_PASSWORD", "ANTHROPIC_API_KEY", "ALERT_WEBHOOK_URL"})
SECRET_PARTS = frozenset({"KEY", "SECRET", "TOKEN", "PASSPHRASE", "PASSWORD", "CREDENTIAL", "CREDENTIALS", "APIKEY"})


def _load_config() -> Any:
    spec = importlib.util.spec_from_file_location("trading_config_standalone", ROOT / "src" / "trading" / "config.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses brauchen das Modul im Verzeichnis
    spec.loader.exec_module(module)
    return module


CONFIG = _load_config()


@dataclass(frozen=True)
class Finding:
    level: str  # VERBOTEN, DATEI, HINWEIS
    source: str  # "Umgebung" oder Dateiname
    name: str
    text: str


def parse_env_file(text: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        name, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name.strip()] = value
    return values


def looks_live(normalized_name: str) -> bool:
    parts = set(normalized_name.split("_"))
    return bool(parts & LIVE_PARTS) or ("REAL" in parts and bool(parts & {"MONEY", "TRADING"}))


def check_values(values: Mapping[str, str], source: str) -> List[Finding]:
    found: List[Finding] = []
    for name in CONFIG.credential_variables(values):
        found.append(Finding("VERBOTEN", source, name, "Boersen-Zugangsdaten - im PAPER-Modus nicht erlaubt; entfernen. "
                                                         "Berechtigungen (Handel/Abhebung) prueft erst AP-29 bei der Boerse."))
    for name, value in values.items():
        norm = CONFIG._normalized(name)
        if norm == "TRADING_MODE" and value.strip().lower() != "paper":
            found.append(Finding("VERBOTEN", source, name, "Modus ist nicht 'paper' - das System startet so nicht."))
        elif norm not in APPROVALS and looks_live(norm):
            if value.strip().lower() in TRUTHY:
                found.append(Finding("VERBOTEN", source, name, "eingeschalteter Echtgeld-Schalter - gibt es im System "
                                                               "nicht und darf es ohne separate Freigabe nicht geben."))
            elif value.strip():
                found.append(Finding("HINWEIS", source, name, "Name klingt nach Echtgeld (ausgeschaltet) - pruefen."))
    forbidden = {f.name for f in found if f.level == "VERBOTEN"}
    for name, value in values.items():
        norm = CONFIG._normalized(name)
        if (value.strip() and name not in forbidden and norm not in KNOWN_ACCESS
                and set(norm.split("_")) & SECRET_PARTS):
            found.append(Finding("HINWEIS", source, name, "unbekannter Zugang - pruefen, ob es ein Boersen-Schluessel "
                                                          "ist (dann entfernen)."))
    if values.get("TRADING_ENGINE_ENABLED") == "1" and values.get("TRADING_ENGINE_APPROVAL"):
        found.append(Finding("HINWEIS", source, "TRADING_ENGINE_APPROVAL", "Paper-Handel ist freigegeben (kein Echtgeld)."))
    if values.get("TRADING_AI_APPROVAL"):
        found.append(Finding("HINWEIS", source, "TRADING_AI_APPROVAL", "KI-Aufrufe freigegeben - Kosten nur im Budget."))
    for name in AI_KEYS:
        if (values.get(name) or "").strip():
            found.append(Finding("HINWEIS", source, name, "KI-Schluessel vorhanden - bezahlte Aufrufe nur mit Freigabe "
                                                          "und Budget (B-004)."))
    return found


def check_file(path: Path) -> List[Finding]:
    name = path.name
    try:
        real = path.resolve()
        mode = stat.S_IMODE(os.stat(real).st_mode)
        text = real.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [Finding("DATEI", name, "-", "nicht lesbar (%s)" % type(exc).__name__)]
    found: List[Finding] = []
    if mode & 0o077:
        found.append(Finding("DATEI", name, "-", "Rechte %o - nur fuer den Eigentuemer erlauben (chmod 600)" % mode))
    if ROOT in real.parents:
        found.append(Finding("DATEI", name, "-", "liegt im Projektordner - koennte eingecheckt werden; verschieben"))
    return found + check_values(parse_env_file(text), name)


def report(findings: Sequence[Finding], out: Callable[[str], None]) -> int:
    if not any(f.level == "VERBOTEN" for f in findings):
        out("OK: keine Boersen-Zugangsdaten, kein Echtgeld-Schalter, Modus PAPER.")
    for f in findings:
        out("%-8s %-12s %s: %s" % (f.level, f.source, f.name, f.text))
    if any(f.level == "VERBOTEN" for f in findings):
        return 1
    return 2 if any(f.level == "DATEI" for f in findings) else 0


def main(argv: Optional[Sequence[str]] = None, env: Optional[Mapping[str, str]] = None,
         out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description="API-Konfiguration auf Handelsberechtigungen pruefen (AP-34)")
    parser.add_argument("--env-file", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    findings = check_values(os.environ if env is None else env, "Umgebung")
    for path in args.env_file:
        findings += check_file(path)
    return report(findings, out)


if __name__ == "__main__":
    sys.exit(main())
