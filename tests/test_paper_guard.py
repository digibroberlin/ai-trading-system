"""Zentraler PAPER-Schutz (Audit M3, AP-06): jeder Prozess prueft Modus und Umgebung, die Schluessel-Erkennung ist breit.

Die Beispielwerte sind erfunden und werden teils erst zur Laufzeit zusammengesetzt, damit der Geheimnis-Scanner
diese Datei nicht als Fund meldet.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from trading.config import ConfigError, credential_variables, enforce_paper_process, load_settings

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
FAKE = "erfunden-" + "x7Q2"


@pytest.mark.parametrize("name", [
    # Kleinschreibung und gemischte Schreibweise (vorher nicht erkannt)
    "bitget_api_key", "Coinbase_Api_Secret", "bitget-passphrase",
    # Coinbase Developer Platform (vorher nicht erkannt)
    "CDP_API_KEY_NAME", "CDP_API_KEY_PRIVATE_KEY", "CDP_API_KEY_ID", "cdp_api_key_secret",
    # allgemeine Namen
    "API_KEY", "api_secret", "API_PASSPHRASE", "PASSPHRASE", "SECRET_KEY", "PRIVATE_KEY", "APIKEY",
    # weitere Boersen und CCXT
    "BINANCE_API_KEY", "KRAKEN_PRIVATE_KEY", "OKX_PASSPHRASE", "BYBIT_API_SECRET", "KUCOIN_API_KEY",
    "CCXT_APIKEY", "EXCHANGE_API_KEY", "exchange_secret", "HYPERLIQUID_PRIVATE_KEY", "GATEIO_KEY",
    # alte Regel weiterhin
    "COINBASE_API_KEY", "BITGET_SECRET", "CB_ACCESS_KEY", "BG_TOKEN",
    # Passphrase in jeder Form
    "MY_TRADING_PASSPHRASE",
    # Befunde der unabhaengigen Pruefung: camelCase, Boersenname nicht am Anfang, ccxt-IDs, Freqtrade, ohne Trenner
    "binanceApiSecret", "cdpApiKeySecret", "krakenPrivateKey", "MY_BINANCE_API_KEY", "PROD_COINBASE_API_SECRET",
    "FREQTRADE__EXCHANGE__KEY", "FREQTRADE__EXCHANGE__SECRET", "BINANCEUS_API_SECRET", "KRAKENFUTURES_API_KEY",
    "BINANCEAPIKEY", "COINBASE_CREDENTIALS",
])
def test_exchange_like_credentials_block_startup(name):
    with pytest.raises(ConfigError) as err:
        load_settings({name: FAKE})
    assert name in str(err.value)
    assert FAKE not in str(err.value), "der Wert darf nie in der Meldung stehen"


@pytest.mark.parametrize("value", [
    "organizations/" + "0a1b2c3d-1111-2222-3333-444455556666" + "/apiKeys/" + "abc",
    "-----BEGIN EC " + "PRIVATE KEY-----\nMHcCAQEE...\n-----END EC " + "PRIVATE KEY-----",
])
def test_credential_looking_values_block_startup_under_any_name(value):
    with pytest.raises(ConfigError) as err:
        load_settings({"HARMLOSER_NAME": value})
    assert "HARMLOSER_NAME" in str(err.value)
    assert value not in str(err.value)


@pytest.mark.parametrize("name", [
    # Zugaenge der Cloud-/Entwicklungsumgebung, die mit Handel nichts zu tun haben, und unsere Datenbank-Variablen
    "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "GH_TOKEN", "GITHUB_TOKEN", "CLOUDSDK_AUTH_ACCESS_TOKEN",
    "CLAUDE_CODE_MESSAGING_TOKEN", "GIT_CONFIG_KEY_0", "ANTHROPIC_API_KEY",
    "DATABASE_URL", "ADMIN_DATABASE_URL", "POSTGRES_PASSWORD", "APP_DB_PASSWORD", "TEST_ADMIN_URL", "TEST_APP_PASSWORD",
    "PATH", "HOME", "CBOR_LIBRARY_PATH", "EXCHANGE_RATE_SOURCE",
])
def test_unrelated_environment_does_not_block(name):
    assert credential_variables({name: FAKE}) == []
    load_settings({name: FAKE})


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_credential_variables_are_not_credentials(value):
    assert credential_variables({"BITGET_API_KEY": value}) == []


@pytest.mark.parametrize("env", [
    {"trading_mode": "live"},
    {"Trading_Mode": "LIVE"},
    {"TRADING_MODE": "paper", "trading_mode": "live"},
    {"TRADING_MODE": " live "},
])
def test_every_spelling_of_trading_mode_must_be_paper(env):
    with pytest.raises(ConfigError):
        load_settings(env)


def test_paper_in_any_spelling_is_accepted():
    load_settings({"trading_mode": "PAPER", "TRADING_MODE": " paper "})


def test_enforce_paper_process_is_the_same_check():
    enforce_paper_process({"TRADING_MODE": "paper"})
    with pytest.raises(ConfigError):
        enforce_paper_process({"cdp_api_key_name": FAKE})


# ------------------------------------------------------------------ jeder Prozess, jeder Einstieg


def _run(args, extra_env, cwd=ROOT):
    env = {k: v for k, v in os.environ.items() if not credential_variables({k: v})}
    env.pop("TRADING_MODE", None)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), str(ROOT / "tests")])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra_env)
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)


def _entry_modules():
    """Alle Module unter src/trading mit eigenem Programmstart (`if __name__ == "__main__"`)."""
    found = []
    for path in sorted((SRC / "trading").rglob("*.py")):
        if re.search(r'^if __name__ == "__main__":', path.read_text(encoding="utf-8"), re.M):
            mod = ".".join(path.relative_to(SRC).with_suffix("").parts)
            found.append(mod[: -len(".__main__")] if mod.endswith(".__main__") else mod)
    return found


def test_entry_points_are_discovered():
    assert {"trading.demo", "trading.backtest", "trading.persistence.migrate"} <= set(_entry_modules())


@pytest.mark.parametrize("bad_env", [{"bitget_api_key": FAKE}, {"CDP_API_KEY_NAME": FAKE}, {"TRADING_MODE": "live"}])
@pytest.mark.parametrize("module", _entry_modules())
def test_every_program_refuses_to_start(module, bad_env):
    proc = _run(["-m", module], bad_env)
    assert proc.returncode != 0
    assert "ConfigError" in proc.stderr, proc.stderr[-500:]
    assert FAKE not in proc.stderr + proc.stdout


@pytest.mark.parametrize("script", ["scripts/restart_check.py", "tests/pg_workers.py"])
def test_helper_processes_refuse_to_start(script):
    proc = _run([script, "verify", "/nicht/vorhanden"], {"API_KEY": FAKE})
    assert proc.returncode != 0
    assert "ConfigError" in proc.stderr, proc.stderr[-500:]


def test_importing_any_part_of_the_system_runs_the_guard():
    """Auch ein Prozess, der nur ein Teilpaket laedt (z. B. ein kuenftiger Worker), wird geprueft."""
    bad = _run(["-c", "import trading.safety.limits"], {"okx_passphrase": FAKE})
    assert bad.returncode != 0 and "ConfigError" in bad.stderr
    good = _run(["-c", "import trading.safety.limits; print('ok')"], {})
    assert good.returncode == 0 and good.stdout.strip() == "ok", good.stderr[-500:]
