# Changelog

## 0.1.0 — 2026-11-22

First release. Paper trading only; verified builds for Linux x86_64. See
[docs/RELEASE_NOTES.md](docs/RELEASE_NOTES.md) for what is included and
[docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) for limitations.

### Added
- Local host with HTTP API, WebSocket live updates, background loops and watchdog; SQLite storage
  with migrations, daily backups and corruption recovery.
- Market data from OKX, Coinbase, Kraken, Binance, Yahoo Finance (delayed, unofficial), FRED, ECB,
  CSV import and a labelled simulator; caching, validation, gap detection, session calendars.
- Web UI: dashboard, multi-chart workspace (KLineChart) with 21 drawing tools, watchlists, screener,
  news/fundamentals/calendar, strategy lab, bots, portfolio, risk, alerts, AI workspace, automation,
  settings, system status; phone layout; pairing for other devices.
- 34 indicators; OMS Script; backtester with optimisation, walk-forward, out-of-sample, Monte Carlo.
- Paper broker, execution gateway and risk engine with circuit breakers and emergency stop; rule,
  DCA and grid bots with restart recovery; alerts and notifications.
- Optional AI analysts (local or hosted), MCP server and client with a permission matrix, plugins,
  automation workflows.
- Scripts for E2E tests, security scans, benchmarks and release builds; full documentation.

### Fixed during release testing
Seventeen defects found by the E2E, packaging and coverage work — including a data-loss bug that
deleted saved drawings, remote access returning 421, shutdown cleanup never running, MCP tool errors
reported as success, and a cache path that could show the previous close as the live price. Listed
in [docs/DEVELOPMENT_STATUS.md](docs/DEVELOPMENT_STATUS.md).

### Security
Preact upgraded to 10.29.8 (JSON VNode injection advisory) and Vite to 7.3.8 (dev-server advisories);
CSP WebSocket sources limited to the host; KLineChart NOTICE shipped with the UI.
