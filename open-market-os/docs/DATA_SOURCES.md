# Data sources — terms, limits and status

Checked live on 2026-11-22 with `OMOS_RUN_NETWORK=1 pytest tests/integration/test_live_providers.py`
(12/12 passed) plus live news and calendar runs. "Self-limit" is the rate Open Market OS uses, below
each venue's published limit. Using each source within its terms is the user's responsibility; the
notes below are a summary, not legal advice — read each provider's own terms.

| Provider | Id prefix | Markets | Label | Key | Venue limit / our self-limit | History | Terms summary |
|---|---|---|---|---|---|---|---|
| OKX | `OKX:` | Crypto spot (swap symbols listed only) | REALTIME | No | 40 req / 2 s per IP for candles / 8 per s | Recent candles + paged history (100 per call) | Public market-data endpoints; OKX API terms and regional availability apply |
| Coinbase Exchange | `COINBASE:` | Crypto spot (USD, USDC, EUR, GBP pairs) | REALTIME | No | ~10 req/s / 6 per s | 300 candles per request, paged | Coinbase API terms |
| Kraken | `KRAKEN:` | Crypto spot, some fiat FX | REALTIME | No | ~1 public call/s / 1 per s | **Most recent 720 bars only** | Kraken terms |
| Binance | `BINANCE:` | Crypto spot | REALTIME | No | 6,000 weight/min / 8 per s | Full history, 1,000 per request | `api.binance.com` is geo-restricted in some regions (HTTP 451); Binance's public mirror `data-api.binance.vision` is used |
| Yahoo Finance | `YAHOO:` | Global equities, ETFs, indices, FX, futures, funds, crypto | **DELAYED** | No | Undocumented / 2 per s | 1m: 7 days · 2m–30m: 60 days · 1h: 730 days · daily+: full | **Unofficial** endpoint with no SLA; may change or be blocked. Yahoo's terms restrict use to personal, non-commercial purposes. Exchange delays apply |
| FRED (St. Louis Fed) | `FRED:` | US & international economic series, rates | HISTORICAL | No (free key for release schedule) | Undocumented / 2 per s | Full | Mostly public data; some series are copyrighted by their original source — check series notes |
| ECB | `ECB:` | Euro reference FX rates | HISTORICAL | No | Static file | Since 1999 | Published once per TARGET business day ~16:00 CET; information only, not for trading; reuse with attribution |
| SEC EDGAR | (fundamentals) | US SEC filers' XBRL facts | as filed | No (contact e-mail required) | ≤10 req/s / 5 per s | All filings | Fair-access policy requires a User-Agent with your contact |
| RSS feeds | (news) | Yahoo per-symbol headlines (unofficial), Federal Reserve, SEC, your feeds | — | No | — | — | Each publisher's terms; headlines and links only are stored |
| CSV import | `CSV:` | Anything you import | HISTORICAL | — | — | Your file | You are responsible for its licence |
| Simulator | `SIM:` | `DEMO-TREND`, `DEMO-RANGE`, `DEMO-VOLATILE` | SIMULATED | — | — | Unlimited | Generated; **not market prices**; only with `--demo` |

## Freshness, precisely

- `REALTIME` here means *polled* from the venue's public REST API every 5–30 s (device tier), not a
  streaming tick feed. No WebSocket market feeds are used in this release.
- Charts receive updated candles over the app's own WebSocket after each poll.
- Only closed bars are cached; the forming bar is re-fetched (or remembered for up to the request's
  freshness window) so a cached answer never silently drops the current bar.

## What is not available for free here

Real-time stock quotes, Level 2/order-book depth, tick data and volume delta, consensus economic
forecasts, whole-market stock listings for screening, survivorship-free historical universes, and
non-US company fundamentals.
