"""Nur lesende Dashboard-API (AP-23): FastAPI mit JSON-Endpunkten und WebSocket-Aktualisierung.

Endpunkte (alle GET, nichts aendert das System):
  /                      Weboberflaeche (lokale Dateien, keine fremden Server)
  /api/health            lebt der Dienst? Mit `probe`: Not-Aus, Datenbank, Marktdaten, Engine, KI-Budget
                         (OK/WARN/CRITICAL; bei CRITICAL HTTP 503). Echtgeld immer "deaktiviert"
  /metrics               Kennzahlen im Textformat von Prometheus (fuer Ueberwachung und Alarme)
  /api/state             gesamte Momentaufnahme
  /api/state/{abschnitt} ein Abschnitt (status, portfolio, positions, trades, equity, metrics, agents, blocked,
                         strategies, costs, errors)
  /ws                    WebSocket: sofort die Momentaufnahme, danach nur bei Aenderung (Pruefsumme `version`)
Jede andere Methode (POST, PUT, DELETE ...) ergibt 405. Modus LIVE laesst sich gar nicht erst starten.
Sicherheits-Header: keine fremden Skripte/Verbindungen (Content-Security-Policy), kein Einbetten, kein Zwischenspeichern.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from trading.clock import Clock, SystemClock
from trading.dashboard.state import SECTIONS, DashboardSource, Mode
from trading.ops.health import HealthProbe, Level, Metric, render_prometheus

STATIC = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def create_app(source: DashboardSource, clock: Optional[Clock] = None, push_interval_s: float = 2.0,
               probe: Optional[HealthProbe] = None) -> FastAPI:
    if not isinstance(source, DashboardSource):
        raise ValueError("source muss eine DashboardSource sein.")
    if source.mode is Mode.LIVE:
        raise ValueError("Modus LIVE ist deaktiviert - das Dashboard zeigt kein Echtgeld-Konto an.")
    if not push_interval_s > 0:
        raise ValueError("push_interval_s muss positiv sein.")
    now = (clock or SystemClock()).now
    app = FastAPI(title="KI-Trading-System (nur lesend)", docs_url=None, redoc_url=None, openapi_url=None)

    def snapshot() -> Dict[str, Any]:
        return source.state(now())

    @app.middleware("http")
    async def headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers[key] = value
        return response

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")

    @app.get("/static/{name}", include_in_schema=False)
    def static(name: str) -> FileResponse:
        if name not in STATIC_FILES:  # feste Liste - kein Zugriff auf andere Dateien
            raise HTTPException(status_code=404)
        return FileResponse(STATIC / name, media_type=STATIC_FILES[name])

    @app.get("/api/health")
    def health() -> JSONResponse:
        if probe is None:
            return JSONResponse({"ok": True, "mode": source.mode.value, "live_trading": "deaktiviert", "read_only": True})
        report = probe.report()
        body = dict(report.as_dict(), mode=source.mode.value)
        return JSONResponse(body, status_code=503 if report.level is Level.CRITICAL else 200)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> PlainTextResponse:
        found = probe.metrics() if probe is not None else [Metric("trading_up", "gauge", "Dienst laeuft", 1)]
        return PlainTextResponse(render_prometheus(found), media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/api/state")
    def state() -> JSONResponse:
        return JSONResponse(snapshot())

    @app.get("/api/state/{section}")
    def section(section: str) -> JSONResponse:
        if section not in SECTIONS:
            raise HTTPException(status_code=404, detail="Unbekannter Abschnitt.")
        body = snapshot()
        return JSONResponse({"mode": body["mode"], "version": body["version"], section: body[section]})

    @app.websocket("/ws")
    async def live(ws: WebSocket) -> None:
        await ws.accept()
        last = ""
        try:
            while True:
                body = await asyncio.to_thread(snapshot)
                if body["version"] != last:
                    await ws.send_json(body)
                    last = str(body["version"])
                await asyncio.sleep(push_interval_s)
        except (WebSocketDisconnect, RuntimeError):
            return  # Browser geschlossen: nichts weiter zu tun

    return app


__all__ = ["SECURITY_HEADERS", "create_app"]
