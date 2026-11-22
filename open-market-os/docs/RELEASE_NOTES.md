# Release notes — Open Market OS 0.1.0 (2026-11-22)

First release. **Paper trading only. Verified builds: Linux x86_64 only.** Everything listed here was
exercised by automated tests in this release unless marked otherwise; the complete state of every
feature is in [FEATURE_MATRIX.md](FEATURE_MATRIX.md) and limitations in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

## Artifacts

Built by `scripts/build_release.sh` and checked by `scripts/verify_release.sh` on Linux x86_64.
They are build outputs, not committed to the repository; rebuild them from the tagged source.

| File | Size | SHA-256 |
|---|---|---|
| `open_market_os-0.1.0-py3-none-any.whl` | 339 KB | `510c7cd328aca446052d2a970dfac54129150e7b187298ebcdfa1d0e4ae23dc2` |
| `omos-0.1.0-linux-x86_64.tar.gz` (single-file app + licenses) | 52.6 MB | `0ed2d13de5b13e8f07daa153f1a3b3323363cc99f31ac0b08a12ab751cd6c978` |

PyInstaller output is not byte-for-byte reproducible, so a rebuild has a different hash; verify your
own build with `scripts/verify_release.sh`.

**Not built:** Windows installer, macOS package, Android APK (no such build environment was
available; the source install is expected to work on Windows/macOS but is untested).

## What is included

- **Host:** local HTTP API + WebSocket, background loops with watchdog, SQLite with migrations,
  daily backups and corruption recovery, `omos` command-line tool (start, open, status, doctor,
  backup, restore, export, token, config, service, mcp, reset).
- **Market data:** OKX, Coinbase, Kraken, Binance (REALTIME, polled), Yahoo Finance (DELAYED,
  unofficial), FRED and ECB (HISTORICAL), CSV import, labelled simulator; caching, validation,
  session-aware gap detection, honest status labels.
- **Web UI (14 pages):** dashboard, charts (layouts up to 16 cells, sync, 21 drawing tools,
  Heikin-Ashi, log/% scales, snapshots), watchlists, screener (scheduled scans), news/fundamentals/
  economic calendar, strategy lab, bots, portfolio, risk, alerts, AI workspace, automation,
  settings, system status; phone layout; device pairing.
- **Analysis:** 34 indicators validated against reference libraries with no-look-ahead tests;
  OMS Script; backtester with costs, gap-aware stops, optimisation, walk-forward, out-of-sample and
  Monte Carlo.
- **Trading:** paper broker, single execution gateway, risk engine (per-trade risk, exposure,
  leverage, rate limits, circuit breakers, emergency stop), rule/DCA/grid bots with restart recovery,
  alerts and notifications.
- **AI (optional):** local (Ollama, LM Studio, llama.cpp) or hosted providers; evidence-based
  analysts, debate, tool use with permission levels; cost tracking and daily budget.
- **MCP:** `omos mcp` server with a host-enforced 17-tool permission matrix; client for external
  MCP servers.
- **Plugins and automation:** declarative and isolated process plugins; trigger/step workflows with
  dry runs, limits and a kill switch.

## Verification for this release

| Check | Result |
|---|---|
| `OMOS_RUN_NETWORK=1 pytest` (live providers, local AI via Ollama) | 291 passed |
| `scripts/e2e.sh` / with `OMOS_E2E_LAN=1` | 12/12 / 13/13 passed |
| `scripts/security_scan.sh` | passed — 0 HIGH static findings, 0 known-vulnerable packages, 0 secrets |
| `scripts/verify_release.sh` | passed |
| Wheel installed into a fresh environment, started, served the UI, stopped cleanly | passed |
| Coverage | 78% line+branch |

## Not included

Live broker/exchange trading, MetaTrader 5, Windows/macOS/Android builds, Renko/point-and-figure/
baseline charts, bar replay, multi-symbol backtests, visual node editors, hosted CI. See the
feature matrix for the full list.
