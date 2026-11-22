# Development status

**Release:** 0.1.0 · **Updated:** 2026-11-22 · **Branch:** `ccr-80d6e707-jb6y48`

## Where things stand

All thirteen phases of the build plan have been carried out. The application builds, installs and
runs on Linux x86_64, and its core workflows are covered by automated tests that were run for this
release. It is an early release: paper trading only, Linux-only verified builds, and the gaps listed
in [KNOWN_ISSUES.md](KNOWN_ISSUES.md) and [FEATURE_MATRIX.md](FEATURE_MATRIX.md).

| Phase | Result |
|---|---|
| 1 Research | [Repository audit](research/REPOSITORY_AUDIT.md), [architecture decisions + risk register](architecture/ARCHITECTURE_DECISIONS.md), [data sources](DATA_SOURCES.md), [cost audit](COST_AUDIT.md) |
| 2 Foundation | Python package, config, SQLite (WAL, migrations, backups, corruption recovery), logging with redaction |
| 3 / 5 Core UI and charts | Preact web UI (14 pages), KLineChart adapter, layouts, drawings, PWA shell |
| 4 Market data | 7 free providers + CSV import + labelled simulator, caching, validation, failover between endpoints of the same venue |
| 6 / 7 Indicators, screener, strategy lab | 34 indicators, OMS Script, backtester with robustness tools, screener |
| 8 / 9 Trading and bots | Paper broker, gateway + risk engine, portfolio, rule/DCA/grid bots, recovery, alerts |
| 10 / 11 AI, MCP, plugins, automation | Optional AI analysts, MCP server and client, plugin system, workflows |
| 12 Security and QA | Scans, E2E suite, benchmarks; 17 defects found and fixed (below) |
| 13 Release | Wheel + Linux single-file binary built and verified; documentation complete |

## Test results for this release (2026-11-22)

| Suite | Command | Result |
|---|---|---|
| Unit + integration, with live network and local AI | `OMOS_RUN_NETWORK=1 pytest` (Ollama running) | **291 passed**, 0 skipped |
| Unit + integration, offline | `pytest` | 278 passed, 13 skipped (network) |
| Coverage | `coverage run --branch --source=src/omos -m pytest` | 78% line+branch of 8,543 statements |
| Browser end-to-end | `scripts/e2e.sh` | **12/12 passed** (repeated runs after fixes) |
| Browser E2E incl. phone pairing over the LAN | `OMOS_E2E_LAN=1 scripts/e2e.sh` | **13/13 passed** — see note below |
| Security scan | `scripts/security_scan.sh` | Passed: 0 HIGH bandit findings, 0 vulnerable Python (92) or shipped npm packages, 0 secrets |
| Release artifact | `scripts/build_release.sh && scripts/verify_release.sh` | Passed |
| Benchmarks | `python scripts/benchmark.py` | [PERFORMANCE_BENCHMARKS.md](PERFORMANCE_BENCHMARKS.md) |
| Web build + type check | `scripts/build_web.sh` | Passed (`tsc --noEmit` clean) |

**LAN note:** in this sandbox, Chromium's WebSocket to the machine's own LAN address is answered
with HTTP 502 by the network layer, although plain HTTP works. A raw WebSocket client sending the
same Origin, compression and token handshake to that address gets `101 Switching Protocols` and
the server's `hello`, so the app side is verified; live updates on a real phone over Wi-Fi were not
observed here.

## Bugs found and fixed during release testing

1. **Data loss:** every chart data reload and unmount deleted the user's saved drawings on the host.
2. Changing symbol or timeframe dropped indicators from the saved chart workspace.
3. Cache hits dropped the still-forming bar; a watchlist "last price" could be yesterday's close
   while labelled REALTIME.
4. Shutdown cleanup never ran on SIGTERM (uvicorn re-raises the signal).
5. MCP client read SDK-v1 field names: external tool errors were reported as successes.
6. The built-in EMA template could not be optimised (625 combinations over the 400 cap).
7. A paused bot could not be reconfigured despite the UI saying "pause first".
8. A too-short `OMOS_VAULT_PASSPHRASE` crashed startup.
9. A hostile RSS feed (XXE/entity tricks) crashed the news refresh instead of failing alone.
10. Ticker symbols were not URL-encoded in the Yahoo RSS query.
11. SEC comparatives could mislabel a fiscal year.
12. Sign-in links opened in an already-open tab did nothing.
13. `omos open/status/mcp` could not find a host started with `--port`.
14. Process plugins and `omos service` used `sys.executable`, wrong in the single-file build.
15. **Remote access did not work:** with `--allow-remote`, phones addressing the PC by its LAN IP
    got HTTP 421 (the Host allow-list held only loopback names). The machine's own addresses and
    hostname are now accepted; COOP is no longer sent on plain-HTTP LAN origins.
16. **Release build:** setuptools reused a stale `build/` directory, so a wheel could contain an
    old UI bundle next to the current one. The build now starts clean and fails if the wheel holds
    any UI asset that `index.html` does not reference.
17. Preact 10.27.2 (JSON VNode injection advisory) and Vite 7.1.12 (dev-server advisories) upgraded;
    CSP no longer allows WebSockets to any host; KLineChart's NOTICE now ships with the UI.

Fixes 1–15 and the CSP change each have a regression test (unit, integration or E2E) that fails on
the old code. The dependency upgrades are checked by the security scan, and the NOTICE by
`scripts/verify_release.sh`; fix 16 by a guard in `scripts/build_release.sh`.

## How to resume work

```bash
cd open-market-os
python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
scripts/build_web.sh
pytest                                  # expect 278 passed, 13 skipped offline
scripts/e2e.sh                          # needs Node + Playwright + Chromium
scripts/security_scan.sh
```

## Next tasks, in priority order

1. **Windows and macOS:** run `scripts/build_release.sh` and `scripts/verify_release.sh` on each OS
   (PyInstaller cannot cross-compile), fix what breaks, record results.
2. **A first live broker adapter** behind the existing gateway (e.g. an exchange spot API with
   testnet support), gated by the `trade:live` scope, bot approval and the live switch; test
   against the venue's testnet before anything else.
3. Exchange holiday calendars (B1 in known issues) and session-anchored VWAP (B2).
4. Chart features with the most user value: compare/overlay symbols, keyboard shortcuts, chart
   templates UI, volume profile and structure zones drawn on the chart.
5. Multi-symbol (portfolio) backtests.
6. Raise coverage of `cli.py`, `host.py` and `mcp/server.py`; add a watchdog test.
7. Optional hosted CI running the same scripts.
