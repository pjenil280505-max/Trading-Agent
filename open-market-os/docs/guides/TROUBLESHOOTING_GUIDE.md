# Troubleshooting

First steps for anything: **System status** in the app, `omos status` and `omos doctor` in a
terminal, and the log file `logs/omos.log` in your data folder ([where is it?](INSTALLATION_GUIDE.md#3-where-your-data-lives)).

## Starting and signing in

| You see | Cause | Fix |
|---|---|---|
| `Port 8765 is already in use — is Open Market OS already running? Try 'omos open'.` | The host is already running, or another program uses the port | `omos open` for a sign-in link; or `omos start --port 8800` |
| `that code is invalid, expired or already used` | Sign-in links work once and expire after 10 minutes; pairing codes after 5 | `omos open` (computer) or create a new pairing code (phone) |
| `too many failed attempts; wait 10 minutes and try again` | Several wrong codes | Wait, then use a fresh code |
| `this sign-in has expired or was revoked; pair the device again` | Token expired (30/90 days) or revoked | Sign in again |
| Page says `The web UI has not been built. Run scripts/build_web.sh.` | Source install without the UI build | `scripts/build_web.sh` (needs Node.js 20+) |
| `The host is not running. Start it with 'omos start'.` | `omos open` without a running host | Start it |

## Phones and other devices

| You see | Cause | Fix |
|---|---|---|
| Page does not load at all | Host only listens on `127.0.0.1` (default), firewall, or wrong address | Start with `--allow-remote`; allow the port in the PC firewall; use the PC's LAN IP |
| `remote access is disabled on this host` (403) | Remote access is off | `omos start --allow-remote` |
| `request Host header is not allowed` (421) | You used a name the host does not know (e.g. a custom DNS name) | Use the PC's IP or hostname, or add the name: `omos config --set extra_allowed_hosts=myname.lan` |
| `too many requests` (429) | A remote device exceeded 20 requests/s | Wait a moment; close extra tabs |
| Notifications or "Install app" missing on the phone | Browsers allow these only on HTTPS origins | Configure TLS (installation guide §5) or use the desktop |
| `this device/token lacks the 'admin' permission` | Device paired with a limited preset | Change settings from the host computer, or pair with `full` |

## Market data and charts

| You see | Cause | Fix |
|---|---|---|
| Chart stays empty for several seconds, "first load … can take several seconds" | First load of a symbol fetches history from the venue (≈9 s for 5,000 hourly bars on OKX) | Wait; later loads come from the cache |
| `STALE` badge, "Provider unavailable — cached bars only" | The provider failed; you see cached data, not live prices | Wait for the provider; check System status → Data providers |
| `DELAYED` badge on stocks | Yahoo data is delayed by the exchange (often 15–20 min) | Expected; there is no free real-time stock feed here |
| Binance errors / HTTP 451 | Binance blocks some regions; the public mirror is used automatically | Use OKX, Coinbase or Kraken instead, or disable Binance |
| `Yahoo: no data for …` or Yahoo errors | Wrong symbol, or the unofficial endpoint changed/blocked | Check the symbol on finance.yahoo.com (e.g. `RELIANCE.NS`, `^NSEI`, `GC=F`, `EURUSD=X`); retry later |
| `OKX symbols look like BTC-USDT` (similar for Coinbase/Binance) | Symbol format for that venue | Coinbase `BTC-USD`, Binance `BTCUSDT`, Kraken `XBTUSD` |
| Kraken shows only recent history | Kraken's public API returns only the last 720 bars | Use another venue for long history |
| Intraday Yahoo history is short | Yahoo keeps 1m data for 7 days, 1h for ~2 years | Use daily bars for long history |
| Gaps reported in data quality | Real exchange gaps or a provider outage; holidays are not modelled | Expected for closed sessions; refetch later for outages |
| Drawing disappeared | Drawings are stored per symbol; you may be on another provider's symbol | Load the same instrument id (e.g. `OKX:BTC-USDT`, not `BINANCE:BTCUSDT`) |

## Research

| You see | Cause | Fix |
|---|---|---|
| `SEC EDGAR requires a contact e-mail …` | The SEC requires a contact in requests | Settings → Data sources → SEC contact e-mail |
| `… is not in the SEC company list` | Fundamentals cover US SEC filers only | — |
| Calendar says "Add a free FRED API key …" | The upcoming-release schedule needs a key; values work without | Get a free key at fred.stlouisfed.org, store it in Settings → Data sources |
| A news feed listed under "failed" | Feed down, not RSS/Atom, or uses forbidden XML features (refused for safety) | Remove or replace the feed |

## Trading, bots and risk

| You see | Cause | Fix |
|---|---|---|
| Order `rejected` with a reason | A risk rule (exposure, risk per trade, price band, rate limit, no shorting…) | Read the reason and the Risk checks list; adjust the order or the limits in Risk |
| `emergency stop is active (…)` | The emergency stop was pressed | Risk → Clear emergency stop (type `RESUME TRADING`) |
| `circuit breaker tripped: daily loss …` | A loss limit was hit; trading is halted for that account | Review, then Risk → Resume trading |
| Bot is running but never trades | It acts only on new closed bars; the rules may not be true; orders may be rejected | Check the bot's Logs and Risk → decisions log; test the rules in the Strategy lab |
| Bot went to `error` | 5 failed ticks in a row (e.g. ~5 minutes without fresh data) | Fix the cause shown in Logs, then Start |
| Bot `paused` by itself | A risk halt or the emergency stop paused it | Clear the halt, then Resume the bot |
| `no market price available` | No quote for the instrument right now | Check the data provider |
| Live trading switched on but nothing changes | No live broker adapter exists in 0.1.0 | Expected |

## AI and MCP

| You see | Cause | Fix |
|---|---|---|
| `cannot reach AI provider 'ollama' at http://127.0.0.1:11434/v1` | Ollama is not running | Start Ollama; `ollama pull <model>` |
| `AI provider '…' is disabled (Settings > AI)` | Providers start disabled | Enable it (hosted ones ask for a cost acknowledgement) |
| `today's AI budget of $… is used up` | Daily budget reached (hosted providers) | Raise it in Settings → AI, or use a local model |
| `the AI provider rejected the API key` | Wrong or revoked key | Re-add the provider with a valid key |
| "AI output could not be parsed" | Small models often break the required JSON | Use a larger model |
| MCP assistant shows no Open Market OS tools | Host not running, no bridge token, or tools disabled | Start the host; Settings → MCP → Create bridge token; tick the tools |
| External MCP tool call refused | The tool is disabled until you enable it | Settings → MCP → tick the tool |

## Plugins

| You see | Cause | Fix |
|---|---|---|
| `grant every requested permission to enable it: …` | Enabling needs all requested permissions | Enable again and accept, or do not use the plugin |
| `plugin files changed since installation; reinstall it` | Files differ from the installed fingerprint | Reinstall from a source you trust |
| `Python process plugins need Python 3 installed …` | The single-file app has no plugin interpreter | Install Python 3 |
| `plugin '…' timed out after …s` | The plugin took too long | Fix or remove the plugin |

## Service and data

| Problem | Fix |
|---|---|
| Linux service does not start at boot | `systemctl --user status open-market-os`, `journalctl --user -u open-market-os`; run `sudo loginctl enable-linger $USER` |
| `the secret vault is locked …` | Settings → Secrets → unlock, or start with `OMOS_VAULT_PASSPHRASE` |
| `OMOS_VAULT_PASSPHRASE did not unlock the vault` in the log | Wrong or too short (< 8 characters) passphrase; the host keeps running with secrets locked | Fix the variable, or unlock in the UI |
| "database was corrupt; restored from backup …" in the log | The host recovered automatically | Check recent data; keep backups |
| Need to roll back | `omos restore <file from backups/>` with the host stopped |
