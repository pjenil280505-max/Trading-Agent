# Backtesting reference

The backtester (`src/omos/backtest/`) replays an OMS Script strategy bar by bar on real (or clearly
labelled simulated/imported) data. It is built to be *pessimistic*: when the data cannot say what
happened first, it assumes the worse outcome.

## Fill model

| Rule | Behaviour |
|---|---|
| Signal timing | Signals are read on a bar's **close**; the order fills at the **next bar's open** (default). The optimistic "same bar close" mode exists but is labelled as such |
| Costs on every fill | Commission (% of notional + optional fixed amount), slippage (basis points, always adverse) and **half the spread** |
| Defaults | Cash 10,000 · commission 0.1% · slippage 5 bps · spread 0 bps |
| Stops and targets | Checked against each bar's high/low. If a bar **gaps** through the stop, the fill is the open, not the stop price |
| Stop and target in the same bar | The **stop is assumed to fill first** (bars do not show the order of intrabar moves) |
| Trailing stop | Ratchets only on bar closes, never using a bar's own extreme to tighten before it ends |
| Partial exit | Optional % closed at the first target; the stop of the remainder moves to break-even |
| Pyramiding | Up to N same-direction entries (default 1); each entry is a layer |
| End of data | Open positions are closed at the last close (can be turned off) |

## Sizing

`pct_equity` (default 100% of equity), `fixed_cash`, `fixed_qty`, or `risk_pct` — equity × % ÷ stop
distance, which requires a stop. A leverage cap (default 1×) limits position value.

## Metrics

| Metric | Definition |
|---|---|
| Total return, CAGR | From initial cash to final equity; years = bars ÷ bars-per-year for the instrument's session and timeframe |
| Volatility | Standard deviation of bar returns, annualised |
| Sharpe | Mean bar return ÷ its standard deviation × √(bars per year); risk-free rate 0 |
| Sortino | Mean bar return ÷ downside deviation × √(bars per year) |
| Max drawdown, Calmar | Largest peak-to-trough fall of equity; CAGR ÷ max drawdown |
| Win rate, profit factor, expectancy, payoff | Over closed trades, after costs |
| Exposure | Share of bars with an open position |
| Buy & hold | Same instrument and period, for comparison |

Warnings are attached when the data is SIMULATED, has missing bars, or yields fewer than 30 trades.

## Robustness tools

| Tool | What it does | Read it as |
|---|---|---|
| **Optimise** | Grid over the script's declared parameter ranges (or ranges you give), at most 400 combinations; the default grid spreads that budget across parameters. Objectives: Sharpe, Sortino, Calmar, return, profit factor, CAGR; a minimum trade count applies | Best-of-grid results are biased upward — they were chosen *because* they did well |
| **Walk-forward** | Splits the data into folds (default 4); each fold optimises on its first 70% and tests on the rest | Look at out-of-sample returns and walk-forward efficiency (OOS ÷ in-sample) |
| **Out-of-sample** | Optimises on the first 70% and tests once on the unseen 30% | A large drop means fitting to noise |
| **Monte Carlo** | Resamples trade returns 1,000 times | 5th-percentile final equity and 95th-percentile drawdown show luck-dependence; assumes independent trades |

## Biases handled — and not

| Bias | Status |
|---|---|
| Look-ahead | Prevented: `x[n]` only looks back; `tf()` uses completed higher-timeframe bars; indicators are tested for it |
| Repainting | Prevented for structure tools (reported when confirmed) |
| Unrealistic fills | Next-bar-open fills, gap-aware stops, stop-first rule, costs on every fill |
| Ignoring costs | Not possible to ignore silently; set them to zero explicitly if you must |
| **Survivorship bias** | **Not handled**: free sources do not include delisted instruments |
| **Holidays** | Not modelled (see known issues) |
| Order-book effects, partial fills, market impact | Not modelled beyond slippage; large orders in thin markets will look better than reality |
| Single instrument | Each run tests one instrument; portfolio effects are not simulated |
