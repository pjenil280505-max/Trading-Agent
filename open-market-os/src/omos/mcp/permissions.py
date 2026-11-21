"""The MCP permission matrix.

Each tool our MCP server exposes is listed here with its level, default and the exact host API
routes it may reach. The HOST enforces this: a request carrying an MCP-scoped token is allowed
only if it names an enabled tool (``X-OMOS-MCP-Tool``) and the route is one of that tool's
routes. The MCP server process is therefore not trusted to police itself.

Levels:
  read   — reads market data or analysis; no state changes                (default: on)
  private— reads your portfolio/trading history                            (default: OFF)
  state  — changes state (alerts, drafts, paper bots)                      (default: OFF)
There is deliberately NO live-trading tool, and no tool can read secrets, change settings,
install plugins, run shell commands or touch arbitrary files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from omos.storage.db import Database


@dataclass(frozen=True)
class MCPTool:
    name: str
    level: str
    default: bool
    description: str
    routes: tuple[tuple[str, str], ...]   # (METHOD, regex path)


MATRIX: list[MCPTool] = [
    MCPTool("get_quote", "read", True, "Latest price of an instrument, with its data-status label.",
            (("GET", r"/api/quote"),)),
    MCPTool("get_candles", "read", True, "Historical OHLCV candles (labelled live/delayed/historical/simulated).",
            (("GET", r"/api/candles"),)),
    MCPTool("search_instruments", "read", True, "Search instruments across enabled data providers.",
            (("GET", r"/api/instruments/search"),)),
    MCPTool("get_indicator", "read", True, "Compute a technical indicator on recent candles.",
            (("POST", r"/api/indicators/compute"),)),
    MCPTool("analyze_symbol", "read", True, "Rule-based evidence pack: returns, indicators, structure (no AI).",
            (("GET", r"/api/analysis"),)),
    MCPTool("list_watchlists", "read", True, "List watchlists and their instruments.", (("GET", r"/api/watchlists"),)),
    MCPTool("run_screener", "read", True, "Run a screener over a watchlist or instrument list.",
            (("POST", r"/api/screener/run"),)),
    MCPTool("run_backtest", "read", True, "Backtest an OMS Script strategy on real data (no trades are placed).",
            (("POST", r"/api/backtest"),)),
    MCPTool("get_bot_status", "read", True, "List bots and their lifecycle state.", (("GET", r"/api/bots"),)),
    MCPTool("get_bot_logs", "read", True, "Recent log lines of a bot.", (("GET", r"/api/bots/\d+/logs"),)),
    MCPTool("list_alerts", "read", True, "List alerts.", (("GET", r"/api/alerts"),)),
    MCPTool("get_portfolio", "private", False, "Paper/live account balances, positions and P&L (your private data).",
            (("GET", r"/api/accounts"), ("GET", r"/api/accounts/\d+/summary"))),
    MCPTool("create_alert", "state", False, "Create a price/indicator alert.", (("POST", r"/api/alerts"),)),
    MCPTool("create_strategy_draft", "state", False, "Save an OMS Script strategy as a draft (never runs it).",
            (("POST", r"/api/strategies"),)),
    MCPTool("start_paper_bot", "state", False, "Start an existing PAPER bot (live bots are refused).",
            (("POST", r"/api/bots/\d+/start"),)),
    MCPTool("pause_bot", "state", False, "Pause a running bot.", (("POST", r"/api/bots/\d+/pause"),)),
    MCPTool("stop_bot", "state", False, "Stop a bot and cancel its open orders.", (("POST", r"/api/bots/\d+/stop"),)),
]
BY_NAME = {t.name: t for t in MATRIX}


def permissions(db: Database) -> dict[str, bool]:
    saved = db.get_setting("mcp.tool_permissions", {}) or {}
    return {t.name: bool(saved.get(t.name, t.default)) for t in MATRIX}


def set_permissions(db: Database, changes: dict[str, bool], actor: str) -> dict[str, bool]:
    cur = permissions(db)
    for k, v in changes.items():
        if k not in BY_NAME:
            raise KeyError(k)
        cur[k] = bool(v)
    db.set_setting("mcp.tool_permissions", cur)
    db.audit(actor, "mcp.permissions", None, changes)
    return cur


def route_allowed(db: Database, tool_name: str | None, method: str, path: str) -> tuple[bool, str]:
    if not tool_name or tool_name not in BY_NAME:
        return False, "MCP requests must name a known tool"
    if not permissions(db).get(tool_name):
        return False, f"MCP tool '{tool_name}' is disabled (Settings > MCP > Our tools)"
    for m, pattern in BY_NAME[tool_name].routes:
        if m == method and re.fullmatch(pattern, path):
            return True, ""
    return False, f"MCP tool '{tool_name}' may not call {method} {path}"


def matrix_table(db: Database) -> list[dict[str, Any]]:
    perms = permissions(db)
    return [{"name": t.name, "level": t.level, "default": t.default, "enabled": perms[t.name],
             "description": t.description, "routes": [f"{m} {p}" for m, p in t.routes]} for t in MATRIX]
