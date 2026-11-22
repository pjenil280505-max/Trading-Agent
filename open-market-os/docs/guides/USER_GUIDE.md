# User guide

A step-by-step tour for beginners. Everything here uses **paper (simulated) money**.

## 1. Install and open

Follow [START_HERE](../../START_HERE.md) or the [installation guide](INSTALLATION_GUIDE.md). Run
`omos start`, open the printed one-time link, and the Dashboard appears. Later, `omos open` gives a
new link; a signed-in browser stays signed in (device tokens last 30 days for the browser on the host,
90 days for paired devices).

The left menu has **Beginner** and **Advanced** buttons. Beginner hides Automation and shows more
explanations; Advanced shows everything.

## 2. Configure data sources

**Settings → Data sources** lists every provider with its freshness label and terms:

- Crypto venues (OKX, Coinbase, Kraken, Binance) are polled every 5–30 seconds depending on your
  computer's performance tier (change it in **Settings → General**): `REALTIME`.
- Yahoo Finance (stocks, ETFs, indices, futures, forex) is unofficial and `DELAYED`; personal,
  non-commercial use only under Yahoo's terms. Untick it if you do not want it.
- FRED (economic data) and ECB (euro reference rates) are `HISTORICAL`.
- **Import CSV** adds your own data (columns: time, open, high, low, close, optional volume).
- **SEC contact e-mail**: needed only for company fundamentals; the SEC requires it.

Details: [data guide](DATA_GUIDE.md).

## 3. Charts

1. Open **Charts**. In the instrument box type a name (`AAPL`, `ETH`, `RELIANCE`) and press Enter to
   search, or a full id such as `OKX:BTC-USDT` or `YAHOO:^NSEI` to load it directly.
2. Pick a timeframe and a chart type (candles, hollow, Heikin-Ashi, OHLC, line, area) and a scale
   (linear, log, %).
3. Scroll to zoom, drag to pan; scrolling far left loads older history.
4. **Layouts**: 1, 2 (horizontal/vertical), 3, 4, 6, 8 or a custom grid like `3x3`. Tick **Sync**
   for crosshair, symbol, timeframe or visible range. Your layout is saved automatically.
5. **Drawing**: click a chart to focus it; the tool row appears (trend line, ray, horizontal line,
   channels, Fibonacci, pitchfork, shapes, long/short position, measure, note, brush). 🧲 snaps to
   candle prices, 🔒 locks, 👁 hides, ☷ lists and deletes drawings. Drawings are saved per symbol
   and stay in place when you change timeframe.
6. 📷 saves a PNG; ⤢ goes full screen.

The label next to the toolbar tells you how fresh the data is. `STALE` means the provider is down
and you are looking at cached bars.

## 4. Indicators

Choose from **+ Indicator** (34 indicators in trend, momentum, volatility, volume and market
structure groups), set parameters, **Add**. Remove with the ✕ on its chip. Values are calculated by
the host with the same code the screener and backtester use, so all three agree.
Reference: [INDICATORS](../reference/INDICATORS.md).

## 5. Watchlists

**Watchlists → New list**, then add instruments by searching or typing `PROVIDER:SYMBOL`. Each row
shows last price, change, a sparkline and the data label. The first list feeds the Dashboard.

## 6. Screener

1. **Screener**: choose the universe (a watchlist) and timeframe.
2. Add rules (e.g. `rsi14 < 35`, `dist_sma200_pct > 0`) or formulas in OMS Script
   (`crossover(ema(close, 20), ema(close, 50))`), and choose ALL (AND) or ANY (OR). Presets help.
3. **Run screen**. Export results as CSV.
4. **Save** a screen, optionally to run every 15 min / hour / 4 h / day — the host then notifies you
   about *new* matches while it is running.

The screener only scans instruments you list; free sources do not provide whole-market listings.

## 7. Strategies

**Strategy lab** → choose a template or write OMS Script:

```
param fast = 12 (5, 30, 1)
param slow = 26 (20, 100, 2)
long_entry = crossover(ema(close, fast), ema(close, slow))
long_exit = crossunder(ema(close, fast), ema(close, slow))
stop_loss_pct = 8
```

**Check script** validates it; **Save** keeps versions you can export and import as files.
Language reference: [SCRIPT_LANGUAGE](../reference/SCRIPT_LANGUAGE.md).

## 8. Backtest

Set the instrument, timeframe, number of bars and costs (commission, slippage, spread), then
**Run backtest**. You get metrics, an equity curve, drawdown and the trade list. Then check
robustness:

- **Optimise** tries the parameter grid declared in the script (biased upward — never trust it alone).
- **Walk-forward** optimises on one window and tests on the next, four times.
- **Out-of-sample** optimises on 70% and tests once on the unseen 30%.
- **Monte Carlo** resamples the trades to show luck-dependent ranges.

Method and caveats: [BACKTESTING](../reference/BACKTESTING.md).

## 9. Paper trade

**Portfolio → Order ticket**: instrument, side, type (market, limit, stop, stop-limit), quantity,
optional stop-loss and take-profit. The result shows every risk check. **Positions** has a Close
button; **Orders**, **Fills**, **Journal** and **Analytics** are next to it. You can create more
paper accounts and reset one to its starting cash.

**Risk** shows your limits (edit them — they apply to every order), the circuit-breaker state and
the **EMERGENCY STOP**.

## 10. Bots

**Bots → + New bot** → choose type (rule, DCA, grid), instrument, timeframe and paper account →
**Create** → **Validate** → (rule bots) **Backtest** → **Start**. Pause, resume, stop, clone and read
logs from the bot panel. Full guide: [BOT_GUIDE](BOT_GUIDE.md).

## 11. AI (optional)

**Settings → AI** → add a provider. Free: install [Ollama](https://ollama.com), run
`ollama pull qwen2.5:0.5b` (small) or a bigger model your computer can handle, add the **ollama**
preset and enable it. Then **AI workspace**: single analyst, bull-vs-bear debate, or a question with
tools. Paid providers need an API key and a typed cost acknowledgement. [AI guide](AI_GUIDE.md).

## 12. MCP (AI assistants such as Claude Desktop or Claude Code)

**Settings → MCP**: choose which of our tools external assistants may use, then **Create bridge
token** and add the shown configuration to your assistant. You can also connect external MCP
servers whose tools Open Market OS may call. [MCP guide](MCP_GUIDE.md).

## 13. Automation (Advanced mode)

**Automation → + New workflow**: a trigger (schedule, price, formula, market open/close, app event)
and steps (condition, risk check, notify, journal, paper order, screener, backtest, AI analysis,
bot control, MCP tool). New workflows are saved **disabled**. Use **Dry run** first, then enable.
**Stop all automation** is the master switch.

## 14. Permissions and devices

- **Settings → Devices & access**: pair phones with a one-time code and a permission preset
  (`monitor` = read only, `paper-control` = read + paper trading, `full` = everything); revoke any
  device instantly.
- **Settings → MCP**: which tools AI assistants may use.
- **Settings → AI**: what AI agents may do (read, analyze, propose orders for your approval).
- **Settings → Plugins**: plugins only get the permissions you grant.
- **Settings → Live trading**: off. No live broker is included in this release.

## 15. Back up, restore, update, uninstall

See [installation guide](INSTALLATION_GUIDE.md) sections 7–9: daily automatic backups,
`omos backup`, `omos restore <file>` (host stopped), `omos export`, updating keeps your data, and
`omos reset --confirm "DELETE ALL LOCAL DATA"` removes everything.
