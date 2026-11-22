# Feature matrix — Open Market OS 0.1.0

Last updated 2026-11-22. Every item from the master build specification is listed, including the
ones that are not built.

**States**

| State | Meaning |
|---|---|
| VERIFIED | Implemented, automated tests exist and ran on 2026-11-22 with recorded results, limitations documented |
| TESTED | Implemented and covered by automated tests that ran, but with a stated gap (e.g. only mocked data) |
| IMPLEMENTED | Implemented and checked by hand or indirectly, without a dedicated automated test |
| PARTIAL | Some of the item works; what is missing is stated |
| NOT STARTED | Not built |
| NOT SUPPORTED | Deliberately not offered, with the reason |
| BLOCKED | Cannot be built or verified in the available environment |

**Evidence codes:** U = unit tests (`tests/unit`), I = API integration tests (`tests/integration`),
L = live-network tests (`OMOS_RUN_NETWORK=1`), E = browser end-to-end tests (`scripts/e2e.sh`),
R = release check (`scripts/verify_release.sh`), M = manual check this session.
Latest results: 290 pytest cases passed (incl. network and local AI), 12/12 E2E passed,
release check passed — see [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md).

## Platform and architecture

| Item | State | Evidence / notes |
|---|---|---|
| Local host process (API, background loops, watchdog) | VERIFIED | I, E, R |
| Browser UI served by the host (desktop) | VERIFIED | E (Chromium) |
| Responsive phone/tablet UI | TESTED | E with an emulated iPhone 13 viewport; no physical phone or tablet was available |
| PWA install | PARTIAL | Manifest + service worker; installable only on `localhost` or HTTPS origins |
| Linux x86_64 build and run | VERIFIED | Wheel install in a fresh venv (M), single-file binary (R) |
| Windows build/run | BLOCKED | No Windows machine available; source install should work but is untested |
| macOS build/run | BLOCKED | No Mac available; untested |
| Android APK / Android host mode | NOT STARTED | Phones work as remote screens over the LAN instead |
| Configuration wizard | PARTIAL | Beginner/advanced modes, welcome hints and Settings pages; no step-by-step first-run wizard |

## 7. Chart engine (KLineChart 10 behind `web/src/chart/adapter.ts`)

| Item | State | Evidence / notes |
|---|---|---|
| Candlesticks | VERIFIED | E (real OKX data and SIM data) |
| OHLC bars, line, area, hollow candles | IMPLEMENTED | Chart-type selector; rendering by KLineChart; not E2E-asserted |
| Heikin-Ashi | TESTED | U (HA calculation); computed on the host; live updates disabled for HA by design |
| Baseline, Renko, point & figure | NOT STARTED | |
| Volume | IMPLEMENTED | `volume` indicator pane |
| Tick data | NOT SUPPORTED | No free tick-level source is integrated |
| Seconds timeframes | PARTIAL | Only where the venue serves them natively (Binance `1s`) |
| 1m … 1M timeframes, custom aggregations (e.g. 45m, 3h, 3d) | VERIFIED | U (aggregation, partial-bucket flag), L |
| Zoom, pan, scroll, crosshair, auto/manual scale | IMPLEMENTED | KLineChart built-ins |
| Log and percentage scales | IMPLEMENTED | Scale selector |
| Compare symbols / symbol overlays | NOT STARTED | |
| Multi-chart layouts 1/2/3/4/6/8 and custom grids up to 16 | VERIFIED | E (4-chart layout) |
| Crosshair / symbol / timeframe / range sync | IMPLEMENTED | Sync toggles; not E2E-asserted |
| Full-screen, PNG snapshot | IMPLEMENTED | |
| Data export from a chart | NOT STARTED | Exports exist for screener results and fills |
| Saved chart templates | PARTIAL | Stored via API (`/api/chart_templates`); no UI yet |
| Layout persistence (symbol, timeframe, indicators, drawings) | VERIFIED | E (survives reload); regression for a bug that dropped indicators |
| Keyboard shortcuts | NOT STARTED | Only `Esc` closes dialogs |
| Touch gestures | IMPLEMENTED | KLineChart touch support; untested on a real device |
| Drawing tools: trend line, ray, extended line, horizontal/vertical line, price label, parallel channel, price channel, regression channel (±2σ), Fibonacci retracement and extension, pitchfork, rectangle, circle, triangle, arrow, long/short position (risk/reward), measure, text note, brush | IMPLEMENTED | 21 tools; horizontal line is E2E-tested end to end |
| Ellipse, polygon, Fibonacci fan, Fibonacci time tools, drawing groups, drawing templates | NOT STARTED | |
| Drawing persistence with time/price anchors | VERIFIED | E: survives timeframe change, leaving the page and reload; regression for a data-loss bug fixed in this release |
| Magnet mode | PARTIAL | Off / strong only; no weak-magnet option in the UI |
| Lock/unlock all, hide/show all, object tree, delete | IMPLEMENTED | |

## 8. Indicators (host-calculated; one implementation for charts, screener, backtests)

| Item | State | Evidence / notes |
|---|---|---|
| SMA, EMA, WMA, VWMA, HMA, DEMA, TEMA, Supertrend, Ichimoku, ADX, Parabolic SAR | VERIFIED | U: values checked against TA-Lib or `ta`; Ichimoku without chikou (would look ahead); ADX seeding differs from TA-Lib in the first ~300 bars (documented) |
| RSI, Stochastic, Stochastic RSI, MACD, CCI, ROC, Momentum, Williams %R, MFI | VERIFIED | U |
| ATR, Bollinger Bands (+ width), Keltner, Donchian, historical volatility | VERIFIED | U |
| Volume, OBV, VWAP, Accumulation/Distribution | VERIFIED | U; VWAP resets at UTC day boundaries, not exchange session opens |
| Volume profile | PARTIAL | API `/api/indicators/volume_profile`; not drawn on charts |
| Volume delta | NOT SUPPORTED | Needs trade-level (bid/ask aggressor) data no free source provides here |
| Swing highs/lows, pivot points, BOS / CHOCH, fair-value gaps | VERIFIED | U, including no-repaint (pivots appear on their confirmation bar) |
| Support/resistance, liquidity zones, order blocks | PARTIAL | Computed by `/api/structure` with documented method; not drawn on charts |
| Trend detection | PARTIAL | Market-structure state and the analysis evidence pack; no dedicated trend indicator |
| No look-ahead for every indicator | VERIFIED | U: truncating future bars never changes past values (all 34) |
| Adjustable parameters, multiple instances, overlay or own pane | VERIFIED | E (RSI dialog), U |
| Custom colours | NOT STARTED | Colours are fixed per output |
| Visibility toggle per indicator | PARTIAL | Remove only |
| Indicator alerts | IMPLEMENTED | Via formula alerts (`rsi(close,14) < 30`) |
| Multi-timeframe inputs | PARTIAL | In OMS Script with `tf()`; built-in indicators use the chart's timeframe |
| Warm-up, NaN and missing-data handling | VERIFIED | U |

## 9. Scripting (OMS Script)

| Item | State | Evidence / notes |
|---|---|---|
| Strategy scripting (entries, exits, stop, target, trailing, size) | VERIFIED | U (script + backtester) |
| Custom indicator scripting | PARTIAL | Runs as an indicator *plugin* (example: EMA ribbon); no in-chart script editor |
| Parameters with ranges | VERIFIED | U |
| Multi-timeframe references `tf("4h", …)` (completed bars only) | VERIFIED | U |
| Versioning, export/import | TESTED | I; UI in the Strategy lab |
| Visual (no-code) strategy builder | NOT STARTED | The screener and workflow builders are form-based |
| Sandbox: no file, network, OS access; CPU/time, size, depth limits | VERIFIED | U: the language has no such functions; limits on source size, statements, nodes, nesting, tree depth and a 5 s timeout are tested |

## 10. Screener

| Item | State | Evidence / notes |
|---|---|---|
| Rules on price, change, volume, relative volume, RSI, MACD, ADX, ATR%, distance to MAs, 52-bar range… (18 fields) | VERIFIED | U, I |
| Custom formulas, AND/OR | VERIFIED | U |
| Save, duplicate, export CSV, presets | TESTED | U, I; UI |
| Scheduled scans with notifications for new matches | TESTED | I (added in this release) |
| Universe | PARTIAL | Scans the instruments of a watchlist (any provider). No market-wide universe: free sources do not offer full listings |
| Market cap / fundamental filters | NOT SUPPORTED | No free licensed bulk fundamentals source |
| Multi-timeframe scans | PARTIAL | One timeframe per screen; formulas can use `tf()` |
| Sortable results | PARTIAL | Sorted by change; no click-to-sort columns |

## 11. News, fundamentals, economic calendar

| Item | State | Evidence / notes |
|---|---|---|
| News from RSS (Yahoo per-symbol, Federal Reserve, SEC, user feeds) | VERIFIED | U (mocked) + live refresh on 2026-11-22 (64 headlines) |
| Search, symbol relevance, topics, de-duplication, source attribution | TESTED | U |
| Sentiment | TESTED | Transparent word-count heuristic, labelled as such — not AI and not a judgement |
| Hostile feeds (XXE / entity expansion) refused per feed | TESTED | U (bug fixed in this release) |
| News alerts, event correlation | NOT STARTED | |
| Company financials from SEC EDGAR (revenue, profit, EPS, assets, liabilities, equity, cash, debt, cash flow, capex, dividends) | TESTED | U with recorded-shape data. Not verified live here: SEC requires *your* contact e-mail, which only you can set |
| Ratios, growth, history (up to 12 periods) | TESTED | U |
| Valuation ratios (P/E etc.), company comparison | NOT STARTED | |
| Non-US companies' fundamentals | NOT SUPPORTED | SEC covers SEC filers only |
| Economic indicators with actual/previous/importance (10 US series from FRED) | VERIFIED | L, live run 2026-11-22 |
| Upcoming release schedule | IMPLEMENTED | Needs a free FRED API key; not verified live (no key here) |
| Consensus forecasts | NOT SUPPORTED | No free licensed source |
| Non-US calendars, event alerts | NOT STARTED | |

## 12. Portfolio and risk analytics

| Item | State | Evidence / notes |
|---|---|---|
| Multiple paper accounts, positions, cash, equity curve | VERIFIED | U, I, E |
| Realized / unrealized P&L, fees, exposure, concentration | VERIFIED | U |
| Drawdown, allocation, correlation, volatility | TESTED | I (analytics endpoint) |
| Journal with tags | IMPLEMENTED | API + UI |
| Fills CSV export | TESTED | U |
| Performance attribution | NOT STARTED | |

## 13. Strategy lab and backtesting

| Item | State | Evidence / notes |
|---|---|---|
| Backtests on real or imported data | VERIFIED | U, I, E |
| Next-bar-open fills, commission, slippage, spread, gap-aware stops, stop-first rule | VERIFIED | U |
| Position sizing (% equity, cash, quantity, % risk), pyramiding, partial exits, trailing stops, long/short | VERIFIED | U |
| Metrics (return, CAGR, Sharpe, Sortino, Calmar, drawdown, win rate, profit factor, expectancy, exposure, fees, buy & hold) | VERIFIED | U |
| Grid optimisation (capped at 400), walk-forward, out-of-sample, Monte Carlo | VERIFIED | U, E (walk-forward); default-grid bug fixed in this release |
| Multiple timeframes in one strategy | VERIFIED | `tf()` |
| Multi-symbol / portfolio backtests | NOT STARTED | One instrument per run |
| Margin simulation | PARTIAL | Leverage cap only |
| Look-ahead and repainting protection | VERIFIED | U |
| Survivorship-bias handling | NOT SUPPORTED | Free sources do not include delisted instruments |
| Bar replay | NOT STARTED | |
| Trade distribution chart | PARTIAL | Trade table and Monte Carlo percentiles; no histogram |

## 14. Bots and 24/7 execution

| Item | State | Evidence / notes |
|---|---|---|
| Rule bots running any OMS Script strategy (trend, breakout, mean reversion, multi-indicator…) | VERIFIED | U, I, E |
| DCA bots, grid bots | TESTED | U |
| Multi-timeframe logic | IMPLEMENTED | Through `tf()` in scripts |
| Multi-symbol bots, AI-driven bots | NOT STARTED | AI can only *propose* orders for a person to approve |
| Lifecycle: created → configured → validated → backtested → running ⇄ paused → stopped / error / recovering / paper-tested / approved / archived | VERIFIED | U (illegal transitions rejected), E |
| Start, pause, resume, stop, clone, edit, logs, state history, performance | VERIFIED | U, I, E |
| Closed-bar decisions; idempotent client order ids; no duplicate orders after restart | VERIFIED | U |
| Restart recovery | VERIFIED | U (no duplicate orders after restart) |
| Watchdog restarting dead loops, heartbeats | IMPLEMENTED | Loop status and restart counts in System status; the watchdog has no dedicated test |
| Graceful shutdown on SIGTERM | VERIFIED | I + M (bug fixed in this release: cleanup never ran) |
| Database corruption recovery from backup | VERIFIED | U |
| Provider outage → cached data labelled STALE, never fake data | VERIFIED | U, L |
| Start at login (systemd user unit / launchd / Task Scheduler) | PARTIAL | `omos service` writes the unit; Linux unit generation checked (M); macOS/Windows untested |
| Clock-drift monitoring, broker-disconnection handling | NOT STARTED | No live broker exists yet |
| Paper broker / local simulator | VERIFIED | U |
| Live broker or exchange trading adapters, MetaTrader 5 bridge | NOT STARTED | Live trading is impossible in this release by design until an adapter is verified |
| Android host mode (Doze, battery limits) | NOT SUPPORTED | Phones are remote controls; the host should be a PC |

## 15. Risk engine (every order passes it)

| Item | State | Evidence / notes |
|---|---|---|
| Risk per trade (stop-based), position risk, require-stop option | VERIFIED | U |
| Daily / weekly loss and drawdown circuit breakers; only a person can resume | VERIFIED | U, I |
| Total and per-instrument exposure, leverage, open positions, order quantity and value | VERIFIED | U |
| Orders per minute, trades per day, simultaneous bots | VERIFIED | U |
| Layer limit reduced to one in high volatility | VERIFIED | U |
| Price band for limit/stop prices, no-shorting default, instrument allow/block lists | VERIFIED | U |
| Emergency stop (pause all bots, cancel all orders, optional flatten) | VERIFIED | U, I, E |
| Risk event log | VERIFIED | U, UI |
| Margin-usage limit | NOT STARTED | No margin model beyond the leverage cap |

## 16. AI hub (optional, off by default)

| Item | State | Evidence / notes |
|---|---|---|
| Local models through Ollama (OpenAI-compatible) | VERIFIED | Integration test with `qwen2.5:0.5b`, 2026-11-22 |
| LM Studio, llama.cpp server presets | IMPLEMENTED | Same OpenAI-compatible client; not run here |
| Anthropic Claude, OpenAI, Gemini, Grok, DeepSeek, Mistral, Groq, OpenRouter presets | IMPLEMENTED | Not verified: no API keys in this environment (and they cost money) |
| Mock (rule-based, clearly labelled "not AI") | VERIFIED | U, I |
| Roles: market, bull, bear, quant, risk, news, fundamental, strategy developer, supervisor | TESTED | Mock and local model |
| Backtest-analyst, portfolio-analyst, execution-assistant roles | NOT STARTED | |
| Evidence pack built by code; numeric claims checked against it | VERIFIED | U (hallucinated numbers flagged) |
| Structured output separating observations, calculations, interpretation, predictions, assumptions, uncertainty, confidence | VERIFIED | U |
| Debate with supervisor consensus and agreement ratio | TESTED | Mock |
| Tool calling with permission levels (read / analyze / propose) | VERIFIED | U, including prompt-injection tests |
| Token and cost tracking, daily budget for hosted providers | VERIFIED | U |
| Agent memory, provider fallback / routing | NOT STARTED | |

## 17. MCP

| Item | State | Evidence / notes |
|---|---|---|
| MCP server (`omos mcp`, stdio) with 17 tools under a host-enforced permission matrix; no live-trading tool | VERIFIED | I, R (single-file binary driven by the MCP SDK client) |
| Private/state-changing tools off by default | VERIFIED | I, R |
| MCP client: add stdio/HTTP servers, discover tools/resources/prompts, per-tool enable, timeouts, size caps, audit log | VERIFIED | I with a real stdio server; result-field bug fixed in this release |
| Authentication to remote HTTP MCP servers | NOT STARTED | Only unauthenticated or localhost servers |

## 18. Plugins

| Item | State | Evidence / notes |
|---|---|---|
| Manifest validation, permissions, install, enable (explicit grant), disable, uninstall, SHA-256 pinning, symlink refusal | VERIFIED | U/I |
| Indicator plugins (OMS Script) | VERIFIED | I |
| REST data-provider plugins (allow-listed host) | VERIFIED | L |
| Process plugins (separate process, scrubbed environment, CPU/memory/time limits) | VERIFIED | I; network access cannot be blocked (documented) |
| Strategy plugins | IMPLEMENTED | Appear as templates |
| Chart-type, broker, AI-provider, news-source, widget plugins | NOT STARTED | |

## 19. Automation workflows

| Item | State | Evidence / notes |
|---|---|---|
| Triggers: schedule, price, formula, market open/close, app events (orders, risk, bots, notifications, proposals, screener matches) | TESTED | U, I |
| Steps: condition, risk check, notify, journal, paper order, screener, backtest, AI analysis, bot control, MCP tool | TESTED | U, I |
| Dry run, limits (runs/day, cooldown, retries), versioning, run history, global kill switch, paper-only orders | VERIFIED | U, I |
| News / economic-event triggers | NOT STARTED | |
| Visual node editor | NOT STARTED | A linear step builder is provided |

## 20. Alerts and notifications

| Item | State | Evidence / notes |
|---|---|---|
| Price above/below/cross, % move, formula conditions | VERIFIED | U, E |
| Once / once per bar / every; snooze, expiry, acknowledgement, history, test fire | VERIFIED | U, E |
| In-app notification toasts delivered live | VERIFIED | E (WebSocket delivery) |
| Sound, browser (OS) notifications from the UI | IMPLEMENTED | Browser notifications need permission, and on phones an HTTPS origin |
| Host desktop notifications | PARTIAL | Linux (`notify-send`) only |
| Bot, risk and screener events | IMPLEMENTED | Notifications; not user-configurable alert types |
| News and economic-event alerts | NOT STARTED | |

## 21. Devices

| Item | State | Evidence / notes |
|---|---|---|
| Pairing codes with permission presets, per-device tokens, revoke | VERIFIED | I, E |
| Bottom navigation, single-column charts on phones | TESTED | E (emulated phone) |
| Device profile (CPU, RAM, disk, battery) adapting chart history, polling, AI model hint | IMPLEMENTED | Shown in System status |
| GPU/VRAM/thermal-aware tuning | NOT STARTED | Only detects whether `nvidia-smi` exists |
| Dockable panels, drag-and-drop workspace, multiple windows | NOT STARTED | |
| Offline mode | PARTIAL | App shell cached; data needs the host |

## Security, testing, release

| Item | State | Evidence / notes |
|---|---|---|
| Loopback-only default, Host-header allow-list, CSP, token scopes, secret vault | VERIFIED | I, E |
| Security scans (bandit, pip-audit, npm audit, detect-secrets) | VERIFIED | `scripts/security_scan.sh`, passed 2026-11-22 |
| Browser E2E tests | VERIFIED | 12/12 |
| Benchmarks | VERIFIED | [PERFORMANCE_BENCHMARKS.md](PERFORMANCE_BENCHMARKS.md) |
| Hosted CI | NOT STARTED | Checks are scripts you run locally |
