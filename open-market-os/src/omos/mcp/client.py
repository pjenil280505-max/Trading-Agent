"""MCP client: connect to EXTERNAL MCP servers the user configures.

External servers are untrusted code or services:
* only admins can add them; a stdio server is a program on this machine that runs with the
  host's user rights — the UI says so before saving;
* every discovered tool starts DISABLED; a person enables tools one by one;
* calls have a timeout and an output cap, are logged to the audit log, and results are
  marked untrusted before an AI model ever sees them;
* our secrets are never passed in the subprocess environment (the SDK passes a minimal
  default environment).
"""

from __future__ import annotations

import asyncio
import json
import shlex
from typing import Any
from urllib.parse import urlparse

from omos.errors import NotFound, OmosError, PermissionDenied, ValidationError
from omos.storage.db import Database
from omos.util import dumps, loads, now_ms

MAX_OUTPUT = 100_000


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # called from inside an event loop thread: run in a fresh loop on a worker thread
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


class MCPClientService:
    def __init__(self, db: Database):
        self.db = db

    # -- configuration -----------------------------------------------------------------
    def add(self, name: str, *, transport: str, command: str = "", args: list[str] | None = None, url: str = "",
            timeout_s: float = 30.0, actor: str = "ui") -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 60:
            raise ValidationError("server name must be 1–60 characters")
        if transport == "stdio":
            if not command or len(command) > 300 or any(c in command for c in ";|&`$\n"):
                raise ValidationError("give the program to run (no shell operators); arguments go in 'args'")
            args = [str(a)[:500] for a in (args or [])][:30]
        elif transport == "http":
            p = urlparse(url)
            if p.scheme not in ("http", "https") or not p.hostname:
                raise ValidationError("HTTP MCP servers need an http(s):// URL")
            if p.scheme == "http" and p.hostname not in ("127.0.0.1", "localhost", "::1"):
                raise ValidationError("remote MCP servers must use https://")
        else:
            raise ValidationError("transport must be stdio or http")
        if not 1 <= timeout_s <= 300:
            raise ValidationError("timeout must be 1–300 seconds")
        cur = self.db.execute("INSERT INTO mcp_servers(name, transport, command, args, url, enabled, timeout_s, created_at) "
                              "VALUES (?,?,?,?,?,1,?,?)",
                              (name, transport, command, dumps(args or []), url, timeout_s, now_ms()))
        self.db.audit(actor, "mcp.server.add", name, {"transport": transport, "command": command, "url": url})
        return self.get(int(cur.lastrowid))

    def get(self, sid: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM mcp_servers WHERE id = ?", (sid,))
        if not row:
            raise NotFound(f"MCP server {sid} not found")
        row["args"] = loads(row["args"], [])
        row["tools"] = self.db.query("SELECT tool_name, enabled, description FROM mcp_tool_permissions WHERE server_id = ? "
                                     "ORDER BY tool_name", (sid,))
        return row

    def list(self) -> list[dict[str, Any]]:
        return [self.get(r["id"]) for r in self.db.query("SELECT id FROM mcp_servers ORDER BY id")]

    def delete(self, sid: int, actor: str = "ui") -> None:
        self.db.execute("DELETE FROM mcp_servers WHERE id = ?", (sid,))
        self.db.audit(actor, "mcp.server.delete", str(sid))

    def set_enabled(self, sid: int, enabled: bool) -> dict[str, Any]:
        self.db.execute("UPDATE mcp_servers SET enabled = ? WHERE id = ?", (int(enabled), sid))
        return self.get(sid)

    def set_tool(self, sid: int, tool: str, enabled: bool, actor: str = "ui") -> dict[str, Any]:
        n = self.db.execute("UPDATE mcp_tool_permissions SET enabled = ? WHERE server_id = ? AND tool_name = ?",
                            (int(enabled), sid, tool)).rowcount
        if not n:
            raise NotFound(f"tool '{tool}' not found on server {sid}; refresh the server first")
        self.db.audit(actor, "mcp.tool.permission", f"{sid}:{tool}", {"enabled": enabled})
        return self.get(sid)

    # -- talking to servers -------------------------------------------------------------------
    def _target(self, s: dict[str, Any]):
        from mcp.client.stdio import StdioServerParameters

        if s["transport"] == "stdio":
            return StdioServerParameters(command=s["command"], args=s["args"])
        return s["url"]

    async def _with_client(self, s: dict[str, Any], fn):
        from mcp import Client

        async with Client(self._target(s), read_timeout_seconds=float(s["timeout_s"])) as client:
            return await asyncio.wait_for(fn(client), timeout=float(s["timeout_s"]))

    def refresh(self, sid: int) -> dict[str, Any]:
        """Connect, list tools/resources/prompts, and register new tools as DISABLED."""
        s = self.get(sid)
        if not s["enabled"]:
            raise PermissionDenied("this MCP server is disabled")

        async def discover(client):
            tools = await client.list_tools()
            out = {"tools": [{"name": t.name, "description": (t.description or "")[:500]} for t in tools.tools]}
            for kind in ("list_resources", "list_prompts"):
                try:
                    res = await getattr(client, kind)()
                    items = getattr(res, "resources", None) or getattr(res, "prompts", None) or []
                    out[kind.split("_")[1]] = [getattr(i, "name", str(i)) for i in items][:100]
                except Exception:  # optional capabilities
                    out[kind.split("_")[1]] = []
            return out

        try:
            found = _run(self._with_client(s, discover))
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise OmosError(f"MCP server '{s['name']}' did not answer within {s['timeout_s']}s", code="mcp_timeout") from exc
        except Exception as exc:
            raise OmosError(f"could not connect to MCP server '{s['name']}': {type(exc).__name__}: {exc}"[:400],
                            code="mcp_connect_failed") from exc
        for t in found["tools"]:
            self.db.execute("INSERT INTO mcp_tool_permissions(server_id, tool_name, enabled, description) VALUES (?,?,0,?) "
                            "ON CONFLICT(server_id, tool_name) DO UPDATE SET description = excluded.description",
                            (sid, t["name"][:120], t["description"]))
        return {**self.get(sid), "discovered": found}

    def call(self, sid: int, tool: str, args: dict[str, Any] | None = None, *, actor: str = "ui") -> dict[str, Any]:
        s = self.get(sid)
        if not s["enabled"]:
            raise PermissionDenied("this MCP server is disabled")
        perm = next((t for t in s["tools"] if t["tool_name"] == tool), None)
        if not perm:
            raise NotFound(f"tool '{tool}' is unknown; refresh the server")
        if not perm["enabled"]:
            raise PermissionDenied(f"tool '{tool}' is disabled; enable it in Settings > MCP > External servers")
        if args is not None and not isinstance(args, dict):
            raise ValidationError("tool arguments must be an object")

        async def invoke(client):
            return await client.call_tool(tool, args or {})

        started = now_ms()
        try:
            res = _run(self._with_client(s, invoke))
        except (TimeoutError, asyncio.TimeoutError) as exc:
            self.db.audit(actor, "mcp.call.timeout", f"{s['name']}:{tool}")
            raise OmosError(f"tool '{tool}' timed out after {s['timeout_s']}s", code="mcp_timeout") from exc
        except Exception as exc:
            self.db.audit(actor, "mcp.call.error", f"{s['name']}:{tool}", {"error": str(exc)[:300]})
            raise OmosError(f"tool '{tool}' failed: {type(exc).__name__}: {exc}"[:400], code="mcp_call_failed") from exc
        parts = []
        for c in getattr(res, "content", []) or []:
            parts.append(getattr(c, "text", None) or json.dumps(getattr(c, "model_dump", lambda: str(c))(), default=str))
        text = "\n".join(parts)
        truncated = len(text) > MAX_OUTPUT
        is_error = bool(_field(res, "is_error", "isError", False))
        self.db.audit(actor, "mcp.call", f"{s['name']}:{tool}", {"ms": now_ms() - started, "bytes": len(text),
                                                                "is_error": is_error})
        return {"server": s["name"], "tool": tool, "is_error": is_error,
                "content": text[:MAX_OUTPUT], "truncated": truncated, "untrusted": True,
                "structured": _field(res, "structured_content", "structuredContent")}


def _field(res: Any, snake: str, camel: str, default: Any = None) -> Any:
    """Read a result field across MCP SDK versions (v2 uses snake_case attributes, v1 camelCase).
    Reading only the v1 name silently returned the default on v2, hiding tool errors."""
    v = getattr(res, snake, None)
    if v is None:
        v = getattr(res, camel, None)
    return default if v is None else v


def parse_command(line: str) -> tuple[str, list[str]]:
    """Split a user-typed command line into program + args without using a shell."""
    parts = shlex.split(line)
    if not parts:
        raise ValidationError("empty command")
    return parts[0], parts[1:]
