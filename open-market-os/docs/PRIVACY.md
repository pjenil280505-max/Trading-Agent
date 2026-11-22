# Privacy

Open Market OS has **no accounts, no telemetry, no analytics and no cloud service.** Everything is
stored on your computer in your data folder.

## What is stored locally

Your watchlists, chart layouts and drawings, strategies, backtests, paper accounts, orders, journal,
alerts, notifications, AI runs, settings, cached market data and an audit log of changes. Secrets are
in the OS keychain or the encrypted vault. Paired devices keep only their own sign-in token.

## What leaves your computer, and to whom

| Destination | What is sent | When |
|---|---|---|
| Market-data providers (OKX, Coinbase, Kraken, Binance, Yahoo, FRED, ECB) | Instrument symbols and time ranges you request; your IP address | When you view, scan, alert or trade instruments |
| RSS feeds (Yahoo, Federal Reserve, SEC, your feeds) | Symbols of the news you refresh | When you refresh news |
| SEC EDGAR | Ticker, and the contact e-mail you entered (SEC policy) | When you open fundamentals |
| Hosted AI providers you enabled | The evidence pack (market data, indicators, headlines, filings) and your question | When you run an analysis |
| External MCP servers you added | The arguments of tools you call | When you call them |

Not sent anywhere: your positions, orders, journal, strategies' results, API keys, or any usage data.
The app makes no requests to its authors.

## Your control

- Disable any data provider in Settings → Data sources.
- Use local AI only, or no AI.
- Delete everything with `omos reset --confirm "DELETE ALL LOCAL DATA"`.
- Exports and backups are files you control; they are not encrypted, so keep them private.
