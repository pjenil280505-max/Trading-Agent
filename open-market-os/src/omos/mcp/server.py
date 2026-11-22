"""Our MCP server: a thin bridge that exposes Open Market OS tools to MCP clients
(Claude Desktop, Claude Code, Cursor, ...).

It holds no state and no secrets of its own: every tool is an HTTP call to the running host
with an MCP-scoped token, and the host decides (permission matrix + scopes + risk engine).

Run it with ``omos mcp`` (stdio). Configure your MCP client with::

    {"mcpServers": {"open-market-os": {"command": "omos", "args": ["mcp"]}}}

The token is read from ``OMOS_MCP_TOKEN`` or from ``<data dir>/mcp_token`` (created by
Settings > MCP > Create bridge token; file mode 0600).
"""

from __future__ import annotations

import logging

import json
import os
from pathlib import Path
from typing import Any

import httpx

from omos import __version__
from omos.mcp.permissions import BY_NAME


class HostAPI:
    """HTTP client for the local host, always tagging the calling MCP tool."""

    def __init__(self, base_url: str, token: str, *, client: httpx.Client | None = None):
        self.base = base_url.rstrip("/")
        self.token = token
        self.client = client or httpx.Client(timeout=60, trust_env=False)

    def call(self, tool: str, method: str, path: str, *, params: dict[str, Any] | None = None,
             body: dict[str, Any] | None = None) -> Any:
        try:
            r = self.client.request(method, self.base + path, params=params, json=body,
                                    headers={"Authorization": f"Bearer {self.token}", "X-OMOS-MCP-Tool": tool})
        except httpx.HTTPError as exc:
            return {"error": "host_unreachable", "message": f"Open Market OS host is not reachable at {self.base} "
                                                            f"({type(exc).__name__}). Start it with 'omos start'."}
        try:
            data = r.json()
        except ValueError:
            data = {"error": "bad_response", "status": r.status_code}
        if r.status_code >= 400:
            return {"error": data.get("error", "error"), "message": data.get("message", f"HTTP {r.status_code}")}
        return data


def _trim(obj: Any, limit: int = 60_000) -> Any:
    text = json.dumps(obj, default=str)
    if len(text) <= limit:
        return obj
    return {"truncated": True, "preview": text[:limit]}


def build_server(api: HostAPI, enabled: dict[str, bool] | None = None):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("open-market-os", version=__version__,
                       instructions=("Market data, analysis, backtests and paper-bot control for a local Open Market OS host. "
                                     "Prices carry status labels (REALTIME/DELAYED/HISTORICAL/SIMULATED). "
                                     "There are no live-trading tools. Some tools may be disabled by the user."))
    on = enabled if enabled is not None else {n: t.default for n, t in BY_NAME.items()}

    def add(name: str, fn) -> None:
        if on.get(name):
            server.add_tool(fn, name=name, description=BY_NAME[name].description + f" [permission level: {BY_NAME[name].level}]")

    def get_quote(instrument_id: str) -> dict:
        return _trim(api.call("get_quote", "GET", "/api/quote", params={"instrument": instrument_id}))

    def get_candles(instrument_id: str, timeframe: str = "1d", limit: int = 200) -> dict:
        return _trim(api.call("get_candles", "GET", "/api/candles",
                              params={"instrument": instrument_id, "tf": timeframe, "limit": max(1, min(int(limit), 2000))}))

    def search_instruments(query: str) -> dict:
        return _trim(api.call("search_instruments", "GET", "/api/instruments/search", params={"q": query}))

    def get_indicator(instrument_id: str, indicator: str, timeframe: str = "1d", params: dict | None = None,
                      limit: int = 300) -> dict:
        return _trim(api.call("get_indicator", "POST", "/api/indicators/compute",
                              body={"instrument": instrument_id, "tf": timeframe, "id": indicator, "params": params or {},
                                    "limit": max(1, min(int(limit), 2000)), "tail": 50}))

    def analyze_symbol(instrument_id: str, timeframe: str = "1d") -> dict:
        return _trim(api.call("analyze_symbol", "GET", "/api/analysis", params={"instrument": instrument_id, "tf": timeframe}))

    def list_watchlists() -> dict:
        return _trim(api.call("list_watchlists", "GET", "/api/watchlists"))

    def run_screener(instruments: list[str], timeframe: str = "1d", rules: list[dict] | None = None) -> dict:
        return _trim(api.call("run_screener", "POST", "/api/screener/run", body={"definition": {
            "universe": {"instruments": instruments[:200]}, "timeframe": timeframe,
            "filter": {"op": "and", "rules": rules or []}}}))

    def run_backtest(script: str, instrument_id: str, timeframe: str = "1d", limit: int = 1000) -> dict:
        res = api.call("run_backtest", "POST", "/api/backtest",
                       body={"source": script, "instrument": instrument_id, "tf": timeframe,
                             "limit": max(50, min(int(limit), 5000))})
        if isinstance(res, dict) and "metrics" in res:
            res = {"metrics": res["metrics"], "warnings": res.get("warnings"), "trades": res.get("trades", [])[-20:],
                   "data": res.get("data")}
        return _trim(res)

    def get_bot_status() -> dict:
        return _trim(api.call("get_bot_status", "GET", "/api/bots"))

    def get_bot_logs(bot_id: int, limit: int = 50) -> dict:
        return _trim(api.call("get_bot_logs", "GET", f"/api/bots/{int(bot_id)}/logs", params={"limit": min(int(limit), 500)}))

    def list_alerts() -> dict:
        return _trim(api.call("list_alerts", "GET", "/api/alerts"))

    def get_portfolio() -> dict:
        return _trim(api.call("get_portfolio", "GET", "/api/accounts"))

    def create_alert(instrument_id: str, kind: str, params: dict, name: str = "", timeframe: str = "1m") -> dict:
        return _trim(api.call("create_alert", "POST", "/api/alerts", body={"instrument_id": instrument_id, "kind": kind,
                                                                         "params": params, "name": name or f"MCP {kind}",
                                                                         "timeframe": timeframe}))

    def create_strategy_draft(name: str, script: str) -> dict:
        return _trim(api.call("create_strategy_draft", "POST", "/api/strategies",
                              body={"name": f"draft: {name}"[:60], "source": script, "note": "created via MCP"}))

    def start_paper_bot(bot_id: int) -> dict:
        return _trim(api.call("start_paper_bot", "POST", f"/api/bots/{int(bot_id)}/start", params={"require_paper": 1}))

    def pause_bot(bot_id: int) -> dict:
        return _trim(api.call("pause_bot", "POST", f"/api/bots/{int(bot_id)}/pause"))

    def stop_bot(bot_id: int) -> dict:
        return _trim(api.call("stop_bot", "POST", f"/api/bots/{int(bot_id)}/stop"))

    for name, fn in [("get_quote", get_quote), ("get_candles", get_candles), ("search_instruments", search_instruments),
                     ("get_indicator", get_indicator), ("analyze_symbol", analyze_symbol),
                     ("list_watchlists", list_watchlists), ("run_screener", run_screener),
                     ("run_backtest", run_backtest), ("get_bot_status", get_bot_status), ("get_bot_logs", get_bot_logs),
                     ("list_alerts", list_alerts), ("get_portfolio", get_portfolio), ("create_alert", create_alert),
                     ("create_strategy_draft", create_strategy_draft), ("start_paper_bot", start_paper_bot),
                     ("pause_bot", pause_bot), ("stop_bot", stop_bot)]:
        add(name, fn)
    return server


def load_token(data_dir: Path) -> str:
    tok = os.environ.get("OMOS_MCP_TOKEN", "").strip()
    if tok:
        return tok
    f = data_dir / "mcp_token"
    if f.exists():
        return f.read_text().strip()
    raise SystemExit("No MCP bridge token. Create one in Settings > MCP > 'Create bridge token' "
                     "(or set OMOS_MCP_TOKEN).")


def serve_stdio(data_dir: Path, base_url: str) -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # per-request INFO lines are noise on the client's stderr
    api = HostAPI(base_url, load_token(data_dir))
    enabled = None
    perms = api.call("get_quote", "GET", "/api/mcp/self")  # host tells the bridge which tools are enabled
    if isinstance(perms, dict) and isinstance(perms.get("enabled"), dict):
        enabled = perms["enabled"]
    build_server(api, enabled).run("stdio")
