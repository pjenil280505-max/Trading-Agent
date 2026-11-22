# Bot guide

Bots trade **paper accounts** only in this release. Every bot order goes through the same risk engine
as your manual orders, and the emergency stop pauses every bot at once.

## How a bot decides

- The bot runner wakes every **10 seconds**. For each running bot it fetches recent bars.
- **Decisions are made once per new closed bar.** A 1-hour bot evaluates its rules at most once an
  hour, a daily bot once a day. A forming (unfinished) bar is never used for entries or exits, so a
  signal cannot appear and then vanish.
- **Protective stops and targets** of rule bots are checked against the latest price on every tick,
  so they do not wait for the bar to close.
- Order ids are built from the bot, the bar time and the action (`bot7:<bar time>:entry_long`).
  If the host restarts and replays a bar, the same order is recognised and never placed twice.

## Bot types

| Type | What it does | Key settings |
|---|---|---|
| **Rule** | Runs an OMS Script strategy: `long_entry`, `long_exit`, `short_entry`, `short_exit`, `stop_loss(_pct)`, `take_profit(_pct)` | Strategy (template, saved strategy or custom script); sizing; allow short; bars of history (50–5000) |
| **DCA** | Buys a fixed amount every N bars until a total is invested, optionally only when a formula is true | Amount per buy (cash or % equity); every N bars; stop after investing X; optional condition such as `rsi(close, 14) < 40` |
| **Grid** | Places limit buys at levels below the price and sells one level up after each fill; never sells more than it holds | Lower and upper price, 2–100 levels, quantity per level |

Sizing for rule bots: `% of equity`, `fixed cash`, `fixed quantity`, or `% equity at risk`, which
sizes the position from the stop distance (requires a stop in the strategy).

Grids lose money when the price leaves the range and stays out; DCA keeps buying into a falling
market until its cap. Neither is a guarantee of anything.

## Step by step

1. **Strategy lab** first: backtest the idea with costs and check walk-forward/out-of-sample.
2. **Bots → + New bot**: name, type, instrument (e.g. `OKX:BTC-USDT`), timeframe, paper account,
   settings → **Create (paper)**. State: `configured`.
3. **Validate**: checks the script on real recent data. State: `validated`.
4. **Backtest** (rule bots): runs the bot's exact script and sizing. State: `backtested`.
5. **Start**: state `running`. Watch the **Logs** tab; each order also appears in Portfolio → Orders.
6. **Pause / Resume** keep the position; **Stop & cancel orders** cancels the bot's open orders
   (positions stay — close them in Portfolio if you want).
7. To change settings, pause or stop the bot, edit the JSON in **Configuration**, **Save**, then
   validate again.
8. **Clone** copies a bot; **Archive** retires a stopped one; **State history** shows every transition.

Lifecycle: `created → configured → validated → backtested → running ⇄ paused → stopped`, plus
`error`, `recovering`, `paper_tested` (after ≥24 h of paper running) and `approved`. Illegal
jumps (e.g. starting an unvalidated bot) are refused.

## Risk settings that affect bots

Set in **Risk** (per account). Defaults: 2% of equity at risk per trade, 10% assumed risk when an
order has no stop, 5% daily / 10% weekly loss and 20% drawdown circuit breakers, 100% total and 50%
per-instrument exposure, no leverage, 10 open positions, 50% of equity per order, 20 orders per
minute, 100 trades per day, **5 bots running at once**, 3 entries per position (1 when ATR% > 5),
limit/stop prices within 5% of the last price, no short selling.

When a circuit breaker trips, trading halts for that account and running bots are **paused** on
their next tick. Only a person can resume (Risk → Resume trading).

## Errors and recovery

- A failing tick is logged and retried with growing waits (20 s, 40 s, 80 s, 160 s).
  After **5 failures in a row** — about 5 minutes of a provider outage — the bot moves to `error`
  and stops trading until you press Start again. Stale data counts as a failure: bots never trade on
  data the host could not refresh.
- After a crash, reboot or power cut, the host first reconciles orders (cancels orders that were
  never confirmed), then resumes bots that were running (`recovering → running`). Duplicate orders
  are prevented by the idempotent order ids above.
- A corrupt database is replaced by the newest valid backup at start-up (logged).

## Running 24/7 on a home computer

Bots and alerts run only while the host runs. There is no cloud server.

1. **Autostart:** `omos service install` ([installation guide §6](INSTALLATION_GUIDE.md#6-start-automatically-for-247-bots-and-alerts)).
   On Linux also run `sudo loginctl enable-linger $USER` so it runs without a login session.
2. **No sleep:**
   - Windows: Settings → System → Power → *Screen and sleep* → "When plugged in, put my device to
     sleep after: Never" (the screen may turn off).
   - macOS: System Settings → Battery/Energy → enable "Prevent automatic sleeping when the display
     is off" (on power adapter).
   - Linux desktops: turn off automatic suspend in power settings; on servers, sleep is usually off.
3. **Power:** keep laptops plugged in. If the host starts while on battery it polls less often (decided at start-up). A small UPS
   protects against short power cuts; after longer ones the autostart and recovery above apply.
4. **Network:** prefer a wired connection. Short outages are handled (cached data is labelled
   `STALE`, bots do not trade on it); outages longer than about 5 minutes put bots into `error` as described.
5. **Check on it:** System status shows every background loop, its last success and restarts.
   `omos status` and `omos doctor` work from a terminal.

Android phones are not suitable hosts (the OS stops background work); use them as remote screens.

## Live trading

**Not available in 0.1.0.** No live broker or exchange adapter has been built and verified, so every
account is a paper account. The safety framework for a future adapter already exists: live accounts
need the `trade:live` permission, a typed confirmation in Settings → Live trading, and a bot must be
paper-tested for ≥24 h and approved by a person with a typed confirmation. AI agents and MCP clients
can never control live bots.

## Emergency stop

**Risk → ■ EMERGENCY STOP** (also available to paired devices with paper-trading permission):
pauses every bot, cancels every open order and blocks all new orders — optionally closing all
positions too. Clearing it requires typing `RESUME TRADING`, and bots stay paused until you resume
each one.
