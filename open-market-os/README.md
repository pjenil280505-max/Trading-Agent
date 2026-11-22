# Open Market OS

A local-first market workstation that runs on your own computer: charts, indicators, a strategy
language with an honest backtester, screeners, news and fundamentals, paper trading, bots with a
risk engine, alerts, optional AI analysts and an MCP bridge for AI assistants — with **no paid
service required**.

> **Status: 0.1.0, early release.** Paper trading only: no live broker adapter has been verified,
> so no real orders can be sent. Not financial advice. Backtests and AI output are not predictions.
> See [docs/FEATURE_MATRIX.md](docs/FEATURE_MATRIX.md) for exactly what works and what does not.

## Why it is different

- **Honest data labels.** Every price carries a label: `REALTIME`, `DELAYED`, `HISTORICAL`,
  `SIMULATED` or `STALE`. Delayed data is never shown as live, one instrument is never silently
  replaced by another, and demo data is labelled `SIMULATED` everywhere it appears.
- **Your computer, your data.** The host listens on `127.0.0.1` by default. Phones and tablets
  join only after you pair them with a one-time code. There is no cloud account and no telemetry.
- **A risk engine nothing can bypass.** Every order — from you, a bot, a workflow or an AI
  proposal — goes through the same gateway and risk checks: exposure, leverage, risk per trade,
  daily/weekly loss and drawdown circuit breakers, an emergency stop. AI can only *propose* orders;
  a person approves each one.
- **Backtests that charge you.** Fills at the next bar's open, commission, slippage and spread on
  every fill, gap-aware stops, plus walk-forward, out-of-sample and Monte Carlo checks.
- **Zero cost by default.** Free public data (OKX, Coinbase, Kraken, Binance, Yahoo Finance
  (unofficial, delayed), FRED, ECB, SEC EDGAR), free local AI via Ollama, open-source libraries
  only. Paid AI providers are optional, off by default and budget-capped.

## Quick start (from source)

Requirements: Python 3.11+, Node.js 20+ (only to build the web UI), Git.

```bash
git clone <this repository>
cd open-market-os
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
scripts/build_web.sh                                  # builds the UI into src/omos/web/dist
omos start                                            # prints a one-time sign-in link
```

Open the printed link (valid 10 minutes, single use). Add `--demo` to also enable the
`SIM:DEMO-*` simulated instruments, which work offline.

Other commands: `omos open` (new sign-in link), `omos status`, `omos doctor`, `omos backup`,
`omos export`, `omos mcp` (MCP bridge for Claude Desktop/Code and other MCP clients),
`omos service install` (start at login). See the [installation guide](docs/guides/INSTALLATION_GUIDE.md).

## What is inside

| Area | Highlights |
|---|---|
| Charts | KLineChart-based multi-chart layouts (1–16 cells), sync, 20+ drawing tools stored with time/price anchors, Heikin-Ashi, log/% scales, snapshots |
| Indicators | 34 indicators computed by one tested implementation shared by charts, screener and backtests; no-look-ahead tests for every one |
| OMS Script | A small sandboxed language for indicators and strategies (`x[n]` look-back only, `tf("4h", …)` with completed higher-timeframe bars) |
| Strategy lab | Backtests with costs, grid optimisation (capped), walk-forward, out-of-sample, Monte Carlo |
| Trading | Paper accounts, order ticket (market/limit/stop/stop-limit), positions, journal, analytics, CSV export |
| Bots | Rule, DCA and grid bots; closed-bar decisions; restart-safe idempotent orders; lifecycle with validation and backtest gates |
| Research | RSS news with a transparent sentiment heuristic, SEC EDGAR fundamentals, FRED economic data |
| AI (optional) | Analysts and a bull/bear/risk debate over an evidence pack built from real data; numeric claims are checked against it |
| MCP | Our tools for AI assistants under a host-enforced permission matrix; external MCP servers as untrusted tools |
| Automation | Trigger → steps workflows with dry runs, limits and a global kill switch |
| Devices | Responsive UI for desktop, tablet and phone; pairing codes for devices on your network; PWA manifest and app-shell service worker (browsers allow installing only on secure origins — `localhost` or HTTPS with your own certificate — not on plain `http://` LAN addresses) |

## Documentation

- [START_HERE.md](START_HERE.md) — five-minute orientation
- Guides: [user](docs/guides/USER_GUIDE.md), [installation](docs/guides/INSTALLATION_GUIDE.md),
  [security](docs/guides/SECURITY_GUIDE.md), [troubleshooting](docs/guides/TROUBLESHOOTING_GUIDE.md),
  [bots](docs/guides/BOT_GUIDE.md), [AI](docs/guides/AI_GUIDE.md), [data](docs/guides/DATA_GUIDE.md),
  [MCP](docs/guides/MCP_GUIDE.md), [plugins](docs/guides/PLUGIN_GUIDE.md)
- Reference: [OMS Script](docs/reference/SCRIPT_LANGUAGE.md), [indicators](docs/reference/INDICATORS.md),
  [backtesting](docs/reference/BACKTESTING.md)
- Project: [feature matrix](docs/FEATURE_MATRIX.md), [development status](docs/DEVELOPMENT_STATUS.md),
  [known issues](docs/KNOWN_ISSUES.md), [data sources](docs/DATA_SOURCES.md), [cost audit](docs/COST_AUDIT.md),
  [threat model](docs/THREAT_MODEL.md), [privacy](docs/PRIVACY.md), [benchmarks](docs/PERFORMANCE_BENCHMARKS.md),
  [architecture decisions](docs/architecture/ARCHITECTURE_DECISIONS.md), [release notes](docs/RELEASE_NOTES.md)

## Testing

```bash
pip install -e '.[dev]'
pytest                      # unit + integration tests (network tests: OMOS_RUN_NETWORK=1)
scripts/e2e.sh              # browser end-to-end tests (needs the built UI and Node Playwright)
scripts/security_scan.sh    # bandit, pip-audit, npm audit, detect-secrets, license inventory
python scripts/benchmark.py # performance benchmarks
```

## License

Apache License 2.0 — see [LICENSE](LICENSE). Third-party components and their licenses are listed
in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Market data belongs to its providers and is
subject to their terms; you are responsible for using each source within its terms.

**Disclaimer:** this software is provided "as is", without warranty. Trading involves substantial
risk of loss. Nothing here is investment advice, and no security property is guaranteed.
