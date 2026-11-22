# MCP guide

The Model Context Protocol lets AI assistants call tools. Open Market OS works in both directions:

- **Server:** assistants such as Claude Desktop or Claude Code use *our* tools (quotes, candles,
  backtests…) through `omos mcp`.
- **Client:** Open Market OS calls tools of *external* MCP servers you add (from workflows or
  Settings → MCP).

## Exposing our tools to an assistant

1. Start the host (`omos start`).
2. **Settings → MCP**: tick the tools the assistant may use (table below), then **Create bridge
   token**. The token is saved to `mcp_token` in your data folder, readable only by you.
3. Add the bridge to your assistant. Claude Desktop (`claude_desktop_config.json`):

   ```json
   { "mcpServers": { "open-market-os": { "command": "omos", "args": ["mcp"] } } }
   ```

   Claude Code: `claude mcp add open-market-os -- omos mcp`

   If `omos` is not on the assistant's `PATH`, use the full path (e.g.
   `/home/you/open-market-os/.venv/bin/omos` or the single-file app). If you use a custom data
   folder, also set `OMOS_HOME` in the server's environment. The bridge finds the running host even
   if it was started with `--port`.
4. Ask the assistant something like "use open-market-os to get the daily RSI of OKX:BTC-USDT".

The bridge is a thin client: every call goes to the host with the MCP token, and **the host**
checks that the named tool is enabled and that the request matches that tool's routes. Disabled
tools are not even listed. Every call is in the audit log.

## Permission matrix

| Tool | Level | Default | Reaches |
|---|---|---|---|
| `get_quote` | read | on | latest price with data label |
| `get_candles` | read | on | OHLCV candles with data label |
| `search_instruments` | read | on | instrument search |
| `get_indicator` | read | on | compute an indicator |
| `analyze_symbol` | read | on | rule-based evidence pack (no AI) |
| `list_watchlists` | read | on | your watchlists |
| `run_screener` | read | on | run a screen over a watchlist |
| `run_backtest` | read | on | backtest a script (no orders) |
| `get_bot_status` | read | on | bot list and states |
| `get_bot_logs` | read | on | a bot's recent log lines |
| `list_alerts` | read | on | alerts |
| `get_portfolio` | private | **off** | balances, positions, P&L |
| `create_alert` | state | **off** | create an alert |
| `create_strategy_draft` | state | **off** | save a script as a draft (never runs it) |
| `start_paper_bot` | state | **off** | start an existing paper bot (live bots refused) |
| `pause_bot` | state | **off** | pause a bot |
| `stop_bot` | state | **off** | stop a bot and cancel its orders |

There is deliberately **no** tool for live trading, placing orders, deciding order proposals,
reading secrets, changing settings, installing plugins, running commands or accessing files.

## Connecting external MCP servers

1. **Settings → MCP → Add a server**: a name and either
   - **stdio**: a program and arguments (e.g. `npx` `-y @modelcontextprotocol/server-everything`) —
     it runs on your computer with your permissions; shell operators are refused; or
   - **HTTP**: an `https://` URL (plain `http://` only for `localhost`). Authentication headers are
     not supported yet.
2. **Discover tools** lists its tools, resources and prompts. Tools start **disabled**.
3. Tick the tools you want. **Call…** lets you try one with JSON arguments.
4. In **Automation**, an "MCP tool" step can call enabled tools.

Calls have a timeout (default 30 s), output is capped and labelled untrusted, and errors from the
server are reported as errors. Treat external servers like any software you install.
