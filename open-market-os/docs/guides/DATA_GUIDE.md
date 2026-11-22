# Data guide

## Instrument ids

Every instrument is named `PROVIDER:SYMBOL`, for example `OKX:BTC-USDT`, `COINBASE:BTC-USD`,
`KRAKEN:XBTUSD`, `BINANCE:BTCUSDT`, `YAHOO:AAPL`, `YAHOO:RELIANCE.NS`, `YAHOO:^NSEI`, `YAHOO:GC=F`,
`YAHOO:EURUSD=X`, `FRED:CPIAUCSL`, `ECB:EURUSD`, `CSV:<your name>`, `SIM:DEMO-TREND`.
One id is never silently replaced by another: if OKX is down, an `OKX:` chart shows cached OKX data
labelled `STALE`, never Binance prices. Failover happens only between endpoints of the same venue
(e.g. Binance's main API and its public mirror).

## Providers (Settings → Data sources)

| Provider | Markets | Label | History | Notes |
|---|---|---|---|---|
| OKX | Crypto spot | REALTIME | Paged deep history | Self-limited to 8 req/s |
| Coinbase Exchange | Crypto spot (USD/USDC/EUR/GBP) | REALTIME | 300 bars per request, paged | 6 req/s |
| Kraken | Crypto spot, some FX | REALTIME | **Last 720 bars only** | 1 req/s |
| Binance | Crypto spot | REALTIME | Full, 1000 per request | Geo-restricted in some regions (mirror used) |
| Yahoo Finance | Stocks, ETFs, indices, FX, futures, funds | DELAYED | 1m: 7 days · up to 30m: 60 days · 1h: ~730 days · daily+: full | **Unofficial**; personal, non-commercial use under Yahoo's terms; 2 req/s |
| FRED | US & international economic series | HISTORICAL | Full | No key for values; free key for release schedule |
| ECB | Euro reference FX rates | HISTORICAL | Since 1999 | Once per business day, informational |
| CSV import | Anything you have | HISTORICAL | What you import | You are responsible for its licence |
| Simulator (`--demo`) | `SIM:DEMO-*` | SIMULATED | Unlimited | **Not real prices**; for learning and tests |

Full terms and rate limits: [DATA_SOURCES.md](../DATA_SOURCES.md).

## Live versus delayed

| Label | Meaning |
|---|---|
| `REALTIME` | Polled directly from the venue. Freshness = polling interval (5–30 s depending on your computer; Settings → General) + network time. This is polling, not a streaming feed |
| `DELAYED` | The source delays it (stock exchanges via Yahoo, often 15–20 min) or is unofficial |
| `HISTORICAL` | End-of-day or archival data (FRED, ECB, CSV) |
| `SIMULATED` | Generated; not market prices |
| `STALE` | The provider failed; you see the last cached bars |

## Rate limits and caching

Each provider has a local rate limiter below the venue's published limit, with retries and back-off.
Closed bars are cached in your database; later requests fetch only what is new. The first load of a
long history can take several seconds (≈9 s for 5,000 hourly OKX bars), later loads well under a
second.

## Historical data and timeframes

Standard timeframes: 1s–30s (only where a venue serves seconds), 1m, 3m, 5m, 15m, 30m, 45m, 1h, 2h,
3h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M. Timeframes a provider does not serve are built from a smaller
native one (e.g. 45m from 15m); a partly filled last bucket is flagged.

## Missing data and quality

Each response carries a quality report: duplicates removed, invalid bars dropped (e.g. high < low,
non-positive prices), future-dated bars dropped, and gaps. Gap detection knows trading sessions
(weekends, overnight), but **not exchange holidays** — a holiday shows as a gap. Gaps are reported,
never filled with invented bars. Backtests on data with missing bars say so in their warnings.

## Importing CSV

**Settings → Data sources → Import CSV**: a header row with `time` (or `date`, `datetime`,
`timestamp`), `open`, `high`, `low`, `close` and optional `volume`. Times can be ISO dates/datetimes
or Unix seconds/milliseconds (UTC). Up to 50 MB per file. The data appears as `CSV:<name>`.

## Market hours

Session calendars: 24x7 (crypto), FX 24x5, NYSE/NASDAQ, NSE India, London, Tokyo, Hong Kong, Xetra,
and daily publication. Holidays, half-days and lunch breaks are not modelled.

## Economic and company data

- **FRED:** 10 US indicators with latest and previous values (CPI, unemployment, payrolls, Fed funds,
  GDP, PCE, jobless claims, retail sales, sentiment, housing starts). Consensus forecasts are not
  available from any free licensed source and are shown as "not available".
- **SEC EDGAR:** annual and quarterly figures exactly as filed by US companies, plus derived ratios.
  Requires your contact e-mail (SEC policy) in Settings → Data sources.
- **News:** RSS headlines from Yahoo (per symbol), the Federal Reserve, the SEC and feeds you add
  (HTTPS only). Sentiment is a transparent word count, not AI.
