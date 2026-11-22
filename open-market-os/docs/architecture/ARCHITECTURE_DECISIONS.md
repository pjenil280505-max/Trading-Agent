# Architecture Decisions

Each entry gives the decision, why we made it, and what it costs. Entries are never rewritten: when a decision changes, a new entry supersedes the old one.

## ADR-001 — One local host process, with the UI in a browser or PWA

- **Decision:**
  - A single Python process, `omos host`, runs everything local:
    - the REST and WebSocket API (FastAPI and uvicorn);
    - the market-data pollers;
    - the alert, bot and workflow engines;
    - the scheduler and backups.
  - The UI is a static single-page app served by that process.
  - Desktop users open it in a browser, or with `omos open`.
  - Android phones and tablets open the same UI over the local network, after explicit opt-in and device pairing.
- **Why:**
  - One codebase covers Windows, Linux, macOS and Android browsers.
  - Nothing runs in our own cloud.
  - The phone becomes a remote control for the PC that actually hosts the bots. That is the only honest way to offer 24/7 bots without a VPS: Android kills long-running background work.
- **Cost:**
  - No native Android APK and no Windows installer is built in this environment.
  - On a phone the UI is a website or installable PWA, not a native app.
  - Native wrappers can come later (a Tauri shell, or a TWA for Android); the HTTP API does not change.

## ADR-002 — Python for the core

- **Why:**
  - numpy gives fast vectorised indicators and backtests.
  - The official MCP SDK is in Python.
  - It is the language most quantitative users can read and extend.
- **Cost:**
  - Python is slower than Rust for tick-level work. We do not claim microsecond latency.
  - Hot paths are vectorised and measured (see `PERFORMANCE_BENCHMARKS.md`).

## ADR-003 — SQLite in WAL mode as the only database

- **Decision:**
  - Everything durable lives in one file, `omos.db`: candles, settings, workspaces, drawings, orders, fills, positions, bots, alerts, audit log and journal.
  - WAL mode lets the host, the MCP process and the CLI read concurrently.
  - Migrations are numbered SQL scripts applied at startup.
  - The online backup API takes consistent snapshots while the host runs.
- **Why:** zero cost, zero administration, one file to back up.
- **Cost:** one writer at a time. That is fine for a single-user host; not designed for multi-user servers.

## ADR-004 — Provider-qualified instruments; never substitute one for another

- **Rule:** an instrument ID always names its data source and venue, for example `OKX:BTC-USDT`, `COINBASE:BTC-USD` or `YAHOO:AAPL`.
- **Failover:**
  - Allowed only between endpoints of the same venue, for example two Binance hosts serving identical data.
  - Allowed between providers only when the user has declared them equivalent; the UI then shows "source switched from X to Y".
  - BTC-USDT on OKX is never silently replaced with BTC-USD on Coinbase.
- **Labels:** every candle response carries:
  - a `status`: REALTIME, DELAYED, HISTORICAL, SIMULATED, ESTIMATED or UNAVAILABLE;
  - `source`, `fetched_at` and `quality`.
  - The UI shows these labels on every chart.

## ADR-005 — Our own sandboxed strategy language ("OMS Script"), not Python plugins

- **Decision:**
  - Strategies, custom indicators, screener formulas and alert conditions all use one small expression language.
  - A hand-written lexer and parser produce an AST, which a vectorised evaluator runs over numpy arrays.
  - No `eval`. No attribute access. No imports. No file or network access.
  - Hard limits on source size, AST depth, statement count, window lengths and wall-clock evaluation time.
- **Why:**
  - Sandboxing arbitrary Python in-process cannot be made secure.
  - A purpose-built language can be made safe, and it can be checked for look-ahead mechanically: `x[n]` only ever looks backwards, and `tf()` only reads higher-timeframe bars that have already closed.
- **Cost:**
  - It is not Pine Script and does not claim compatibility. Syntax is documented in `docs/reference/SCRIPT_LANGUAGE.md`.
  - Users who need full Python can write a process plugin (ADR-009).

## ADR-006 — One order path: gateway, then risk engine, then broker

- **Decision:**
  - Every order passes through `ExecutionGateway.submit()`, whether it comes from the UI, a bot, an AI agent, an MCP tool or a workflow.
  - The gateway validates the order, asks `RiskEngine.check()` for a decision, and only an approved order reaches `Broker.place()`.
  - Bots, AI agents, MCP handlers and workflows never hold a broker reference; they receive a `TradingContext` that wraps the gateway.
  - Every decision, approved or rejected, is written to `risk_events`.
- **Kill switch:** trading is halted for everyone when either:
  - a `trading_halted` circuit-breaker flag is raised (daily loss, drawdown or order-rate breach); or
  - the user presses emergency stop.
  - Only a person, through the UI or CLI, can clear the halt.
- **Live trading:**
  - Off by default, at three levels: the global setting, the broker adapter and the bot's mode.
  - It needs a separate confirmation step.
  - No live broker adapter is verified in this release; see `FEATURE_MATRIX.md`.

## ADR-007 — AI is optional, untrusted and permission-bound

- **Providers:** adapters for OpenAI-compatible endpoints (which covers Ollama, LM Studio, llama.cpp server, vLLM, DeepSeek, Mistral, Groq, OpenRouter, xAI and Gemini's compatible endpoint) and for Anthropic Messages. None is enabled by default.
- **Tools:**
  - AI tools are registered with a permission level: `read`, `analyze`, `propose` or `act`.
  - The application enforces the level, whatever the model writes.
  - An AI can at most *propose* an order. A person approves it in the UI, and the order still goes through the risk engine.
- **Context rules:**
  - Secrets never enter prompts.
  - Tool output is wrapped and labelled as untrusted data.
  - Outputs must follow a structured schema that separates observations (with evidence references) from calculations, interpretation, predictions, assumptions and uncertainty.

## ADR-008 — MCP server as a thin, scoped client of the host

- **Decision:**
  - `omos mcp` (stdio) is a separate process that calls the host's HTTP API with an MCP-scoped token.
  - The host checks that token's scopes, so the MCP server cannot do anything the permission matrix does not allow.
  - Read-only tools are on by default. State-changing tools (paper bots, alerts, strategy drafts) are off until the user enables them one by one.
  - No live-trading tool exists at all.
- **MCP client:**
  - Connects to external servers over stdio or streamable HTTP.
  - Every external tool is disabled until the user enables it.
  - Calls run with timeouts and output-size caps, and are logged.

## ADR-009 — Plugins: declarative first, process plugins isolated

- **Declarative plugins:**
  - Types: indicator, strategy, screener preset, workflow template, and a generic REST candle provider with an allow-listed host.
  - They are data, interpreted by our own engines, so they are sandboxed by construction.
- **Process plugins:**
  - Run as a subprocess speaking JSON-RPC over stdio.
  - The environment is scrubbed (no secrets), with resource limits on POSIX and timeouts.
  - The host answers only the RPC methods the manifest's granted permissions allow.
- **Cost:** a process plugin's own network access cannot be blocked portably. This is disclosed on install and in `SECURITY.md`.

## ADR-010 — Authentication: per-device bearer tokens, loopback by default

- **Binding:** the host binds to `127.0.0.1`. LAN access is an explicit opt-in.
- **Getting a token:**
  - The browser gets one by exchanging a short-lived bootstrap code. `omos open` prints a URL containing the code.
  - Other devices pair with a one-time code that expires in 5 minutes.
- **Token storage and limits:**
  - Tokens are stored only as SHA-256 hashes.
  - Each token has scopes, an expiry and a last-used time, and can be revoked.
  - Authentication endpoints are rate-limited per client address.
- **Web protections:**
  - No cookies, so cross-site request forgery does not apply.
  - No CORS.
  - A Host-header allow-list blocks DNS rebinding.
  - A strict Content Security Policy is sent.

## ADR-011 — Secret storage

- **Primary:** the OS keyring through `keyring`: Windows Credential Manager, macOS Keychain, or Secret Service on Linux.
- **Fallback:**
  - An AES-256-GCM vault file whose key comes from a user passphrase (scrypt).
  - The host refuses to store secrets in plaintext.
  - On headless Linux without a keyring, the passphrase can come from `OMOS_VAULT_PASSPHRASE`. That trade-off is documented in the security guide.

## ADR-012 — KLineChart behind an adapter

The UI talks only to `web/src/chart/adapter.ts`. Indicator values are calculated by the backend, our single tested implementation, and passed to the chart as custom indicators. The chart library's own indicator formulas are not used, so the screener, backtester and chart always agree.

## Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Free data sources change or block access (Yahoo's API is unofficial) | High | High | Each provider is a plugin. Health monitoring and clear UNAVAILABLE states; never fall back to made-up data. Offline cache. |
| R2 | A user mistakes paper results for real money | Medium | High | Every simulated value is labelled PAPER. Live trading is off by default and cannot be switched on silently. |
| R3 | An AI or MCP agent causes an unwanted action | Medium | High | Permission levels enforced in code. AI can only propose orders. The risk engine cannot be bypassed. Every tool call is logged. |
| R4 | A bug in the backtester inflates results | Medium | High | Look-ahead tests (appending future bars cannot change past decisions). Fees and slippage on by default. Independent cross-checks. |
| R5 | Android OS kills background work | High | Medium | Bots run on a PC host; Android is a remote control. Android host mode is labelled experimental. |
| R6 | Credential leak | Low | Critical | OS keyring or encrypted vault. Log redaction. Secrets never sent to AI. Secret scanning with `scripts/security_scan.sh` (no hosted CI in 0.1.0). |
| R7 | Supply-chain compromise | Low | High | Minimal dependencies, lockfiles, `pip-audit` and `npm audit`, license scan. |
| R8 | One person maintaining a huge scope | High | Medium | A feature matrix that reflects reality; stable core modules prioritised over breadth. |
