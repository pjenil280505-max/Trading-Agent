import math

import numpy as np
import pytest

from omos.backtest.engine import BacktestConfig, Engine, Signals, backtest_script
from omos.backtest.metrics import max_drawdown, periods_per_year
from omos.backtest.optimize import monte_carlo, optimize, out_of_sample, walk_forward
from omos.errors import ValidationError
from omos.marketdata.models import Candles, DataStatus

NOFEES = dict(commission_pct=0.0, slippage_bps=0.0, spread_bps=0.0)


def bars(o, h, lo, c, step=86_400_000, tf="1d"):
    n = len(c)
    ts = 1_700_000_000_000 + step * np.arange(n, dtype=np.int64)
    return Candles(ts=ts, open=np.asarray(o, float), high=np.asarray(h, float), low=np.asarray(lo, float),
                   close=np.asarray(c, float), volume=np.ones(n), instrument_id="T:X", timeframe=tf,
                   status=DataStatus.HISTORICAL)


def sig(n, entries=(), exits=(), stop=np.nan, target=np.nan, trail=np.nan, short_entries=(), short_exits=()):
    def mask(idx):
        m = np.zeros(n, bool)
        m[list(idx)] = True
        return m
    return Signals(mask(entries), mask(exits), mask(short_entries), mask(short_exits),
                   np.full(n, stop), np.full(n, target), np.full(n, trail), np.full(n, np.nan))


def test_next_open_fill_exact_pnl():
    c = bars([10, 11, 12, 13, 14], [10.5, 11.5, 12.5, 13.5, 14.5], [9.5, 10.5, 11.5, 12.5, 13.5], [10.2, 11.2, 12.2, 13.2, 14.2])
    s = sig(5, entries=[0], exits=[2])
    r = Engine(c, s, BacktestConfig(initial_cash=1100, **NOFEES)).run()
    t = r.trades[0]
    # signal at close of bar 0 -> buy at open of bar 1 (11); exit signal at bar 2 -> sell at open of bar 3 (13)
    assert t["entry_price"] == 11 and t["exit_price"] == 13 and t["qty"] == pytest.approx(100)
    assert t["pnl"] == pytest.approx(200) and r.metrics["final_equity"] == pytest.approx(1300)


def test_fees_slippage_spread_applied():
    c = bars([10, 11, 12, 13, 14], [10.5, 11.5, 12.5, 13.5, 14.5], [9.5, 10.5, 11.5, 12.5, 13.5], [10.2, 11.2, 12.2, 13.2, 14.2])
    s = sig(5, entries=[0], exits=[2])
    cfg = BacktestConfig(initial_cash=10_000, commission_pct=0.1, slippage_bps=10, spread_bps=20, commission_fixed=1)
    t = Engine(c, s, cfg).run().trades[0]
    assert t["entry_price"] == pytest.approx(11 * (1 + 0.002))      # 10bps slippage + 10bps half spread
    assert t["exit_price"] == pytest.approx(13 * (1 - 0.002))
    gross = (t["exit_price"] - t["entry_price"]) * t["qty"]
    fees = t["qty"] * t["entry_price"] * 0.001 + t["qty"] * t["exit_price"] * 0.001 + 2
    assert t["pnl"] == pytest.approx(gross - fees)


def test_stop_loss_intrabar_and_gap():
    o = [10, 10, 10, 7]
    h = [10.5, 10.5, 10.2, 7.5]
    lo = [9.5, 9.5, 8.0, 6.5]
    c = [10, 10, 9.9, 7]
    s = sig(4, entries=[0], stop=1.5)
    r = Engine(bars(o, h, lo, c), s, BacktestConfig(initial_cash=1000, **NOFEES)).run()
    t = r.trades[0]
    assert t["reason"] == "stop_loss" and t["exit_price"] == pytest.approx(8.5)  # 10 - 1.5, intrabar on bar 2
    # gap through the stop: fill at the open, not the stop price
    o2, h2, lo2, c2 = [10, 10, 7, 7], [10.5, 10.5, 7.2, 7.5], [9.5, 9.6, 6.8, 6.5], [10, 10, 7, 7]
    t2 = Engine(bars(o2, h2, lo2, c2), sig(4, entries=[0], stop=1.0), BacktestConfig(initial_cash=1000, **NOFEES)).run().trades[0]
    assert t2["exit_price"] == pytest.approx(7.0) and t2["reason"] == "stop_loss"


def test_stop_first_when_both_hit_in_one_bar():
    o, h, lo, c = [10, 10, 10], [10.2, 10.2, 13], [9.8, 9.8, 7], [10, 10, 10]
    s = sig(3, entries=[0], stop=2, target=2)
    t = Engine(bars(o, h, lo, c), s, BacktestConfig(initial_cash=1000, **NOFEES)).run().trades[0]
    assert t["reason"] == "stop_loss" and t["exit_price"] == pytest.approx(8)
    t2 = Engine(bars(o, h, lo, c), s, BacktestConfig(initial_cash=1000, stop_first=False, **NOFEES)).run().trades[0]
    assert t2["reason"] == "take_profit"


def test_trailing_stop_ratchets_on_close():
    o = [10, 10, 11, 12, 13, 12.6]
    h = [10.1, 10.5, 11.5, 12.5, 13.5, 12.8]
    lo = [9.9, 9.9, 10.8, 11.8, 12.8, 11.0]
    c = [10, 10.4, 11.4, 12.4, 13.4, 11.1]
    s = sig(6, entries=[0], trail=1.0)
    t = Engine(bars(o, h, lo, c), s, BacktestConfig(initial_cash=1000, **NOFEES)).run().trades[0]
    assert t["reason"] == "trailing_stop" and t["exit_price"] == pytest.approx(12.4)  # 13.4 - 1.0, hit intrabar
    # if the bar gaps below the trail, the fill is the (worse) open
    o[5] = 12.0
    t2 = Engine(bars(o, h, lo, c), s, BacktestConfig(initial_cash=1000, **NOFEES)).run().trades[0]
    assert t2["exit_price"] == pytest.approx(12.0)


def test_short_trade_profit_sign():
    o, h, lo, c = [10, 10, 9, 8], [10.1, 10.1, 9.1, 8.1], [9.9, 9.9, 8.9, 7.9], [10, 10, 9, 8]
    s = sig(4, short_entries=[0], short_exits=[2])
    r = Engine(bars(o, h, lo, c), s, BacktestConfig(initial_cash=1000, allow_short=True, **NOFEES)).run()
    t = r.trades[0]
    assert t["side"] == "short" and t["entry_price"] == 10 and t["exit_price"] == 8 and t["pnl"] > 0


def test_shorts_ignored_unless_allowed():
    o, h, lo, c = [10, 10, 9, 8], [10.1, 10.1, 9.1, 8.1], [9.9, 9.9, 8.9, 7.9], [10, 10, 9, 8]
    r = Engine(bars(o, h, lo, c), sig(4, short_entries=[0]), BacktestConfig(**NOFEES)).run()
    assert r.trades == []


def test_risk_pct_sizing_loses_risk_amount_at_stop():
    o, h, lo, c = [100, 100, 100], [101, 101, 100.5], [99, 99, 94], [100, 100, 95]
    s = sig(3, entries=[0], stop=5.0)
    r = Engine(bars(o, h, lo, c), s, BacktestConfig(initial_cash=10_000, sizing="risk_pct", size_value=1.0,
                                                    max_leverage=10, **NOFEES)).run()
    assert r.trades[0]["pnl"] == pytest.approx(-100.0)  # 1% of 10,000


def test_partial_take_profit_then_breakeven():
    o = [10, 10, 10, 11, 10]
    h = [10.1, 10.1, 12.5, 11.2, 10.2]
    lo = [9.9, 9.9, 9.95, 10.5, 9.5]
    c = [10, 10, 11, 11, 10]
    s = sig(5, entries=[0], target=2.0)
    r = Engine(bars(o, h, lo, c), s, BacktestConfig(initial_cash=1000, partial_exit_pct=50, **NOFEES)).run()
    reasons = [t["reason"] for t in r.trades]
    assert reasons[0] == "take_profit_partial" and reasons[1] == "stop_loss"   # rest stopped at break-even
    assert r.trades[1]["exit_price"] == pytest.approx(10.0)


def test_no_lookahead_truncation_invariance(ohlcv):
    src = """
param fast = 8
param slow = 21
long_entry = crossover(ema(close, fast), ema(close, slow))
long_exit = crossunder(ema(close, fast), ema(close, slow))
stop_loss = 2 * atr(14)
"""
    full = backtest_script(src, ohlcv, BacktestConfig()).trades
    cut = 400
    part = backtest_script(src, ohlcv.slice(0, cut), BacktestConfig(close_at_end=False)).trades
    closed_before = [t for t in full if t["exit_bar"] < cut - 1]
    assert closed_before, "test needs trades before the cut"
    assert [(t["entry_bar"], t["exit_bar"], round(t["pnl"], 9)) for t in closed_before] == \
           [(t["entry_bar"], t["exit_bar"], round(t["pnl"], 9)) for t in part if t["exit_bar"] < cut - 1]


def test_backtest_reports_warnings_and_metrics(ohlcv):
    r = backtest_script("long_entry = close > sma(close, 20)\nlong_exit = close < sma(close, 20)\n", ohlcv)
    m = r.metrics
    for k in ("sharpe", "sortino", "max_drawdown_pct", "profit_factor", "win_rate_pct", "exposure_pct", "fees_paid",
              "buy_hold_return_pct", "expectancy"):
        assert k in m
    assert m["fees_paid"] > 0
    assert any("trades" in w or "missing" in w for w in r.warnings) or m["trades"] >= 30


def test_non_strategy_rejected(ohlcv):
    with pytest.raises(ValidationError):
        backtest_script("x = sma(close, 5)\n", ohlcv)


def test_config_validation():
    with pytest.raises(ValidationError):
        BacktestConfig(initial_cash=-1).validate()
    with pytest.raises(ValidationError):
        BacktestConfig(fill="magic").validate()


def test_max_drawdown_and_periods():
    dd, peak, trough, under = max_drawdown(np.array([100, 120, 90, 95, 130]))
    assert dd == pytest.approx(0.25) and peak == 1 and trough == 2 and under == 2
    assert periods_per_year("1d", "xnys") == pytest.approx(252)
    assert periods_per_year("1h", "24x7") == pytest.approx(8766)


def test_optimize_walk_forward_oos_montecarlo(ohlcv):
    src = """
param fast = 5 (4, 10, 2)
param slow = 20 (20, 30, 5)
long_entry = crossover(sma(close, fast), sma(close, slow))
long_exit = crossunder(sma(close, fast), sma(close, slow))
"""
    opt = optimize(src, ohlcv, objective="total_return_pct", min_trades=1)
    assert opt["combinations"] == 4 * 3
    scores = [r["score"] for r in opt["results"] if r["score"] is not None]
    assert scores == sorted(scores, reverse=True)
    wf = walk_forward(src, ohlcv, folds=3)
    assert len(wf["folds"]) == 3
    oos = out_of_sample(src, ohlcv, train_pct=60)
    assert "out_of_sample" in oos or "message" in oos
    trades = backtest_script(src, ohlcv).trades
    mc = monte_carlo(trades, 10_000, runs=200)
    assert mc["final_equity"]["p5"] <= mc["final_equity"]["p50"] <= mc["final_equity"]["p95"]


def test_optimize_grid_limit(ohlcv):
    src = "param a = 5 (1, 300, 1)\nparam b = 5 (1, 300, 1)\nlong_entry = close > sma(close, a)\n"
    with pytest.raises(ValidationError):
        optimize(src, ohlcv, ranges={"a": list(range(1, 50)), "b": list(range(1, 50))})


def test_equity_never_nan(ohlcv):
    r = backtest_script("long_entry = rsi(close, 14) < 30\nlong_exit = rsi(close, 14) > 60\n", ohlcv)
    assert all(math.isfinite(e[1]) for e in r.equity)
