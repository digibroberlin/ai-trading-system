"""Server-Haertung, Deployment, Alarme und Serverkosten (AP-28, D-030) - nur Trockenlauf, kein echter Server.

Die Skripte laufen ohne `--apply` und duerfen dann nichts veraendern; geprueft wird, WAS sie tun wuerden.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading.audit.costs import CostCategory, operating_costs_from_env

ROOT = Path(__file__).resolve().parent.parent
D = Decimal
T0 = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
_spec = importlib.util.spec_from_file_location("alert_check", ROOT / "scripts" / "ops" / "alert_check.py")
alert = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(alert)


def dry(script, *args, **env):
    import os

    return subprocess.run(["bash", str(ROOT / "deploy" / "server" / script), *args], capture_output=True, text=True,
                          env=dict(os.environ, **env))


# ------------------------------------------------------------------ Haertung


def test_hardening_dry_run_changes_nothing_and_closes_everything_but_ssh():
    done = dry("harden.sh")
    out = done.stdout
    assert done.returncode == 0 and out.startswith("TROCKENLAUF")
    for setting in ("PermitRootLogin no", "PasswordAuthentication no", "KbdInteractiveAuthentication no",
                    "AllowUsers trading", "MaxAuthTries 3"):
        assert setting in out
    for step in ("+ ufw default deny incoming", "+ ufw limit OpenSSH", "+ ufw --force enable", "+ sshd -t",
                 "+ systemctl enable --now fail2ban", "APT::Periodic::Unattended-Upgrade \"1\";",
                 "+ timedatectl set-timezone UTC", "SystemMaxUse=500M", '"no-new-privileges": true'):
        assert step in out, step
    assert out.index("+ sshd -t") < out.index("+ systemctl reload ssh")  # erst pruefen, dann laden
    assert not re.search(r"ufw allow|5432|8000|:80\b|443", out)  # kein Port fuer Datenbank/Dashboard/Web
    assert "AllowUsers ops" in dry("harden.sh", OPS_USER="ops").stdout


def test_install_dry_run_order_and_units():
    out = dry("install.sh", REF="stable/005-test").stdout
    assert out.startswith("TROCKENLAUF") and "checkout --detach stable/005-test" in out
    order = [out.index(s) for s in ("git clone", "systemctl daemon-reload", "enable --now ai-trading-backup.timer",
                                    "systemctl restart ai-trading.service")]
    assert order == sorted(order)
    for unit in ("ai-trading.service", "ai-trading-backup.timer", "ai-trading-alert.timer",
                 "ai-trading-alert-failure.service"):
        assert "/etc/systemd/system/%s" % unit in out and (ROOT / "deploy" / "systemd" / unit).exists()


@pytest.mark.parametrize("script", ["harden.sh", "install.sh"])
def test_deploy_scripts_are_strict_and_dry_by_default(script):
    text = (ROOT / "deploy" / "server" / script).read_text()
    assert text.startswith("#!/usr/bin/env bash") and "set -euo pipefail" in text
    assert 'APPLY=0\n[ "${1:-}" = "--apply" ] && APPLY=1' in text  # nur mit ausdruecklichem --apply
    assert subprocess.run(["bash", "-n", str(ROOT / "deploy" / "server" / script)]).returncode == 0


def test_hardening_refuses_to_lock_the_owner_out(tmp_path):
    empty, key = tmp_path / "leer", tmp_path / "authorized_keys"
    empty.write_text("")
    key.write_text("ssh-ed25519 AAAAattrappe besitzer")
    refused = dry("harden.sh", "--check", HARDEN_KEY_FILES="%s %s" % (empty, tmp_path / "fehlt"))
    assert refused.returncode == 1 and "kein SSH-Schluessel" in refused.stderr and "+ " not in refused.stdout
    passed_keys = dry("harden.sh", "--check", HARDEN_KEY_FILES="%s %s" % (empty, key))
    assert "kein SSH-Schluessel" not in passed_keys.stderr and "+ " not in passed_keys.stdout  # nie Aenderungen
    text = (ROOT / "deploy" / "server" / "harden.sh").read_text()
    assert text.index('if [ "$APPLY" = "1" ]; then preflight') < text.index("== 3. SSH")


def test_install_checks_the_password_file_first(tmp_path):
    import getpass
    import os

    env_path = tmp_path / "trading.env"
    user = getpass.getuser()
    assert "fehlt" in dry("install.sh", "--check", INSTALL_ENV_FILE=str(env_path), OPS_USER=user).stderr
    env_path.write_text("POSTGRES_PASSWORD=fake\n")
    os.chmod(env_path, 0o644)
    assert "Rechte 600" in dry("install.sh", "--check", INSTALL_ENV_FILE=str(env_path), OPS_USER=user).stderr
    os.chmod(env_path, 0o600)
    ok = dry("install.sh", "--check", INSTALL_ENV_FILE=str(env_path), OPS_USER=user)
    assert "Rechte 600" not in ok.stderr and "fehlt" not in ok.stderr and "+ " not in ok.stdout


def units():
    out = {}
    for path in (ROOT / "deploy" / "systemd").iterdir():
        out[path.name] = path.read_text()
    return out


def test_systemd_units_run_as_the_operations_user_with_timers():
    u = units()
    for name, text in u.items():
        if name.endswith(".service"):
            assert "User=trading" in text and "root" not in text.split("[Service]")[1], name
    assert "OnCalendar=*-*-* 03:17:00 UTC" in u["ai-trading-backup.timer"] and "Persistent=true" in u["ai-trading-backup.timer"]
    assert "OnUnitActiveSec=5min" in u["ai-trading-alert.timer"]
    assert "OnFailure=ai-trading-alert-failure.service" in u["ai-trading-backup.service"]
    assert "EnvironmentFile=/etc/ai-trading/secrets.env" in u["ai-trading-alert.service"]
    assert "ExecStop=" in u["ai-trading.service"] and "WantedBy=multi-user.target" in u["ai-trading.service"]


# ------------------------------------------------------------------ Alarme


def health(status, *findings):
    return status, list(findings)


def test_alerts_on_change_on_new_findings_repeat_and_recovery():
    repeat = timedelta(hours=6)
    ok = {"status": "OK", "findings": [], "sent_at": None}
    assert alert.decide(ok, health("OK"), T0, repeat) is None
    warn = alert.decide(ok, health("WARN", "ai_budget: KI-Budget gesperrt: kein Verbrauch"), T0, repeat)
    assert warn == "WARN: ai_budget: KI-Budget gesperrt: kein Verbrauch"  # Alarm bei KI-Budget-Sperre
    state = {"status": "WARN", "findings": ["ai_budget: KI-Budget gesperrt: kein Verbrauch"], "sent_at": T0.isoformat()}
    assert alert.decide(state, health("WARN", *state["findings"]), T0 + timedelta(hours=1), repeat) is None
    assert "neu: market_data" in alert.decide(state, health("WARN", state["findings"][0], "market_data: still"),
                                              T0 + timedelta(hours=1), repeat)
    assert "besteht weiter" in alert.decide(state, health("WARN", *state["findings"]), T0 + repeat, repeat)
    assert alert.decide(state, health("CRITICAL", "kill_switch: Not-Aus aktiv"), T0, repeat).startswith("CRITICAL")
    assert alert.decide(state, health("OK"), T0, repeat) == "Wieder in Ordnung (vorher WARN)."


def test_alert_main_logs_always_sends_only_with_a_channel_and_keeps_state(tmp_path):
    sent, lines = [], []
    env = {"ALERT_STATE_FILE": str(tmp_path / "s.json")}

    def run(status, *findings, channel=False, when=T0):
        e = dict(env, ALERT_WEBHOOK_URL="https://alarm.example/geheim-token") if channel else env
        return alert.main([], e, fetch=lambda: health(status, *findings), send=lambda url, text: sent.append(text),
                          now=when, out=lines.append)

    assert run("OK") == 0 and lines == [] and sent == []
    assert run("CRITICAL", "database: nicht erreichbar") == 2 and lines[-1].startswith("ALARM: CRITICAL") and sent == []
    assert run("CRITICAL", "database: nicht erreichbar", channel=True) == 2 and sent == []  # schon gemeldet
    assert run("OK", channel=True) == 0 and sent == ["Wieder in Ordnung (vorher CRITICAL)."]
    assert all("geheim-token" not in line for line in lines)  # die Kanal-Adresse erscheint nie
    assert json.loads((tmp_path / "s.json").read_text())["status"] == "OK"

    def broken(url, text):
        raise OSError("Kanal weg")

    assert alert.main(["--event", "Sicherung fehlgeschlagen"], dict(env, ALERT_WEBHOOK_URL="https://x.example/"),
                      send=broken, out=lines.append) == 2
    assert lines[-2] == "ALARM: Sicherung fehlgeschlagen" and "FEHLGESCHLAGEN" in lines[-1]


def test_unreachable_service_and_plain_http_channels():
    def refused(*args, **kwargs):
        raise OSError("refused")

    original = alert.urllib.request.urlopen
    alert.urllib.request.urlopen = refused
    try:
        assert alert.fetch_health("http://127.0.0.1:9/api/health")[0] == "NICHT_ERREICHBAR"
    finally:
        alert.urllib.request.urlopen = original
    with pytest.raises(ValueError):
        alert.send_webhook("http://unverschluesselt.example/", "x")


# ------------------------------------------------------------------ Serverkosten


def test_server_costs_flow_into_the_net_result_month_by_month():
    ledger = operating_costs_from_env({"COST_SERVER_MONTHLY_USD": "6.00", "COST_MARKET_DATA_MONTHLY_USD": "0"}, T0, 3)
    assert [c.category for c in ledger.costs] == [CostCategory.SERVER] * 3
    october = ledger.total(datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert october == D("6.00")
    half = ledger.total(datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 11, 16, tzinfo=timezone.utc))
    assert half == D("3.00")
    assert operating_costs_from_env({}, T0).costs == ()
    for bad in ({"COST_SERVER_MONTHLY_USD": "viel"}, {"COST_SERVER_MONTHLY_USD": "-1"},
                {"COST_OTHER_MONTHLY_USD": "NaN"}):
        with pytest.raises(ValueError):
            operating_costs_from_env(bad, T0)
