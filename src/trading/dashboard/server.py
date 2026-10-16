"""Dashboard im Hintergrund eines Dienstes (AP-27): uvicorn in einem eigenen Thread, sauber beendbar.

Nur hier und in `__main__` wird der Webserver gestartet (Webserver-Bibliotheken nur im Dashboard-Paket). Standard:
nur auf diesem Rechner erreichbar (127.0.0.1) - von aussen nur ueber einen SSH-Tunnel.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from trading.dashboard.api import create_app
from trading.dashboard.state import DashboardSource
from trading.ops.health import HealthProbe


class DashboardServer:
    def __init__(self, source: DashboardSource, probe: Optional[HealthProbe], host: str = "127.0.0.1",
                 port: int = 8000) -> None:
        import uvicorn

        config = uvicorn.Config(create_app(source, probe=probe), host=host, port=port, log_level="warning",
                                lifespan="off", ws="wsproto")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="dashboard", daemon=True)

    def start(self, timeout_s: float = 10.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout_s
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Dashboard konnte nicht starten (Port belegt?).")
            time.sleep(0.05)

    @property
    def port(self) -> int:
        sockets = [s for server in (self._server.servers or []) for s in server.sockets]
        return int(sockets[0].getsockname()[1]) if sockets else 0

    def stop(self, timeout_s: float = 10.0) -> None:
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout_s)


__all__ = ["DashboardServer"]
