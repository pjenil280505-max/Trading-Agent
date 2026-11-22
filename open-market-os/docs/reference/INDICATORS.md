# Indicator reference

All 34 indicators are calculated by the host (`src/omos/indicators/`) with one
implementation shared by charts, screener, backtester, alerts and AI evidence, so they always agree.

## How they are validated

- **Reference values:** compared with TA-Lib (exact match for most) and the `ta` library as an
  independent oracle on the same data (`tests/unit/test_indicators.py`).
- **No look-ahead:** for every registered indicator, a test computes it on the full series and on
  truncated series and checks that past values never change when future bars are added.
- **Warm-up and gaps:** values before an indicator has enough history are `na`, never zero; missing
  inputs propagate as `na`.
- **No repainting:** swing points and structure breaks appear on the bar where they are *confirmed*
  (after the right-hand bars), not back-dated to the pivot bar.

Known differences: ADX/DMI use TradingView-style smoothing seeds and differ from TA-Lib in roughly the
first 300 bars; Ichimoku omits the chikou span (it is plotted in the past and would leak future
data into strategies); VWAP resets at UTC day boundaries, not exchange session opens.

## Catalogue

| Group | Id | Name | Placement | Parameters (defaults) | Outputs | Notes |
|---|---|---|---|---|---|---|
| trend | `sma` | Simple Moving Average | overlay | length=20, source=close | value |  |
| trend | `ema` | Exponential Moving Average | overlay | length=20, source=close | value |  |
| trend | `wma` | Weighted Moving Average | overlay | length=20, source=close | value |  |
| trend | `vwma` | Volume-Weighted Moving Average | overlay | length=20 | value |  |
| trend | `hma` | Hull Moving Average | overlay | length=21, source=close | value |  |
| trend | `dema` | Double EMA | overlay | length=20, source=close | value |  |
| trend | `tema` | Triple EMA | overlay | length=20, source=close | value |  |
| trend | `supertrend` | Supertrend | overlay | length=10, multiplier=3.0 | line, direction |  |
| trend | `ichimoku` | Ichimoku (no chikou) | overlay | tenkan=9, kijun=26, senkou_b=52, displacement=26 | tenkan, kijun, span_a, span_b | Spans are aligned to the bar they apply to; chikou omitted to avoid look-ahead. |
| trend | `adx` | ADX / DMI | pane | length=14 | adx, plus_di, minus_di |  |
| trend | `psar` | Parabolic SAR | overlay | step=0.02, max_step=0.2 | value |  |
| momentum | `rsi` | Relative Strength Index | pane | length=14, source=close | value |  |
| momentum | `stoch` | Stochastic | pane | k=14, d=3, smooth=3 | k, d |  |
| momentum | `stochrsi` | Stochastic RSI | pane | rsi_length=14, stoch_length=14, k=3, d=3 | k, d |  |
| momentum | `macd` | MACD | pane | fast=12, slow=26, signal=9, source=close | macd, signal, hist |  |
| momentum | `cci` | Commodity Channel Index | pane | length=20 | value |  |
| momentum | `roc` | Rate of Change (%) | pane | length=10, source=close | value |  |
| momentum | `momentum` | Momentum | pane | length=10, source=close | value |  |
| momentum | `willr` | Williams %R | pane | length=14 | value |  |
| momentum | `mfi` | Money Flow Index | pane | length=14 | value |  |
| volatility | `atr` | Average True Range | pane | length=14 | value |  |
| volatility | `bbands` | Bollinger Bands | overlay | length=20, stddev=2.0, source=close | mid, upper, lower |  |
| volatility | `bbwidth` | Bollinger Band Width | pane | length=20, stddev=2.0 | value |  |
| volatility | `keltner` | Keltner Channels | overlay | length=20, multiplier=2.0, atr_length=10 | mid, upper, lower |  |
| volatility | `donchian` | Donchian Channels | overlay | length=20 | upper, mid, lower |  |
| volatility | `hv` | Historical Volatility (% annualised) | pane | length=20, periods_per_year=365.0 | value |  |
| volume | `volume` | Volume | pane | — | value |  |
| volume | `obv` | On-Balance Volume | pane | — | value |  |
| volume | `vwap` | VWAP (anchored) | overlay | anchor=D | value | Resets at UTC day/week boundaries, not exchange session open. |
| volume | `ad` | Accumulation / Distribution | pane | — | value |  |
| structure | `swings` | Swing Highs / Lows (confirmed) | overlay | left=3, right=3 | swing_high, swing_low | Plotted at the bar where each pivot is confirmed (left/right strength), so it never repaints. |
| structure | `pivots` | Classic Pivot Points (prior UTC day) | overlay | — | p, r1, s1, r2, s2 |  |
| structure | `structure` | Market Structure (BOS / CHoCH) | pane | left=3, right=3 | bos, choch |  |
| structure | `fvg` | Fair Value Gaps | pane | — | bullish, bearish |  |

## Market-structure method

- **Swings:** a high is a swing high when it is strictly greater than the `left` bars before it and
  greater than or equal to the `right` bars after it (lows mirror this); reported `right` bars later.
- **BOS / CHOCH:** a close beyond the last confirmed swing in the trend direction is a break of
  structure (BOS); a close beyond it against the trend is a change of character (CHOCH).
- **Fair-value gaps:** three-bar gaps where bar 1's high is below bar 3's low (bullish) or the reverse.
- **Order blocks, support/resistance, liquidity zones:** computed by `/api/structure` with the method
  described in that response; not drawn on charts in this release.
