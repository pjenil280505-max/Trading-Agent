"""Request authentication and authorisation helpers for the HTTP API."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request

from omos.errors import AuthRequired, PermissionDenied
from omos.mcp.permissions import route_allowed
from omos.security.auth import TokenInfo


def client_ip(request: Request) -> str:
    # We never trust X-Forwarded-For: the host is not designed to sit behind a proxy by default.
    return request.client.host if request.client else "unknown"


def bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return None


def auth(scope: str) -> Callable[[Request], TokenInfo]:
    """FastAPI dependency: valid token + required scope (+ MCP route check for MCP tokens)."""

    def dep(request: Request) -> TokenInfo:
        host = request.app.state.host
        info = host.auth.verify(bearer(request), client=client_ip(request))
        if info.kind == "mcp":
            if "mcp" not in info.scopes:
                raise PermissionDenied("MCP token lacks the mcp scope")
            if request.url.path != "/api/mcp/self":
                ok, why = route_allowed(host.db, request.headers.get("x-omos-mcp-tool"), request.method, request.url.path)
                if not ok:
                    host.db.audit(info.actor, "mcp.denied", request.url.path, {"reason": why})
                    raise PermissionDenied(why)
                host.db.audit(info.actor, "mcp.call", request.headers.get("x-omos-mcp-tool"), {"path": request.url.path})
            return info
        if scope not in info.scopes:
            raise PermissionDenied(f"this device/token lacks the '{scope}' permission")
        return info

    return dep


def actor(info: TokenInfo) -> str:
    return info.actor


def require_live_scope(info: TokenInfo) -> None:
    if "trade:live" not in info.scopes:
        raise PermissionDenied("live trading needs the 'trade:live' permission on this device")


def no_auth_needed() -> None:
    return None


__all__ = ["auth", "actor", "bearer", "client_ip", "AuthRequired"]
