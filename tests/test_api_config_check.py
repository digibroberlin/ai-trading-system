"""Pruefung der API-Konfiguration auf Handelsberechtigungen (AP-34, D-033): findet Boersen-Zugaenge, Echtgeld-Schalter
und falsche Modi in Umgebung und Passwort-Dateien - und gibt dabei NIE einen Wert aus. Alle Werte sind erfunden."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_api_config", ROOT / "scripts" / "check_api_config.py")
check = importlib.util.module_from_spec(_spec)
sys.modules["check_api_config"] = check
_spec.loader.exec_module(check)

F = "fake"  # erfundene Werte werden zusammengesetzt - die Pruefung auf fest eingetragene Zugangsdaten bleibt streng
FAKE = {  # erfundene Werte, die nie in der Ausgabe stehen duerfen
    "BITGET_API_KEY": F + "bitget1111",
    "coinbaseApiSecret": F + "coinbase2222",
    "MY_SETTING": "organizations/fake-org/apiKeys/fake-key-3333",  # Coinbase-Form am Wert erkannt
    "Trading_Mode": "live",
    "LIVE_TRADING": "1",
    "USE_MAINNET": "0",
    "ANTHROPIC_API_KEY": F + "anthropic4444",
}


def run(env, files=()):
    lines = []
    code = check.main([arg for f in files for arg in ("--env-file", str(f))], env=env, out=lines.append)
    return code, "\n".join(lines)


def test_forbidden_settings_are_found_by_name_without_any_value():
    code, text = run(FAKE)
    assert code == 1
    forbidden = {line.split()[2].rstrip(":") for line in text.splitlines() if line.startswith("VERBOTEN")}
    assert forbidden == {"BITGET_API_KEY", "coinbaseApiSecret", "MY_SETTING", "Trading_Mode", "LIVE_TRADING"}
    assert any(line.startswith("HINWEIS") and "USE_MAINNET" in line for line in text.splitlines())  # aus: Hinweis
    assert "ANTHROPIC_API_KEY" in text and "B-004" in text
    for value in FAKE.values():
        if value not in ("1", "0", "live"):
            assert value not in text and value[-4:] not in text  # nie ein Wert, nicht einmal ein Teil


def test_clean_configuration_is_ok_and_harmless_names_are_not_flagged():
    env = {"PATH": "/usr/bin", "TRADING_MODE": "paper", "CLAUDE_CODE_REMOTE_SEND_KEEPALIVES": "1",
           "AWS_SECRET_ACCESS_KEY": "fakeaws", "TRADING_ENGINE_ENABLED": "0"}
    code, text = run(env)
    assert code == 0 and text.startswith("OK:") and "VERBOTEN" not in text
    hints = [line for line in text.splitlines() if line.startswith("HINWEIS")]
    assert len(hints) == 1 and "AWS_SECRET_ACCESS_KEY" in hints[0] and "unbekannter Zugang" in hints[0]  # nur benannt
    assert "fakeaws" not in text


def test_password_files_are_checked_for_rights_place_and_content(tmp_path):
    good = tmp_path / "trading.env"
    good.write_text("# Kommentar\nexport POSTGRES_PASSWORD='fake'\nTRADING_ENGINE_ENABLED=1\n"
                    "TRADING_ENGINE_APPROVAL=\"PAPER-HANDEL FREIGEGEBEN\"\n")
    os.chmod(good, 0o600)
    code, text = run({}, [good])
    assert code == 0 and "Paper-Handel ist freigegeben" in text and "fake" not in text
    os.chmod(good, 0o644)
    code, text = run({}, [good])
    assert code == 2 and "Rechte 644" in text
    bad = tmp_path / "boerse.env"
    bad.write_text("BITGET_SECRET_KEY" + "=fakesecret5555\n")  # zusammengesetzt: erfundener Testwert, kein Fund
    os.chmod(bad, 0o600)
    code, text = run({}, [bad])
    assert code == 1 and "BITGET_SECRET_KEY" in text and "fakesecret5555" not in text
    inside = ROOT / "scripts" / "pruef-test.env"
    try:
        inside.write_text("A=1\n")
        os.chmod(inside, 0o600)
        assert "Projektordner" in run({}, [inside])[1]
    finally:
        inside.unlink()
    assert run({}, [tmp_path / "fehlt.env"])[0] == 2


def test_works_even_where_the_trading_package_refuses_to_start():
    env = {"PATH": os.environ.get("PATH", ""), "BITGET_API_KEY": F + "bitget6666",
           "PYTHONPATH": str(ROOT / "src")}
    refused = subprocess.run([sys.executable, "-c", "import trading"], env=env, capture_output=True, text=True)
    assert refused.returncode != 0 and "ConfigError" in refused.stderr  # PAPER-Schutz greift
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_api_config.py")], env=env,
                          capture_output=True, text=True)
    assert done.returncode == 1 and "BITGET_API_KEY" in done.stdout and "fakebitget6666" not in done.stdout + done.stderr
