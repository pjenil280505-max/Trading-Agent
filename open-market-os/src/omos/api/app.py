"""FastAPI application: middleware, error handling, auth endpoints, WebSocket and the web UI."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from omos import __version__
from omos.api.deps import auth, client_ip
from omos.errors import OmosError, RateLimited
from omos.marketdata.ratelimit import TokenBucket

log = logging.getLogger(__name__)

def sanitize(obj: Any) -> Any:
    """Make any result JSON-safe: non-finite floats become None, numpy scalars become Python numbers."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [sanitize(v) for v in obj]
    if hasattr(obj, "item") and hasattr(obj, "dtype"):
        return sanitize(obj.item())
    return obj


class SafeJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(sanitize(content), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "font-src 'self' data:; connect-src 'self'{ws}; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
        "object-src 'none'")
_HOSTPORT = re.compile(r"^(?:[a-z0-9.-]+|\[[0-9a-f:.]+\])(?::\d{1,5})?$")


def csp_for(host_header: str) -> str:
    """CSP with WebSocket access to THIS host only (older browsers do not match ws: against 'self').
    The Host header was already checked against the allow-list; the pattern check is defence in depth."""
    ws = f" ws://{host_header} wss://{host_header}" if _HOSTPORT.match(host_header) else ""
    return _CSP.format(ws=ws)


def web_dist() -> Path | None:
    try:
        p = Path(str(resources.files("omos").joinpath("web").joinpath("dist")))
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    return p if (p / "index.html").exists() else None


class Guard(BaseHTTPMiddleware):
    """Host-header allow-list (DNS-rebinding defence), per-client rate limit, security headers."""

    def __init__(self, app, *, allowed_hosts: set[str], loopback_only: bool, refresh=None):
        super().__init__(app)
        self.allowed = allowed_hosts
        self.loopback_only = loopback_only
        self.refresh = refresh   # re-reads this machine's addresses (e.g. after a DHCP change)
        self.refreshed_at = 0.0
        self.buckets: dict[str, TokenBucket] = {}

    async def dispatch(self, request: Request, call_next):
        host_header = (request.headers.get("host") or "").lower()
        hostname = host_header.rsplit(":", 1)[0] if not host_header.startswith("[") else host_header.split("]")[0] + "]"
        if hostname not in self.allowed and self.refresh and time.monotonic() - self.refreshed_at > 30:
            self.refreshed_at = time.monotonic()
            self.allowed = self.allowed | set(self.refresh())
        if hostname not in self.allowed:
            return JSONResponse({"error": "bad_host", "message": "request Host header is not allowed"}, status_code=421)
        ip = client_ip(request)
        if self.loopback_only and ip not in ("127.0.0.1", "::1", "testclient", "unknown"):
            return JSONResponse({"error": "remote_disabled", "message": "remote access is disabled on this host"},
                                status_code=403)
        if ip not in ("127.0.0.1", "::1", "testclient"):
            b = self.buckets.setdefault(ip, TokenBucket(20.0, 60.0))
            if not b.try_acquire():
                return JSONResponse({"error": "rate_limited", "message": "too many requests"}, status_code=429)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = csp_for(host_header)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        if request.url.scheme == "https" or hostname in ("127.0.0.1", "localhost", "[::1]"):
            # browsers ignore (and log an error about) COOP on plain-HTTP LAN origins
            response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    # uvicorn re-raises SIGTERM/SIGINT after its graceful shutdown, so code placed after uvicorn.run()
    # never runs on a signal. Cleanup registered here runs during that graceful shutdown.
    for hook in getattr(app.state, "shutdown_hooks", []):
        try:
            await asyncio.to_thread(hook)
        except Exception:  # noqa: BLE001 - one failing hook must not skip the others
            log.exception("shutdown hook failed")


def create_app(host, *, testing: bool = False) -> FastAPI:
    app = FastAPI(title="Open Market OS", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
                  default_response_class=SafeJSONResponse, lifespan=_lifespan)
    app.state.host = host
    app.state.shutdown_hooks = []
    allowed = set(host.cfg.allowed_host_headers())
    if testing:
        allowed.add("testserver")
    app.add_middleware(Guard, allowed_hosts=allowed, loopback_only=not host.cfg.allow_remote and not testing,
                       refresh=host.cfg.allowed_host_headers if host.cfg.allow_remote else None)

    @app.exception_handler(OmosError)
    async def omos_error(request: Request, exc: OmosError):
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):  # never leak stack traces to users
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"error": "internal_error", "message": "Something went wrong. Details are in the host log "
                                                                   "(Settings > Diagnostics)."}, status_code=500)

    # -- public ------------------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.post("/api/auth/exchange")
    def exchange(request: Request, payload: dict[str, Any] = Body(...)):
        token, info = host.auth.exchange_code(str(payload.get("code", "")), device_name=str(payload.get("device_name", ""))[:80],
                                              client=client_ip(request))
        return {"token": token, "token_info": info}

    @app.get("/api/auth/me")
    def me(info=Depends(auth("read"))):
        return {"id": info.id, "name": info.name, "kind": info.kind, "scopes": info.scopes}

    # -- business routes -----------------------------------------------------------------------
    from omos.api.routes import router
    from omos.api.routes_system import router as system_router

    app.include_router(router)
    app.include_router(system_router)

    # -- WebSocket: first message must be {"type":"auth","token":...} --------------------------------
    @app.websocket("/api/ws")
    async def ws(socket: WebSocket):
        hh = (socket.headers.get("host") or "").lower().rsplit(":", 1)[0]
        if hh not in allowed and not (host.cfg.allow_remote and hh in host.cfg.allowed_host_headers()):  # new LAN address
            await socket.close(code=4403)
            return
        await socket.accept()
        try:
            first = await asyncio.wait_for(socket.receive_text(), timeout=5)
            msg = json.loads(first)
            info = host.auth.verify(msg.get("token"), client=socket.client.host if socket.client else "ws")
            if "read" not in info.scopes:
                raise OmosError("read scope required")
        except (asyncio.TimeoutError, ValueError, OmosError, WebSocketDisconnect):
            try:
                await socket.send_text(json.dumps({"topic": "error", "data": {"message": "authentication failed"}}))
                await socket.close(code=4401)
            except Exception:
                pass
            return
        loop = asyncio.get_running_loop()
        token, queue = host.bus.async_queue(loop)
        subs: set[tuple[str, str]] = set()
        await socket.send_text(json.dumps({"topic": "hello", "data": {"version": __version__, "scopes": info.scopes}}))

        async def reader():
            while True:
                raw = await socket.receive_text()
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                if m.get("type") == "subscribe" and isinstance(m.get("instrument"), str) and isinstance(m.get("tf"), str):
                    key = (m["instrument"][:80], m["tf"][:8])
                    subs.add(key)
                    host.subscribe(*key)
                elif m.get("type") == "unsubscribe":
                    subs.discard((m.get("instrument"), m.get("tf")))
                elif m.get("type") == "ping":
                    await socket.send_text(json.dumps({"topic": "pong", "data": {}}))

        async def keepalive():
            while True:
                await asyncio.sleep(30)
                for key in list(subs):
                    host.subscribe(*key)  # refresh subscription liveness

        async def writer():
            while True:
                evt = await queue.get()
                if evt["topic"] == "candle":
                    d = evt["data"]
                    if (d["instrument"], d["timeframe"]) not in subs:
                        continue
                await socket.send_text(json.dumps(evt, default=str))

        tasks = [asyncio.create_task(reader()), asyncio.create_task(writer()), asyncio.create_task(keepalive())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            host.bus.unsubscribe(token)

    # -- web UI ------------------------------------------------------------------------------------
    dist = web_dist()

    @app.get("/{path:path}", include_in_schema=False)
    def ui(path: str):
        if path.startswith("api/"):
            return JSONResponse({"error": "not_found", "message": "unknown API route"}, status_code=404)
        if dist is None:
            return HTMLResponse("<h1>Open Market OS</h1><p>The web UI has not been built. Run <code>scripts/build_web.sh</code>.</p>")
        target = (dist / path).resolve()
        if path and target.is_file() and dist.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def json_response(data: Any, status: int = 200) -> Response:
    return JSONResponse(data, status_code=status)


__all__ = ["create_app", "RateLimited"]
