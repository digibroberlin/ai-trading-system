"""Alarm-Pruefung fuer den Betrieb (AP-28, D-030) - alle 5 Minuten per systemd-Zeitplan, nur Python-Standardbibliothek.

  python3 scripts/ops/alert_check.py                       # /api/health pruefen, bei Bedarf melden
  python3 scripts/ops/alert_check.py --event "Sicherung fehlgeschlagen"   # Einzelmeldung (z. B. aus systemd OnFailure)

Was gemeldet wird:
  * jeder Wechsel des Gesamtzustands (OK / WARN / CRITICAL / NICHT_ERREICHBAR),
  * jede NEUE Einzelmeldung bei gleichem Zustand (z. B. "KI-Budget gesperrt" zusaetzlich zu einer Warnung),
  * ein anhaltend schlechter Zustand erneut alle `ALERT_REPEAT_HOURS` (Standard 6),
  * Rueckkehr zu OK ("wieder in Ordnung").
Wohin: Immer ins Systemprotokoll (stdout -> journald). Zusaetzlich an `ALERT_WEBHOOK_URL`, wenn der Eigner einen Kanal
eingerichtet hat (nur HTTPS; z. B. ntfy, Slack oder eine Telegram-Bruecke - Wahl des Eigners, B-005). Die Adresse
wird nie ausgegeben (sie kann ein Geheimnis enthalten). Rueckgabe: 0 OK, 1 WARN, 2 CRITICAL/nicht erreichbar.
Der Zustand liegt in einer kleinen JSON-Datei (`ALERT_STATE_FILE`, Standard /var/lib/ai-trading/alert-state.json).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

HEALTH_URL = "http://127.0.0.1:8000/api/health"
DEFAULT_STATE = "/var/lib/ai-trading/alert-state.json"
EXIT = {"OK": 0, "WARN": 1, "CRITICAL": 2, "NICHT_ERREICHBAR": 2}

Health = Tuple[str, List[str]]  # (Gesamtzustand, Einzelmeldungen)


def fetch_health(url: str = HEALTH_URL, timeout: float = 10.0) -> Health:
    """Gesundheit des Paper-Dienstes. HTTP 503 ist eine gueltige Antwort (CRITICAL); keine Antwort: NICHT_ERREICHBAR."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(1_000_000)
    except urllib.error.HTTPError as exc:
        body = exc.read(1_000_000) if exc.code == 503 else b""
        if not body:
            return "NICHT_ERREICHBAR", ["HTTP %d" % exc.code]
    except (urllib.error.URLError, OSError) as exc:
        return "NICHT_ERREICHBAR", ["keine Antwort (%s)" % type(exc).__name__]
    try:
        data = json.loads(body.decode("utf-8"))
        status = str(data.get("status") or ("OK" if data.get("ok") else "CRITICAL"))
        findings = ["%s: %s" % (c.get("name"), c.get("detail")) for c in data.get("checks", [])
                    if c.get("status") != "OK"]
    except (ValueError, AttributeError):
        return "NICHT_ERREICHBAR", ["unlesbare Antwort"]
    return (status if status in EXIT else "CRITICAL"), findings


def decide(state: Mapping[str, Any], health: Health, now: datetime, repeat: timedelta) -> Optional[str]:
    """Meldung oder None. `state`: zuletzt gemeldeter Zustand, Einzelmeldungen und Zeitpunkt."""
    status, findings = health
    before, seen = state.get("status", "OK"), set(state.get("findings", []))
    last = state.get("sent_at")
    new = [f for f in findings if f not in seen]
    if status != before:
        if status == "OK":
            return "Wieder in Ordnung (vorher %s)." % before
        return "%s: %s" % (status, "; ".join(findings) or "ohne Einzelheiten")
    if status == "OK":
        return None
    if new:
        return "%s, neu: %s" % (status, "; ".join(new))
    if last is None or now - datetime.fromisoformat(last) >= repeat:
        return "%s besteht weiter: %s" % (status, "; ".join(findings) or "ohne Einzelheiten")
    return None


def send_webhook(url: str, text: str, timeout: float = 10.0) -> None:
    if not url.startswith("https://"):
        raise ValueError("ALERT_WEBHOOK_URL muss mit https:// beginnen.")
    payload = json.dumps({"text": "[ai-trading] " + text}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read(1000)


def load_state(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def main(argv: Optional[Sequence[str]] = None, env: Optional[Mapping[str, str]] = None,
         fetch: Callable[[], Health] = fetch_health, send: Callable[[str, str], None] = send_webhook,
         now: Optional[datetime] = None, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description="Alarm-Pruefung (AP-28)")
    parser.add_argument("--event", help="Einzelmeldung senden (z. B. aus systemd OnFailure)")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    webhook = env.get("ALERT_WEBHOOK_URL", "").strip()

    def notify(text: str) -> None:
        out("ALARM: %s" % text)
        if webhook:
            try:
                send(webhook, text)
            except Exception as exc:  # noqa: BLE001 - der Kanal ist kaputt: wenigstens im Systemprotokoll
                out("ALARM-KANAL FEHLGESCHLAGEN (%s) - Meldung nur im Systemprotokoll." % type(exc).__name__)

    if args.event:
        notify(args.event)
        return 2
    path = Path(env.get("ALERT_STATE_FILE", DEFAULT_STATE))
    state = load_state(path)
    health = fetch()
    repeat = timedelta(hours=float(env.get("ALERT_REPEAT_HOURS", "6")))
    message = decide(state, health, now, repeat)
    if message is not None:
        notify(message)
        state = {"status": health[0], "findings": health[1], "sent_at": now.isoformat()}
        save_state(path, state)
    return EXIT.get(health[0], 2)


if __name__ == "__main__":
    sys.exit(main())
