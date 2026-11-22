# Open Market OS 0.1.0 — final report

Date: 2026-11-22 · Source: `open-market-os/` on branch `ccr-80d6e707-jb6y48`

**The short version:** a working, tested, local-first market workstation with charts, indicators, a
strategy language and honest backtester, screener, research, paper trading, bots behind a
non-bypassable risk engine, optional AI and MCP — at zero cost. It is **not** finished as specified:
it cannot trade real money (no live broker adapter), only Linux builds were verified, and 37 of the
167 tracked feature items are not started, not supported or blocked. Nothing below claims more than
was tested.

## A. Project summary

Built: a Python host (~12,600 lines) serving an HTTP/WebSocket API and a Preact web UI (~3,400
lines, 14 pages), with ~3,500 lines of tests.

- **Data:** 7 free sources (OKX, Coinbase, Kraken, Binance live-polled; Yahoo delayed; FRED, ECB
  historical), CSV import and a labelled simulator, with status labels on every price.
- **Charts and analysis:** KLineChart-based layouts up to 16 charts with 21 persistent drawing tools;
  34 indicators checked against reference libraries; OMS Script; a backtester with costs,
  walk-forward, out-of-sample and Monte Carlo; screener; SEC/FRED/RSS research.
- **Trading:** paper broker; rule, DCA and grid bots with restart recovery; risk engine with
  circuit breakers and emergency stop; alerts.
- **Extensions:** optional AI analysts (local or hosted), MCP server and client, plugins,
  automation workflows.

Phase 12–13 testing found and fixed 17 defects, including one that deleted users' saved drawings
and one that made remote (phone) access impossible
([list](DEVELOPMENT_STATUS.md#bugs-found-and-fixed-during-release-testing)).

## B. Installation

- **Linux (tested):** unpack `omos-0.1.0-linux-x86_64.tar.gz`, run `./omos start`, open the printed
  link. Or from source: Python 3.11+, Node 20+, `pip install -e .`, `scripts/build_web.sh`,
  `omos start`.
- **Windows/macOS (untested):** the same source steps; see the guide for PowerShell differences.

Details, data locations, autostart, update, backup/restore and uninstall:
[INSTALLATION_GUIDE](guides/INSTALLATION_GUIDE.md).

## C. START HERE

[START_HERE.md](../START_HERE.md): install, open the one-time link, then Dashboard → Charts →
Strategy lab (backtest + walk-forward) → paper order → a bot (Validate → Start) → find the
emergency stop. Everything is paper money.

## D. Supported platforms

| Platform | Built | Tested |
|---|---|---|
| Linux x86_64 | Yes (wheel + single-file binary) | Yes: tests, E2E, release verification, clean install |
| Windows 10/11 | No | No (source install expected to work) |
| macOS | No | No |
| Linux ARM | No | No |
| Android / iOS | No app | Usable as a browser client over the LAN; layout tested only in an emulated phone viewport |

## E. Feature matrix

167 items from the specification ([FEATURE_MATRIX.md](FEATURE_MATRIX.md)):

| State | Count |
|---|---|
| VERIFIED | 72 |
| TESTED | 19 |
| IMPLEMENTED | 20 |
| PARTIAL | 19 |
| NOT STARTED | 28 |
| NOT SUPPORTED | 7 |
| BLOCKED | 2 |

Biggest gaps: live trading and MetaTrader 5; Windows/macOS/Android builds; Renko, point-and-figure
and baseline charts; compare/overlay symbols; keyboard shortcuts; bar replay; multi-symbol backtests;
visual node editors.

## F. Trading bots

Bots → **+ New bot** (rule / DCA / grid, instrument, timeframe, paper account) → **Create** →
**Validate** → **Backtest** (rule bots) → **Start**. Then **Pause**, **Resume**, **Stop & cancel
orders**, **Clone**, edit the configuration while paused or stopped (it must be re-validated), and
read **Logs** and **State history**.

Bots decide once per closed bar, check stops every 10 s, and never duplicate orders after a restart.
[BOT_GUIDE](guides/BOT_GUIDE.md)

## G. 24/7 operation

No VPS is needed, but the computer must stay on, awake and online.

- **Autostart:** `omos service install`. On Linux, also run `loginctl enable-linger`.
- **Power and sleep:** disable sleep on AC power, keep laptops plugged in, and consider a UPS.
- **Network:** prefer a wired connection.
- **Recovery:** after a restart the host reconciles orders and resumes running bots. Outages longer
  than about 5 minutes put bots into `error` until you restart them, so they never trade on stale data.

Steps per OS: [BOT_GUIDE → Running 24/7](guides/BOT_GUIDE.md#running-247-on-a-home-computer).

## H. AI

**Settings → AI → add a provider:**

- **Claude:** `anthropic` preset with an API key from console.anthropic.com. API usage is paid
  separately from Claude.ai plans.
- **Other hosted models:** OpenAI, Gemini, Grok, DeepSeek, Mistral, Groq, OpenRouter, or any
  OpenAI-compatible endpoint.
- **Local:** `ollama` (free; `ollama pull qwen2.5:3b` for example), LM Studio or llama.cpp.

Hosted providers stay off until you type a cost acknowledgement, and they are stopped by a daily
budget. In this release only Ollama (`qwen2.5:0.5b`) and the mock were exercised; the hosted presets
were not run (no keys).

AI never places orders: it can only propose them for your approval. [AI_GUIDE](guides/AI_GUIDE.md)

## I. MCP

- **Expose our tools:** Settings → MCP → tick tools → **Create bridge token**. Then
  `claude mcp add open-market-os -- omos mcp` (Claude Code), or add
  `{"command":"omos","args":["mcp"]}` to Claude Desktop's `mcpServers`. 11 read tools are on by
  default; 6 private or state-changing tools are off; there is no trading tool.
- **Use external servers:** Settings → MCP → Add a server (stdio program or HTTPS URL) → Discover
  tools → enable the ones you want.

[MCP_GUIDE](guides/MCP_GUIDE.md)

## J. Security

**Audited:**
- bandit, pip-audit (92 packages), npm audit, detect-secrets and a license inventory, via
  `scripts/security_scan.sh`, which passed;
- a manual review against a threat model of 15 threats;
- security tests covering auth, scopes, MCP enforcement, prompt injection, plugin isolation, CSP
  and Host rules.

**Fixed:**
- Preact JSON-injection advisory; Vite dev-server advisories;
- a CSP that allowed WebSockets to any host;
- a hostile-feed (XXE) crash path;
- an assert guarding vault writes;
- the missing third-party NOTICE.

**Remains:**
- plain HTTP on the LAN unless you configure TLS;
- tokens kept in browser storage;
- process plugins can use the network;
- unsigned binaries;
- `OMOS_VAULT_PASSPHRASE` readable by your user.

**How to secure it:** keep the host on localhost, pair phones with limited presets, keep MCP
private tools off, prefer local AI, and install only trusted plugins.
[SECURITY_GUIDE](guides/SECURITY_GUIDE.md) · [THREAT_MODEL](THREAT_MODEL.md)

## K. Testing

| Command | Result (2026-11-22) |
|---|---|
| `OMOS_RUN_NETWORK=1 pytest` (live sources + Ollama) | 291 passed |
| `pytest` (offline) | 278 passed, 13 skipped |
| `scripts/e2e.sh` / `OMOS_E2E_LAN=1 scripts/e2e.sh` | 12/12 / 13/13 passed |
| `scripts/security_scan.sh` | passed |
| `scripts/build_release.sh && scripts/verify_release.sh` | passed |
| `coverage run --branch -m pytest` | 78% line+branch |

Weakest coverage is in `cli.py` (18%), which the E2E and release scripts exercise outside coverage's
view, and in `host.py` and `mcp/server.py` (55% each).

## L. Performance

Measured on a shared 4-vCPU cloud container ([PERFORMANCE_BENCHMARKS.md](PERFORMANCE_BENCHMARKS.md)):

| Measurement | Result |
|---|---|
| All 34 indicators | 204 ms on 10,000 bars; 725 ms on 50,000 |
| Backtest | 32 ms on 10,000 bars; 150 ms on 50,000 |
| 400-combination optimisation | 6.7 s on 5,000 bars |
| API latency (median) | 2 ms (health) to 34 ms (2,000-bar backtest) |
| UI bundle | 117 KB gzipped; warm chart open 72 ms |
| Host memory | ~100–112 MB |
| First fetch of 5,000 hourly OKX bars | ~9 s (network-bound) |

Phones and low-end PCs were not measured.

## M. Data

| Label | Sources |
|---|---|
| REALTIME (polled every 5–30 s, not streamed) | OKX, Coinbase, Kraken, Binance (Binance via its public mirror where geo-blocked) |
| DELAYED | Yahoo Finance: unofficial, personal non-commercial use only, short intraday history |
| HISTORICAL | FRED (no consensus forecasts), ECB (daily), CSV |
| As filed | SEC EDGAR (US filers; needs your contact e-mail) |
| SIMULATED | Demo data only |

All live-provider tests passed on 2026-11-22. [DATA_SOURCES](DATA_SOURCES.md) ·
[DATA_GUIDE](guides/DATA_GUIDE.md)

## N. Known bugs and limitations

All are in [KNOWN_ISSUES.md](KNOWN_ISSUES.md). Most important:

- No exchange holidays: paper fills on holidays use the last price.
- VWAP resets at UTC midnight, not at the exchange session open.
- ADX differs from TA-Lib early in the history.
- Heikin-Ashi charts don't update live.
- On the LAN, browser live updates were verified only with a raw WebSocket client: this sandbox's
  browser path returned 502.
- No component-level UI tests.
- No hosted CI.

## O. Release artifacts

Built in `open-market-os/releases/`. These are build outputs and are not committed; rebuild with
`scripts/build_release.sh`.

| File | SHA-256 |
|---|---|
| `open_market_os-0.1.0-py3-none-any.whl` (339 KB) | `510c7cd328aca446052d2a970dfac54129150e7b187298ebcdfa1d0e4ae23dc2` |
| `omos-0.1.0-linux-x86_64.tar.gz` (52.6 MB) | `0ed2d13de5b13e8f07daa153f1a3b3323363cc99f31ac0b08a12ab751cd6c978` |

Install: `pip install open_market_os-0.1.0-py3-none-any.whl`, or unpack the tarball and run
`./omos start`.

Not built: Windows installer, macOS package, Android APK.

## P. Next steps

These are genuine remaining work only.

1. Build and verify on Windows and macOS: run the two release scripts on each OS.
2. Add a first live adapter (exchange spot API with testnet), behind the existing `trade:live`,
   approval and switch gates; test on the testnet first.
3. Add exchange holiday calendars and session-anchored VWAP.
4. Chart features: compare and overlay symbols, keyboard shortcuts, a chart-templates UI, and
   volume profile and structure zones drawn on the chart.
5. Multi-symbol backtests.
6. Raise coverage of `cli.py`, `host.py` and `mcp/server.py`, and add a watchdog test.
7. Hosted CI running the same scripts.
