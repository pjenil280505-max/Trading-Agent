# START HERE

Open Market OS runs on **your own computer**. You open it in a web browser; phones and tablets on
your home network can join later. Everything starts in **paper mode with simulated money** — nothing
here can place a real trade.

## 1. Install (Linux, about 5 minutes)

**Option A — single-file app (Linux x86_64):**

```bash
tar -xzf omos-0.1.0-linux-x86_64.tar.gz
cd omos-0.1.0-linux-x86_64
./omos start
```

**Option B — from source (Linux; Windows/macOS untested):** see the
[installation guide](docs/guides/INSTALLATION_GUIDE.md). In short: Python 3.11+, Node.js 20+, then
`pip install -e .`, `scripts/build_web.sh`, `omos start`.

`omos start` prints a link like `http://127.0.0.1:8765/#code=…`. Open it in your browser within
10 minutes. It works once; later, run `omos open` for a new one.

> No desktop keychain (servers, minimal Linux)? Secrets then go into an encrypted file. Start with
> `OMOS_VAULT_PASSPHRASE='a long passphrase' ./omos start` or unlock it later in Settings → Secrets.

## 2. Your first ten minutes

1. **Dashboard** — a starter watchlist with crypto (live from exchanges) and stocks/indices
   (delayed, from Yahoo). Every price shows a label: `REALTIME`, `DELAYED`, `HISTORICAL`,
   `SIMULATED`, or `STALE` when a source is down.
2. **Charts** — type a symbol (`AAPL`, `ETH`, `RELIANCE`) and press Enter, or a full id like
   `OKX:BTC-USDT`. Pick a timeframe, add an indicator from **+ Indicator**, draw with the tool row.
   The first load of a symbol can take a few seconds.
3. **Strategy lab** — choose a template (EMA crossover is a good start), set the instrument, press
   **Run backtest**. Costs are always charged. Then try **Walk-forward**: if out-of-sample results
   are much worse, the strategy was probably fitted to noise.
4. **Portfolio → Order ticket** — place a small paper order. Look at "Risk checks" to see the rules
   every order passes.
5. **Bots** — create a rule bot from the same template on a paper account, then
   **Validate → Start**. Bots act only when a bar closes, so a 1-hour bot acts at most once an hour.
6. **Risk** — find the **EMERGENCY STOP** button now, before you need it.

## 3. Things to know

- Bots and alerts run only while this computer is on, awake and online. See the
  [bot guide](docs/guides/BOT_GUIDE.md) for running 24/7 on a home PC.
- AI is optional and off by default. The free route is Ollama on your computer
  ([AI guide](docs/guides/AI_GUIDE.md)). Paid providers stay disabled until you accept a cost warning.
- To use a phone: restart with `omos start --allow-remote` on a network you trust, then create a
  pairing code in **Settings → Devices & access**.
- Back up: **Settings → Backups** (daily automatic backups are on). Restore with
  `omos restore <file>` while the app is stopped.

## 4. Where to go next

[User guide](docs/guides/USER_GUIDE.md) · [Troubleshooting](docs/guides/TROUBLESHOOTING_GUIDE.md) ·
[Security](docs/guides/SECURITY_GUIDE.md) · [What works and what doesn't](docs/FEATURE_MATRIX.md)

Not financial advice. Backtests, simulations and AI output do not predict future results.
