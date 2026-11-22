# OMS Script reference

OMS Script is the small language used for strategies, custom indicators, screener formulas, alert
conditions and workflow conditions. It works on whole price series at once (like a spreadsheet
column), runs inside a sandbox, and **cannot see the future**.

## A complete strategy

```
# EMA trend with a higher-timeframe filter
param fast = 12 (5, 30, 1)          # name = default (min, max, step) - the range drives optimisation
param slow = 26 (20, 100, 2)
f = ema(close, fast)
s = ema(close, slow)
trend_up = tf("4h", close > ema(close, 50))   # completed 4-hour bars only
long_entry = crossover(f, s) and trend_up
long_exit = crossunder(f, s)
stop_loss_pct = 8                   # protective stop 8% below the fill
take_profit_pct = 20
plot(f, "fast EMA")
plot(s, "slow EMA")
```

## Syntax

| Element | Form |
|---|---|
| Comment | `# …` to the end of the line |
| Assignment | `name = expression` (one statement per line) |
| Parameter | `param name = 10` or `param name = 10 (min, max, step)` |
| Plot | `plot(expression, "label")` |
| Numbers | `14`, `0.5`, `1e-3` |
| Constants | `true`, `false`, `na` (missing), `pi` |
| Arithmetic | `+ - * / % ^` (`^` is power) |
| Comparison | `< <= > >= == !=` |
| Logic | `and`, `or`, `not` |
| Choice | `condition ? a : b` |
| Look-back | `x[n]` = value of `x` *n* bars ago (`n` a non-negative constant). There is no look-forward |
| Higher timeframe | `tf("4h", expression)` evaluates on completed 4-hour bars and aligns the result back |

Built-in series: `open high low close volume hl2 hlc3 ohlc4 time bar_index`.
Names are letters, digits and `_` (up to 40 characters).

## Strategy outputs

A script is a *strategy* when it assigns `long_entry` and/or `short_entry`.

| Output | Meaning |
|---|---|
| `long_entry`, `short_entry` | Open a position when true (on a closed bar; filled at the next bar's open) |
| `long_exit`, `short_exit` | Close the position when true |
| `stop_loss` / `stop_loss_pct` | Protective stop as a price distance or % of the entry |
| `take_profit` / `take_profit_pct` | Target as a price distance or % |
| `trailing_stop` / `trailing_stop_pct` | Trailing stop distance; ratchets on bar closes |
| `position_size_pct` | Size as % of equity for that entry (overrides the run's sizing) |

Short entries are ignored unless short selling is allowed in the run or bot.

## Limits (sandbox)

| Limit | Value |
|---|---|
| Source size | 20,000 characters |
| Statements | 300 |
| Expression nodes | 5,000 |
| Nesting depth | 48 |
| Parsed tree depth | 160 |
| Look-back / window length | 5,000 bars |
| Evaluation time | 5 seconds |

There are no loops, user-defined functions, imports, attribute access, files, network or system
calls — so every script terminates and cannot reach your computer.

## Functions (69)

| Function | Arguments | Description |
|---|---|---|
| `abs` | 1 | absolute value |
| `adx` | 1 | ADX |
| `atr` | 1 | average true range |
| `barssince` | 1 | bars since condition was last true |
| `bb_lower` | 3 | Bollinger lower band |
| `bb_mid` | 2 | Bollinger middle band |
| `bb_upper` | 3 | Bollinger upper band |
| `bb_width` | 3 | Bollinger band width |
| `cci` | 1 | CCI |
| `ceil` | 1 | ceiling |
| `change` | 1–2 | x - x[n] |
| `clamp` | 3 | clamp |
| `cross` | 2 | a crosses b either way |
| `crossover` | 2 | a crosses above b |
| `crossunder` | 2 | a crosses below b |
| `cum` | 1 | cumulative sum |
| `dc_lower` | 1 | Donchian lower |
| `dc_upper` | 1 | Donchian upper |
| `dema` | 2 | double EMA |
| `ema` | 2 | exponential moving average |
| `exp` | 1 | e^x |
| `falling` | 2 | x fell on each of the last n bars |
| `floor` | 1 | floor |
| `fvg_bear` | 0 | bearish fair-value gap |
| `fvg_bull` | 0 | bullish fair-value gap |
| `highest` | 2 | highest value over n bars |
| `hma` | 2 | Hull moving average |
| `hv` | 1–2 | historical volatility % |
| `iff` | 3 | if-then-else |
| `kc_lower` | 3 | Keltner lower |
| `kc_upper` | 3 | Keltner upper |
| `log` | 1 | natural log |
| `lowest` | 2 | lowest value over n bars |
| `macd` | 4 | MACD line |
| `macd_hist` | 4 | MACD histogram |
| `macd_signal` | 4 | MACD signal line |
| `max` | 2 | element-wise maximum |
| `mfi` | 1 | money flow index |
| `min` | 2 | element-wise minimum |
| `minus_di` | 1 | -DI |
| `mom` | 2 | momentum |
| `na` | 1 | is NaN |
| `nz` | 1–2 | replace NaN |
| `obv` | 0 | on-balance volume |
| `plus_di` | 1 | +DI |
| `psar` | 0–2 | Parabolic SAR |
| `rising` | 2 | x rose on each of the last n bars |
| `rma` | 2 | Wilder moving average |
| `roc` | 2 | rate of change % |
| `round` | 1 | round |
| `rsi` | 2 | RSI |
| `sign` | 1 | sign |
| `sma` | 2 | simple moving average |
| `sqrt` | 1 | square root |
| `stdev` | 2 | population standard deviation |
| `stoch_d` | 3 | stochastic %D |
| `stoch_k` | 3 | stochastic %K |
| `supertrend` | 2 | Supertrend line |
| `supertrend_dir` | 2 | Supertrend direction (+1 up, -1 down) |
| `swing_high` | 2 | last confirmed swing high price |
| `swing_low` | 2 | last confirmed swing low price |
| `tema` | 2 | triple EMA |
| `tf` | 2 | evaluate an expression on a higher timeframe (completed bars only) |
| `tr` | 0 | true range |
| `valuewhen` | 2 | value of x when condition was last true |
| `vwap` | 0 | VWAP reset each UTC day |
| `vwma` | 1 | volume-weighted MA of close |
| `willr` | 1 | Williams %R |
| `wma` | 2 | weighted moving average |
