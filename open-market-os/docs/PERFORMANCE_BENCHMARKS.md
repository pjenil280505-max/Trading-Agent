# Performance benchmarks

Measured on **2026-11-22** with `python scripts/benchmark.py` (raw output:
[`benchmarks/2026-11-22-linux-cloud-container.json`](benchmarks/2026-11-22-linux-cloud-container.json)).

**Machine:** a shared Linux cloud container — Intel Xeon @ 2.10 GHz, 4 logical CPUs, 15.7 GB RAM,
Python 3.13.18, NumPy 2.5.5. It is not a typical user computer and other workloads shared the CPU,
so treat the numbers as rough orders of magnitude. Your machine will differ; rerun the script.

## What was measured, and how

| Area | Method |
|---|---|
| Indicators, OMS Script, backtester, optimiser | In-process, synthetic random-walk OHLCV with a fixed seed (no network). Median of 5 runs after 1 warm-up (optimiser and walk-forward: 1 run). |
| API latency | Real HTTP to uvicorn on 127.0.0.1 with the full app stack (Host-header guard, token auth, JSON), SIMULATED demo data, 30 requests per endpoint after 1 warm-up. |
| UI | Chromium (Playwright) against the same server: sign-in to app shell, opening the Charts page until the data badge appears. |
| Cold market-data fetch | Measured separately with `curl` against a fresh data folder and live OKX REST (see below). |

## Compute

| Operation | Bars | Median |
|---|---:|---:|
| All 34 indicators, one after another | 10,000 | 204 ms total |
| All 34 indicators, one after another | 50,000 | 725 ms total |
| OMS Script evaluation (EMA-cross template) | 10,000 | 4.7 ms |
| OMS Script evaluation (EMA-cross template) | 50,000 | 20.3 ms |
| Backtest (EMA-cross, costs on, next-bar-open fills) | 10,000 | 32.0 ms |
| Backtest (EMA-cross) | 50,000 | 150.4 ms |
| Grid optimisation, 400 combinations | 5,000 | 6.66 s (16.6 ms per backtest) |
| Walk-forward, 4 folds (each fold optimises its window) | 5,000 | 5.44 s |
| Monte Carlo, 1,000 resamples of 194 trades | — | 25.0 ms |

Per indicator (median ms), slowest first:

| Indicator | 10,000 bars (ms) | 50,000 bars (ms) |
|---|---:|---:|
| structure | 53.66 | 260.99 |
| swings | 42.26 | 108.35 |
| supertrend | 22.17 | 68.26 |
| adx | 9.26 | 34.18 |
| psar | 8.27 | 22.56 |
| stochrsi | 6.68 | 21.37 |
| ichimoku | 5.43 | 21.12 |
| fvg | 3.70 | 20.70 |
| macd | 6.07 | 19.26 |
| tema | 6.00 | 19.23 |
| rsi | 4.34 | 13.68 |
| dema | 4.10 | 13.57 |
| keltner | 4.21 | 13.52 |
| pivots | 4.71 | 12.81 |
| cci | 3.07 | 8.34 |
| stoch | 2.53 | 8.29 |
| willr | 1.83 | 7.59 |
| atr | 2.21 | 7.17 |
| donchian | 1.74 | 6.79 |
| ema | 2.04 | 6.46 |
| bbands | 1.83 | 6.32 |
| bbwidth | 1.77 | 6.09 |
| hv | 1.52 | 5.84 |
| mfi | 1.42 | 4.19 |
| hma | 0.81 | 1.98 |
| vwap | 0.43 | 1.48 |
| vwma | 0.31 | 1.28 |
| ad | 0.17 | 0.89 |
| wma | 0.42 | 0.87 |
| sma | 0.17 | 0.68 |
| obv | 0.14 | 0.57 |
| roc | 0.11 | 0.44 |
| momentum | 0.06 | 0.33 |
| volume | 0.04 | 0.18 |

The market-structure family (`structure`, `swings`) loops in Python because pivots must be confirmed
bar by bar without look-ahead; it is the slowest group but still well under a second at 50,000 bars.

## API latency (HTTP, localhost)

| Endpoint | p50 | p95 |
|---|---:|---:|
| `GET /api/health` | 2.2 ms | 2.4 ms |
| `GET /api/candles` — 1,000 bars from cache | 19.8 ms | 26.7 ms |
| `GET /api/candles` — 5,000 bars from cache | 83.0 ms | 115.7 ms |
| `POST /api/indicators/compute` — RSI on 1,000 bars | 12.9 ms | 15.5 ms |
| `GET /api/watchlists/{id}/quotes` — 3 symbols | 9.0 ms | 15.5 ms |
| `POST /api/screener/run` — 3 symbols | 14.0 ms | 17.5 ms |
| `POST /api/backtest` — EMA cross, 2,000 bars | 34.4 ms | 57.0 ms |
| `GET /api/accounts/{id}/summary` | 2.4 ms | 2.6 ms |

## Market data from real providers (network-bound)

Measured once with `curl` on this machine, live OKX REST, 2026-11-22:

| Request | Time |
|---|---:|
| 5,000 one-hour bars, **empty cache** (OKX pages ~300 bars per request) | 9.4 s |
| The same request again (local cache, incremental) | 0.33 s |

Because of this, the chart asks for a 1,000-bar first chunk and loads older history only when you
scroll left, and it shows a "first load can take several seconds" message while waiting.

## Web UI

| Item | Value |
|---|---:|
| JavaScript bundle | 424 KB (117 KB gzip) |
| CSS | 8 KB (2.4 KB gzip) |
| One-time sign-in link → app shell ready | 209 ms |
| `DOMContentLoaded` / `load` | 72 ms / 72 ms |
| Charts page, first open (host generates + caches bars) | 171 ms |
| Charts page, warm open (median of 5) | 72 ms |
| JS heap after the runs above | 12 MB |

## Not measured (honest gaps)

- Phones, tablets, low-end laptops, Windows and macOS: no such hardware was available. The app
  adapts chart history (1,500 / 5,000 / 20,000 bars) to a detected low / medium / high tier, but the
  tiers' thresholds are estimates, not measurements.
- Long-running memory behaviour of the host over days, and many simultaneous charts with live updates.
- AI model latency: depends entirely on the model and hardware (a 0.5B local model answered in seconds
  here during functional tests; not benchmarked).
- Latency over Wi-Fi from paired devices.
