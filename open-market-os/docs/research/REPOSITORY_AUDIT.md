# Repository Audit

Audit date: 2026-11-21. Method, applied to every row below:

1. Resolved the repository through the session's GitHub access check (`add_repo`). That proves it exists at that exact `owner/name`.
2. Made a shallow, blob-less clone and read the license file(s) directly from the tree.
3. Read the date and subject of the latest commit.
4. Counted release tags with `git ls-remote --tags`.

**What this audit did not do**, unless a row says otherwise:
- read issue trackers;
- run those projects' test suites;
- review their source for security problems.

Stars and download counts were not collected; they are not evidence of quality.

## Verdict key

| Verdict | Meaning |
|---|---|
| **USED** | Shipped as a dependency of Open Market OS |
| **TEST-ONLY** | Used only by our test suite, never shipped |
| **OPTIONAL** | External program the user may install separately; never bundled |
| **REFERENCE** | Studied for design ideas only; no code copied |
| **REJECTED** | Not used, for the stated reason |

## Charting

| Repository | License (from tree) | Last commit | Tags | Verdict | Notes |
|---|---|---|---|---|---|
| [klinecharts/KLineChart](https://github.com/klinecharts/KLineChart) | Apache-2.0 + NOTICE | 2026-11-17 (release v10.1.1) | 109 | **USED** | Canvas engine with zero runtime dependencies. Built-in drawing overlays store points as `{timestamp, value}`, so drawings stay anchored across timeframe and zoom changes. Has a data-loader API (`getBars`/`subscribeBar`), multiple panes and custom indicators. Its NOTICE credits TradingView Lightweight Charts, which we reproduce. Wrapped behind our own `ChartAdapter`, so it can be replaced. |
| [tradingview/lightweight-charts](https://github.com/tradingview/lightweight-charts) | Apache-2.0 + NOTICE | 2026-11-18 | 93 | REJECTED (kept as fallback) | Excellent performance, but no built-in drawing tools; drawings would have to be written as plugin primitives. Its NOTICE asks for attribution to TradingView, which this project does not use as branding. |
| [apache/echarts](https://github.com/apache/echarts) | Apache-2.0 | 2026-10-04 | 157 | REJECTED | General-purpose charting. Heavier, and less suited to interactive trading drawings. |
| [tradex-app/TradeX-chart](https://github.com/tradex-app/TradeX-chart) | GPL-3.0 | 2026-07-21 | 0 | REJECTED | GPL-3.0 would force the whole distribution to GPL. No tagged releases. |
| [longsurf-ai/openchart](https://github.com/longsurf-ai/openchart) | MIT | 2026-11-10 (v0.1.25) | 13 | REFERENCE | Search result for "OpenChart". Early (0.1.x). Bun/TypeScript stack. |
| [dylanpersonguy/OpenCharts](https://github.com/dylanpersonguy/OpenCharts) | MIT | 2026-11-10 | 0 | REFERENCE | Search result for "OpenCharts". Needs Next.js, Express, PostgreSQL and Redis, which is too heavy for a zero-cost local-first host. No tagged releases. |
| [ErTasselli/OpenTerminal](https://github.com/ErTasselli/OpenTerminal) | MIT | 2026-10-06 | 2 | REFERENCE | Closest match to "Opentern" (the exact name was not found). A Bloomberg-style dashboard on free data. |
| "Kandle" | — | — | — | NOT FOUND | No repository by this name was found. |

## Trading platforms and bots

| Repository | License | Last commit | Tags | Verdict | Notes |
|---|---|---|---|---|---|
| [marketcalls/openalgo](https://github.com/marketcalls/openalgo) | AGPL-3.0 (`License.md`) | 2026-11-20 | 76 | REFERENCE | Indian broker bridge. AGPL network copyleft rules out embedding. Users who want Indian broker access can run OpenAlgo separately and connect through its own API (documented as an optional integration). |
| [brokermr810/QuantDinger](https://github.com/brokermr810/QuantDinger) | Apache-2.0 (backend repo) | 2026-11-22 | 106 | REFERENCE | Its documentation says the frontend is under a separate non-commercial source-available license, so none of its UI is used. Ships a default password (`quantdinger/123456`), which this project explicitly avoids. |
| [deonmenezes/opentradex](https://github.com/deonmenezes/opentradex) | MIT | 2026-07-07 | 1 | REFERENCE | Paper-by-default cockpit focused on Kalshi and Polymarket. |
| [antonygiomarxdev/greedy](https://github.com/antonygiomarxdev/greedy) | MIT | 2026-05-25 | 47 | REFERENCE | Go, single-binary trading engine with MCP. Its "local-first + MCP" design matches our goals. |
| [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) | GPL-3.0 | 2026-11-19 | 124 | REFERENCE | Mature crypto bot. GPL-3.0 is incompatible with our Apache-2.0 distribution. Its systemd watchdog and dry-run-first design informed our daemon. |
| [hummingbot/hummingbot](https://github.com/hummingbot/hummingbot) | Apache-2.0 | 2026-11-18 | 113 | REFERENCE | Market-making framework. Large dependency tree; not embedded. |
| [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | LGPL-3.0 | 2026-11-21 | 219 (v2.0.0 RCs) | REJECTED | Rust/Python, high-performance and event-driven. Too heavy a toolchain and footprint for low-resource devices, and v2 is still in release candidates. |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | Apache-2.0 | 2026-11-20 | 4513 | REJECTED | Requires the .NET runtime. Too heavy for Android and low-end PCs. |

## Backtesting and indicators

| Repository | License | Last commit | Tags | Verdict | Notes |
|---|---|---|---|---|---|
| [kernc/backtesting.py](https://github.com/kernc/backtesting.py) | AGPL-3.0 | 2026-08-05 | 27 | REJECTED | AGPL is incompatible with permissive distribution. We wrote our own engine; no code was copied. |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | Apache-2.0 **+ Commons Clause** | 2026-11-11 | 10 | REJECTED | The Commons Clause forbids selling, so the license is not OSI-approved open source. |
| [TA-Lib/ta-lib-python](https://github.com/TA-Lib/ta-lib-python) | BSD-2-Clause | 2026-11-20 | 70 | REJECTED for core | Needs the TA-Lib C library, which is hard on Android and Windows. Could be added later as an optional accelerator. |
| [bukosabino/ta](https://github.com/bukosabino/ta) | MIT | 2026-03-18 | 65 | **TEST-ONLY** | Independent oracle our indicator tests compare against. Not shipped. |
| twopirllc/pandas-ta | — | — | — | NOT FOUND | `add_repo` reports the repository does not exist at this path any more. That is a maintenance risk; not used. |
| tulipchart/tulipindicators | — | — | — | NOT FOUND | Not found at this path. |

## Infrastructure

| Repository | License | Last commit | Tags | Verdict | Notes |
|---|---|---|---|---|---|
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | MIT | 2026-11-05 (v2.3.0) | 80 | **USED** | Official MCP SDK. Version 2 renamed `FastMCP` to `MCPServer`; our code targets v2. |
| [ccxt/ccxt](https://github.com/ccxt/ccxt) | MIT | 2026-11-20 | 719 | OPTIONAL (not bundled) | Very large package. Our four exchange data providers are small direct REST clients instead. ccxt is the documented route for future live broker adapters. |
| [ollama/ollama](https://github.com/ollama/ollama) | MIT | 2026-11-20 | 736 | OPTIONAL | Local model runtime, reached through its OpenAI-compatible endpoint. Installed separately by the user; never bundled. |

## Conclusions that shaped the architecture

1. **No permissive, embeddable backtester exists that fits.** The good ones are AGPL, GPL, under the Commons Clause, or tied to a heavy runtime. We therefore wrote `omos.backtest` from scratch and test it for look-ahead bias.
2. **KLineChart gives the best reuse-to-risk ratio for charts.** It has drawing overlays anchored to time and price, panes, custom indicators and an Apache-2.0 license. All chart access goes through `web/src/chart/adapter.ts`, so a swap touches one module.
3. **Copyleft projects are references only.** These are OpenAlgo, Freqtrade, backtesting.py, TradeX-chart and Nautilus. No code from them is in this repository.
4. **Two projects named in the brief were not found** under the expected paths (pandas-ta and tulipindicators), and "Kandle" does not exist. We depend on neither.
