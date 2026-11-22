# Known issues and limitations — 0.1.0

This list is meant to be complete. If you find something that is not here, it is a bug in this
list too. Bugs that were found *and fixed* during release testing are recorded in
[DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md) and the [CHANGELOG](../CHANGELOG.md).

## Bugs and incorrect behaviour (unresolved)

| # | Issue | Impact | Workaround |
|---|---|---|---|
| B1 | Exchange holidays and half-days are not modelled. On a holiday the "trading hours only" risk rule treats the market as open, and a paper market order fills at the last available price. | Paper results on holidays can be unrealistic | Avoid manual paper orders on holidays; bots only act on new closed bars, so they are not affected |
| B2 | VWAP resets at UTC midnight/week, not at each exchange's session open | VWAP differs from exchange-session VWAP for stocks | Use it mainly on 24x7 crypto, or compute session VWAP in OMS Script |
| B3 | ADX/DMI use TradingView-style smoothing seeds; values differ from TA-Lib for roughly the first 300 bars, then agree | Early-history ADX values differ between tools | Load enough history before relying on ADX |
| B4 | Price precision without a tick size is inferred from the data at 7 significant digits; prices above 1,000,000 or with ticks finer than that may show too few decimals on the chart axis | Cosmetic | None needed for most instruments |
| B5 | Heikin-Ashi charts do not update live (live bars would need re-deriving all HA values) | HA chart refreshes only on reload or timeframe change | Switch timeframe or reload |
| B6 | A drawing deleted on another device stays visible on an already-open chart until it reloads | Cosmetic | Reload the chart |
| B7 | A host killed with SIGKILL or a power cut leaves `host.runtime.json`; it is ignored safely (process and port are checked) but stays on disk | None | — |

## Technical debt

- Test coverage is 78% (line + branch). Weakest: `cli.py` 18% (exercised by the E2E and release
  scripts, which run out of process and are not counted), `research/calendar.py` 49%, `host.py` 55%,
  `mcp/server.py` 55%. The watchdog has no dedicated test.
- The web UI has no component-level unit tests; it is covered by 12 browser E2E tests.
- Views use loosely typed API responses (`any`) in TypeScript.
- No hosted CI: tests, E2E, security scan and release checks are scripts run locally.
- `optimize()` runs single-threaded.

## Security findings and residual risks

Audited on 2026-11-22 with bandit, pip-audit, npm audit, detect-secrets and manual review; see
[SECURITY_GUIDE](guides/SECURITY_GUIDE.md) and [THREAT_MODEL](THREAT_MODEL.md). Fixed in this release:
Preact JSON VNode injection advisory, Vite dev-server advisories, CSP allowing WebSockets to any host,
an assert guarding vault writes, missing KLineChart NOTICE. Remaining:

| # | Risk | Mitigation / advice |
|---|---|---|
| S1 | With `--allow-remote`, traffic on your LAN is plain HTTP unless you configure TLS; anyone on the same network could read tokens and data | Use only on a network you trust, or set `tls_certfile`/`tls_keyfile` in `host.toml`. Never port-forward to the internet |
| S2 | Device tokens are kept in the browser's `localStorage`; script injection in the UI would expose them | Strict CSP (`script-src 'self'`), no inline scripts, Preact escaping, tokens revocable per device |
| S3 | `OMOS_VAULT_PASSPHRASE` (headless/no-keyring setups) sits in an environment variable or service file readable by your OS user | Prefer the OS keychain; protect the account; see the security guide |
| S4 | Process plugins run as your user; their network access cannot be blocked | Install only plugins you trust; prefer declarative plugins |
| S5 | A stdio MCP server you add runs a program on your computer | Add only servers you trust; their tools start disabled |
| S6 | Hosted AI providers receive the evidence pack (prices, indicators, headlines; never secrets, accounts or positions) | Prefer local models; paid providers are off until you accept the cost warning |
| S7 | bandit MEDIUM findings remain: SQL built from constant fragments with bound parameters (4) and the opt-in `0.0.0.0` bind for remote access (3) | Reviewed; not exploitable as written |
| S8 | The single-file binary is not code-signed | Verify the SHA-256 in `SHA256SUMS` |

## Performance issues

- First load of a symbol fetches history from the venue: about 9 s for 5,000 one-hour OKX bars
  (venue paging and rate limits). The chart paints a 1,000-bar first chunk and shows a message.
- Grid optimisation of 400 combinations on 5,000 bars takes ~7 s on a 4-vCPU container.
- Market-structure indicators take ~260 ms on 50,000 bars (bar-by-bar confirmation logic).
- Phones, tablets and low-end PCs were not measured.

## Incomplete features (summary — details in the [feature matrix](FEATURE_MATRIX.md))

Live broker/exchange trading and the MetaTrader 5 bridge; baseline, Renko and point-and-figure charts;
compare/overlay symbols; keyboard shortcuts; ellipse, polygon, Fibonacci fan/time tools; drawing
groups and templates; chart templates UI; bar replay; multi-symbol backtests and bots; visual strategy
and workflow node editors; custom indicator colours; news and economic-event alerts and triggers;
valuation ratios and company comparison; AI memory, provider fallback and three AI roles; MCP
authentication to remote servers; dockable/multi-window desktop layout; GPU/thermal-aware tuning;
Android APK; hosted CI.

## Unsupported or untested platforms

| Platform | Status |
|---|---|
| Linux x86_64 | Built and tested (wheel and single-file binary) |
| Windows 10/11 | Not built or tested here. Source install should work (pure-Python dependencies have Windows wheels); unverified |
| macOS | Not built or tested here; unverified |
| Linux ARM (e.g. Raspberry Pi) | Not tested |
| Android / iOS as the host | Not supported; use them as remote screens on your LAN |
| Phones/tablets as clients | Layout tested with an emulated phone viewport only |

## External service limitations

| Source | Limitation |
|---|---|
| Yahoo Finance | Unofficial endpoint: may change or be blocked without notice. Yahoo's terms allow personal, non-commercial use only. Exchange delays apply (labelled DELAYED). Intraday history is short (1m: 7 days; 1h: ~730 days) |
| OKX, Coinbase, Kraken, Binance | Public REST polling, not streaming; "REALTIME" means freshness of the polling interval. Kraken serves only the last 720 bars; Binance's main API is geo-blocked in some regions (a public mirror is used); regional availability and terms vary |
| FRED | Some series are copyrighted by their original sources; the upcoming-release schedule needs a free API key |
| ECB | Reference rates once per business day; for information, not trading |
| SEC EDGAR | US SEC filers only; requires your contact e-mail; ≤10 requests/s |
| RSS feeds | Headlines only; quality and availability depend on each publisher |
| Hosted AI providers | Cost money per token (except free tiers); availability and terms are theirs |
| Ollama / local AI | Speed and quality depend on your hardware; small models make more mistakes |
