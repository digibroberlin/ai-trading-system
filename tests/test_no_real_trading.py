"""Strukturelle Sicherheitstests: Der Quellcode darf (noch) nichts ins Netz senden.

Diese Tests scannen den Quelltext. Seit AP-14 (D-025) gibt es genau EINE bewusste Ausnahme: das Lesen oeffentlicher
Marktdaten in `marketdata/public_http.py` (nur GET, feste Hosts, keine Zugangsdaten). Jede weitere Ausnahme muss
ABSICHTLICH und mit Review hier eingetragen werden - das ist gewollt: Es zwingt zu einer bewussten Entscheidung.
"""

import ast
import importlib.util
import re
import sys
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
    # nur das Marktdaten-Paket und wer es zum LESEN zusammensetzt: Paper-Dienst (AP-27), Live-Funding (D-031, nur die
    # Fehlerklasse), Untersuchung auf echten Daten (D-032) - nie Executor, Risiko oder Agenten
    allowed = {"runtime/service.py", "runtime/funding.py", "research/study.py"}
    assert users and all(u.startswith("marketdata/") or u in allowed for u in users), users


def test_no_hardcoded_credentials():
    pattern = re.compile(
        r"""["']?(api[_-]?key|api[_-]?secret|secret[_-]?key|passphrase|password|token)["']?\s*[:=]\s*["'][^"']{6,}["']""",
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
    # auch der Executor des Betriebs (PostgreSQL) - ueber den Syntaxbaum, ohne Datenbank-Treiber zu laden
    tree = ast.parse((SRC / "persistence" / "pg_executor.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PostgresPaperExecutor")
    methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}
    assert methods and not any(word in n for n in methods for word in ("live", "send", "exchange", "api"))


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


# ------------------------------------------------------------------ AP-34: keine echte Boersenorder moeglich (D-033)

ORDER_MARKERS = (
    "place-order", "placeorder", "batch-orders", "cancel-order", "cancel-replace", "modify-order", "/orders",
    "/mix/order", "/spot/trade", "/v3/brokerage", "withdraw", "transfer", "access-sign", "access-key",
    "access-passphrase", "cb-access", "ok-access", "x-mbx-apikey",
)


def operational_files():
    """Handelscode UND alles, was im Betrieb laeuft (Skripte, Server-Dateien) - ohne die Gegenprobe selbst, die
    absichtlich Fehler (auch Order-Pfade) einbaut, um die Tests zu pruefen."""
    scripts = [p for p in sorted((ROOT / "scripts").rglob("*")) if p.is_file() and p.suffix in (".py", ".sh")
               and p.name != "mutation_check.py" and "__pycache__" not in p.parts]
    deploy = [p for p in sorted((ROOT / "deploy").rglob("*")) if p.is_file()] if (ROOT / "deploy").exists() else []
    return python_files() + scripts + deploy


def test_no_order_endpoint_or_authenticated_request_exists_in_the_code():
    """Weder Order-Pfade der Boersen noch Abhebungen noch signierte (authentifizierte) Anfragen - in KEINER Datei."""
    offenders = {}
    for path in operational_files():
        text = path.read_text(encoding="utf-8").lower()
        hits = [m for m in ORDER_MARKERS if m in text]
        if hits:
            offenders[str(path.relative_to(ROOT))] = hits
    assert offenders == {}


def test_no_request_signing_code():
    """Boersen-Orders muessen signiert werden (HMAC). Ohne hmac/Signatur-Bibliotheken gibt es keinen Weg dorthin."""
    signing = {"hmac", "nacl", "ecdsa", "jwt", "cryptography", "Crypto"}
    files = python_files() + [p for p in operational_files() if p.suffix == ".py" and p not in python_files()]
    offenders = {str(p.relative_to(ROOT)): sorted(set(imported_modules(p)) & signing) for p in files
                 if set(imported_modules(p)) & signing}
    assert offenders == {}


# Weitere Wege ins Netz oder zu anderen Programmen, die der Handelscode nicht braucht (Gegenpruefung AP-34)
EXTRA_FORBIDDEN = {"ctypes", "xmlrpc", "socketserver", "poplib", "imaplib", "nntplib", "wsgiref", "webbrowser",
                   "multiprocessing", "pty", "asyncio"}
SHELL_CALLS = ("os.system", "os.popen", "os.exec", "os.spawn", "os.posix_spawn", "os.fork")


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return inner + "." + node.attr if inner else node.attr
    return ""


def test_no_other_way_to_the_network_or_to_other_programs():
    offenders = {}
    for p in python_files():
        bad = set(imported_modules(p)) & EXTRA_FORBIDDEN
        if "dashboard" in p.parts:
            bad -= {"asyncio"}  # Server-Schleife des nur lesenden Dashboards
        sdk_loader = p.relative_to(SRC).as_posix() == "ai/anthropic_provider.py"
        if "importlib" in set(imported_modules(p)) and not sdk_loader:
            bad.add("importlib")  # dynamisches Laden nur fuer das KI-SDK (nach Freigabe B-004)
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)):  # auch ein blosser Verweis (x = os.system) zaehlt
                name = _dotted(node)
                if name == "__import__" or name.startswith(SHELL_CALLS):
                    bad.add(name)
                if name.endswith("import_module") and not sdk_loader:
                    bad.add(name)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("system", "popen"):
                bad.add(node.func.id)
            if isinstance(node, ast.ImportFrom) and node.module == "os":
                bad |= {a.name for a in node.names if a.name.startswith(("system", "popen", "exec", "spawn"))}
        if bad:
            offenders[str(p.relative_to(ROOT))] = sorted(bad)
    assert offenders == {}


def test_network_building_blocks_are_used_only_where_market_data_is_read():
    """Auch ueber Umwege (Weiter-Export) darf kein anderer Teil an die Netzbausteine kommen."""
    terms = ("public_http", "PublicHttpGet", "public_ws", "PublicWebSocket", "_tls_socket")
    allowed = {"runtime/service.py", "runtime/funding.py", "research/study.py"}
    users = sorted(str(p.relative_to(SRC)) for p in python_files()
                   if any(t in p.read_text(encoding="utf-8") for t in terms))
    assert users and all(u.startswith("marketdata/") or u in allowed for u in users), users
    tree = ast.parse((SRC / "marketdata" / "public_http.py").read_text(encoding="utf-8"))
    opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "open"]
    assert opens and all(len(n.args) == 1 and {k.arg for k in n.keywords} <= {"timeout"} for n in opens)  # nie ein Inhalt


def test_only_the_paper_mode_exists():
    from trading.config import TradingMode

    assert [m.value for m in TradingMode] == ["paper"]  # Echtgeld braucht eine eigene, separat freigegebene Phase


def test_websocket_subscriptions_only_ask_for_public_market_channels():
    import json

    from trading.domain.instrument import Exchange, InstrumentId, MarketType
    from trading.marketdata.bitget_ws import BitgetStream
    from trading.marketdata.coinbase_ws import CoinbaseStream

    bitget = [json.loads(m) for m in BitgetStream().subscribe(
        [InstrumentId(Exchange.BITGET, MarketType.FUTURES, "BTC-USDT")], 3600)]
    assert all(m["op"] == "subscribe" and all(a["channel"].startswith("candle") for a in m["args"]) for m in bitget)
    coinbase = [json.loads(m) for m in CoinbaseStream().subscribe(
        [InstrumentId(Exchange.COINBASE, MarketType.SPOT, "BTC-USD")], 60)]
    assert all(m["type"] == "subscribe" and set(m["channels"]) <= {"matches", "heartbeat"} for m in coinbase)
    for message in bitget + coinbase:
        assert not {"login", "sign", "signature", "key", "passphrase", "jwt"} & {k.lower() for k in message}
    assert BitgetStream().keepalive() == "ping"  # das einzige andere, was je gesendet wird


CONTAINER_COMMANDS = {  # alles, was ein Container starten darf - nichts davon kann eine Order senden
    '["python", "-m", "trading.persistence.migrate"]', '["python", "scripts/backup.py", "cycle"]',
    '["python", "-m", "trading.runtime"]', '["python", "-m", "trading.demo"]', '["pytest", "-q", "-rs"]'}


def test_containers_start_only_known_paper_programs_without_extra_privileges():
    files = [ROOT / "docker-compose.yml", ROOT / "docker-compose.prod.yml", ROOT / "Dockerfile",
             ROOT / "db" / "Dockerfile"]
    commands = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        commands += re.findall(r"^\s*(?:command:|CMD)\s*(.+?)\s*$", text, re.M)
        assert not re.search(r"^\s*(entrypoint:|ENTRYPOINT)", text, re.M), path.name
        for word in ("privileged", "network_mode", "cap_add", "pid:", "docker.sock", "BITGET", "COINBASE",
                     "BINANCE", "EXCHANGE_"):
            assert word not in text, "%s: %s" % (path.name, word)
    assert commands and set(commands) <= CONTAINER_COMMANDS, set(commands) - CONTAINER_COMMANDS


# Fremdpakete: nur diese - ein Boersen-SDK (ccxt, python-binance, coinbase-advanced-py ...) faellt sofort auf, denn es
# signiert Anfragen intern und braeuchte keinen der oben gesperrten Bausteine im eigenen Code (Gegenpruefung AP-34).
THIRD_PARTY = {"psycopg", "fastapi", "uvicorn", "wsproto"}  # Datenbank, Dashboard, WebSocket-Rahmen
RUNTIME_PACKAGES = {"psycopg", "fastapi", "uvicorn", "wsproto", "starlette", "pydantic"}
EXCHANGE_SDKS = ("ccxt", "binance", "coinbase", "bitget", "pybit", "okx", "kraken", "bybit", "kucoin", "cryptofeed")
# Skripte duerfen andere Programme starten (git, pg_dump, pip-audit) bzw. EIN Skript sendet Alarme an den Kanal des
# Eigners - jede weitere Ausnahme muss hier bewusst eingetragen werden.
SCRIPT_EXCEPTIONS = {
    "scripts/backup.py": ["subprocess"], "scripts/dependency_audit.py": ["subprocess"],
    "scripts/preflight.py": ["subprocess"], "scripts/secret_scan.py": ["subprocess"],
    "scripts/ops/alert_check.py": ["urllib"],
}


def _is_stdlib(name):
    if hasattr(sys, "stdlib_module_names"):  # ab Python 3.10
        return name in sys.stdlib_module_names
    spec = importlib.util.find_spec(name)
    origin = (spec.origin or "") if spec is not None else "site-packages"
    return origin in ("built-in", "frozen") or "site-packages" not in origin


def operational_python_files():
    return python_files() + [p for p in operational_files() if p.suffix == ".py" and p not in python_files()]


def test_only_known_third_party_packages_are_imported():
    offenders = {}
    for p in operational_python_files():
        foreign = {m for m in imported_modules(p) if m not in ("trading", "__future__") and not _is_stdlib(m)}
        if foreign - THIRD_PARTY:
            offenders[str(p.relative_to(ROOT))] = sorted(foreign - THIRD_PARTY)
    assert offenders == {}


def test_no_exchange_sdk_in_the_dependencies():
    names = set()
    for line in (ROOT / "requirements" / "runtime.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            names.add(re.split(r"[\[=<>~!; ]", line)[0].lower())
    assert names == RUNTIME_PACKAGES
    for lock in sorted((ROOT / "requirements").glob("*.lock")):
        packages = re.findall(r"^([A-Za-z0-9_.-]+)==", lock.read_text(encoding="utf-8"), re.M)
        assert packages, lock.name
        assert not [n for n in packages if any(s in n.lower() for s in EXCHANGE_SDKS)], lock.name


def test_scripts_reach_the_network_or_other_programs_only_where_registered():
    offenders = {}
    for p in operational_python_files():
        if p in python_files():
            continue
        bad = set(imported_modules(p)) & (FORBIDDEN_IMPORTS | EXTRA_FORBIDDEN)
        if bad:
            offenders[str(p.relative_to(ROOT))] = sorted(bad)
    assert offenders == SCRIPT_EXCEPTIONS
