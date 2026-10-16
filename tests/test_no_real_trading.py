"""Strukturelle Sicherheitstests: Der Quellcode darf (noch) nichts ins Netz senden.

Diese Tests scannen den Quelltext. Seit AP-14 (D-025) gibt es genau EINE bewusste Ausnahme: das Lesen oeffentlicher
Marktdaten in `marketdata/public_http.py` (nur GET, feste Hosts, keine Zugangsdaten). Jede weitere Ausnahme muss
ABSICHTLICH und mit Review hier eingetragen werden - das ist gewollt: Es zwingt zu einer bewussten Entscheidung.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "trading"

FORBIDDEN_IMPORTS = {
    "socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx", "aiohttp",
    "websockets", "websocket", "ccxt", "ftplib", "smtplib", "telnetlib", "subprocess",
}


def python_files():
    return sorted(SRC.rglob("*.py"))


def imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module.split(".")[0]


def test_source_files_were_found():
    assert len(python_files()) > 20


# Bewusste Ausnahmen: oeffentliche Marktdaten LESEN - je eine Datei, feste Adressen, keine Zugangsdaten.
NETWORK_EXCEPTIONS = {
    "src/trading/marketdata/public_http.py": ["urllib"],  # AP-14, D-025: historische Kerzen (HTTPS-GET)
    "src/trading/marketdata/public_ws.py": ["socket", "ssl"],  # AP-15, D-026: Marktueberwachung (WebSocket)
}


def test_no_network_or_process_libraries_are_imported_anywhere():
    offenders = {
        str(p.relative_to(ROOT)): sorted(set(imported_modules(p)) & FORBIDDEN_IMPORTS)
        for p in python_files()
        if set(imported_modules(p)) & FORBIDDEN_IMPORTS
    }
    assert offenders == NETWORK_EXCEPTIONS


def test_the_only_network_access_reads_public_market_data_and_sends_nothing():
    """Die Ausnahme oben darf nie zu einem Sende-Weg werden: nur GET, kein Inhalt, keine Zugangs-Kopfzeilen, feste Hosts."""
    path = ROOT / "src" / "trading" / "marketdata" / "public_http.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    requests = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "Request"]
    assert len(requests) == 1
    keywords = {k.arg: k.value for k in requests[0].keywords}
    assert isinstance(keywords.get("method"), ast.Constant) and keywords["method"].value == "GET"
    assert "data" not in keywords and len(requests[0].args) == 1  # kein Inhalt
    headers = keywords["headers"]
    assert isinstance(headers, ast.Dict) and [k.value for k in headers.keys] == ["User-Agent", "Accept"]
    for word in ("POST", "PUT", "DELETE", "PATCH", "Authorization", "ACCESS-KEY", "ACCESS-SIGN", "CB-ACCESS",
                 "api_key", "secret", "passphrase", "CookieProcessor"):
        assert word not in source, word
    hosts = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(
        isinstance(tg, ast.Name) and tg.id == "ALLOWED_HOSTS" for tg in n.targets)]
    assert len(hosts) == 1 and ast.literal_eval(hosts[0].value.args[0]) == {"api.exchange.coinbase.com",
                                                                             "api.bitget.com"}
    users = sorted(str(p.relative_to(SRC)) for p in python_files() if "public_http" in p.read_text(encoding="utf-8"))
    assert users and all(u.startswith("marketdata/") for u in users), users  # nirgends sonst (z. B. nie im Executor)


def test_no_hardcoded_credentials():
    pattern = re.compile(
        r"""(api[_-]?key|api[_-]?secret|secret[_-]?key|passphrase|password|token)\s*[:=]\s*["'][^"']{6,}["']""",
        re.IGNORECASE,
    )
    hits = []
    for p in python_files() + sorted((ROOT / "tests").glob("*.py")):
        for no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                hits.append("%s:%d" % (p.name, no))
    assert hits == []


def test_no_env_file_with_secrets_in_project():
    assert not (ROOT / ".env").exists()


def test_executor_has_no_order_sending_api():
    from trading.execution.paper_executor import PaperExecutor

    public = {n for n in dir(PaperExecutor) if not n.startswith("_")}
    assert not any(word in n for n in public for word in ("live", "send", "exchange", "api"))


def test_database_driver_is_only_imported_inside_the_persistence_package():
    offenders = [
        str(p.relative_to(ROOT))
        for p in python_files()
        if "psycopg" in set(imported_modules(p)) and "persistence" not in p.parts
    ]
    assert offenders == []


WEB_SERVER = {"fastapi", "starlette", "uvicorn"}


def test_web_server_libraries_only_in_the_read_only_dashboard():
    """Bewusste Ausnahme (AP-23, D-020): Das Dashboard nimmt lokale Anfragen an (nur lesend). Nirgends sonst ein Server."""
    offenders = [
        str(p.relative_to(ROOT))
        for p in python_files()
        if set(imported_modules(p)) & WEB_SERVER and "dashboard" not in p.parts
    ]
    assert offenders == []


def test_persistence_package_connects_only_to_the_configured_database():
    # Kein fest eingetragener Host, kein Port, keine URL im Quelltext der Persistenzschicht.
    for p in (SRC / "persistence").glob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"(localhost|127\.0\.0\.1|postgresql://|postgres://)", text), p.name


def test_the_websocket_access_only_reaches_fixed_public_market_data_endpoints():
    """Zweite Ausnahme (AP-15): nur feste wss-Adressen oeffentlicher Kanaele, TLS mit Pruefung, keine Zugangsdaten."""
    path = ROOT / "src" / "trading" / "marketdata" / "public_ws.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    endpoints = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(
        isinstance(tg, ast.Name) and tg.id == "ALLOWED_ENDPOINTS" for tg in n.targets)]
    assert len(endpoints) == 1 and ast.literal_eval(endpoints[0].value.args[0]) == {
        "wss://ws.bitget.com/v2/ws/public", "wss://ws-feed.exchange.coinbase.com"}
    assert "create_default_context()" in source and "CERT_NONE" not in source and "check_hostname" not in source
    for word in ("authorization", "access-key", "access-sign", "cb-access", "api_key", "secret", "passphrase",
                 "login", "/private"):
        assert word not in source.lower(), word  # keine Anmeldung, keine privaten Kanaele
    users = sorted(str(p.relative_to(SRC)) for p in python_files() if "wsproto" in set(imported_modules(p)))
    assert users == ["marketdata/public_ws.py"]  # WebSocket-Rahmen nur hier (der Server im Dashboard nutzt uvicorn)
