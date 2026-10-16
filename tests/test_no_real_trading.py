"""Strukturelle Sicherheitstests: Der Quellcode darf (noch) nichts ins Netz senden.

Diese Tests scannen den Quelltext. Kommen in einer spaeteren Phase bewusst
Marktdaten-Adapter dazu (nur lesend), muss diese Liste ABSICHTLICH und mit Review angepasst
werden - das ist gewollt: Es zwingt zu einer bewussten Entscheidung.
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


def test_no_network_or_process_libraries_are_imported_anywhere():
    offenders = {
        str(p.relative_to(ROOT)): sorted(set(imported_modules(p)) & FORBIDDEN_IMPORTS)
        for p in python_files()
        if set(imported_modules(p)) & FORBIDDEN_IMPORTS
    }
    assert offenders == {}


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
