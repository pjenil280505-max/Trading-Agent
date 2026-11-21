import numpy as np
import pytest

from omos.errors import ScriptError
from omos.indicators import core as ind
from omos.marketdata.timeframes import aggregate
from omos.script import lang

STRAT = """
# simple EMA cross
param fast = 9 (5, 20, 1)
param slow = 21 (15, 60, 3)
f = ema(close, fast)
s = ema(close, slow)
long_entry = crossover(f, s)
long_exit = crossunder(f, s)
stop_loss = 2 * atr(14)
plot(f, "fast")
"""


def test_parse_and_run_strategy(ohlcv):
    prog = lang.parse(STRAT)
    assert prog.is_strategy
    assert [p["name"] for p in prog.describe_params()] == ["fast", "slow"]
    res = lang.run(prog, ohlcv)
    np.testing.assert_allclose(res.series("f"), ind.ema(ohlcv.close, 9), equal_nan=True)
    assert res.series("long_entry", as_bool=True).dtype == bool
    assert "fast" in res.plots


def test_param_override_and_bounds(ohlcv):
    prog = lang.parse(STRAT)
    res = lang.run(prog, ohlcv, {"fast": 12})
    np.testing.assert_allclose(res.series("f"), ind.ema(ohlcv.close, 12), equal_nan=True)
    with pytest.raises(ScriptError):
        lang.run(prog, ohlcv, {"fast": 99})
    with pytest.raises(ScriptError):
        lang.run(prog, ohlcv, {"nope": 1})


def test_operators_and_precedence(ohlcv):
    res = lang.run("a = 1 + 2 * 3\nb = (1 + 2) * 3\nc = 2 ^ 3\nd = 7 % 4\ne = -close\n", ohlcv)
    assert res.variables["a"] == 7 and res.variables["b"] == 9 and res.variables["c"] == 8 and res.variables["d"] == 3
    np.testing.assert_allclose(res.variables["e"], -ohlcv.close)


def test_history_operator_lags_only(ohlcv):
    res = lang.run("x = close[3]\n", ohlcv)
    np.testing.assert_allclose(res.series("x")[3:], ohlcv.close[:-3])
    with pytest.raises(ScriptError):
        lang.run("x = close[-1]\n", ohlcv)  # future bar: impossible
    with pytest.raises(ScriptError):
        lang.run("x = close[close]\n", ohlcv)  # offset must be constant


def test_division_by_zero_is_nan_not_crash(ohlcv):
    res = lang.run("x = close / (close - close)\ny = 1 / 0\n", ohlcv)
    assert np.isnan(res.series("x")).all() and np.isnan(res.variables["y"])


def test_ternary_iff_nz(ohlcv):
    res = lang.run("a = close > open ? 1 : -1\nb = iff(close > open, 1, -1)\nc = nz(sma(close, 5), 0)\n", ohlcv)
    np.testing.assert_array_equal(res.series("a"), res.series("b"))
    assert res.series("c")[0] == 0


def test_barssince_valuewhen(ohlcv):
    res = lang.run("u = close > close[1]\nb = barssince(u)\nv = valuewhen(u, close)\n", ohlcv)
    u = res.series("u", as_bool=True)
    i = int(np.flatnonzero(u)[5])
    assert res.series("b")[i] == 0 and res.series("v")[i] == ohlcv.close[i]


def test_multi_timeframe_uses_completed_bars_only(ohlcv):
    """tf("15m", close) on 1m bars must equal the close of the last COMPLETED 15m bar."""
    res = lang.run('h = tf("15m", close)\n', ohlcv)
    hi = aggregate(ohlcv, "15m")
    vals = res.series("h")
    for i in (20, 33, 59, 60, 300):
        bar_close = ohlcv.ts[i] + 60_000
        done = [j for j in range(len(hi)) if hi.ts[j] + 900_000 <= bar_close]
        expect = hi.close[done[-1]] if done else np.nan
        assert (np.isnan(expect) and np.isnan(vals[i])) or vals[i] == expect
    # and appending future bars never changes past values
    part = lang.run('h = tf("15m", close)\n', ohlcv.slice(0, 200)).series("h")
    np.testing.assert_allclose(part, vals[:200], equal_nan=True)


def test_mtf_rejects_lower_timeframe(ohlcv):
    with pytest.raises(ScriptError):
        lang.run('h = tf("1m", close)\n', ohlcv)


@pytest.mark.parametrize("src,msg", [
    ("x = __import__('os')\n", "unknown function"),
    ("x = close.__class__\n", "unexpected character"),
    ("x = open('/etc/passwd')\n", "unknown function"),  # 'open' is the price series, not a callable
    ("x = eval('1')\n", "unknown function"),
    ("close = 1\n", "built in"),
    ("x = y + 1\n", "before it is defined"),
    ("x = sma(close)\n", "takes 2"),
    ("x = sma(close, 0)\n", "between"),
    ("x = sma(close, 99999999)\n", "between"),
    ("x = sma(close, close)\n", "constant"),
    ("x = 'hello'\n", "text"),
    ("long_entry = \n", "unexpected"),
    ("x = 1 +\n", "unexpected"),
    ("import os\n", "statement"),
    ("x = lambda: 1\n", "unexpected"),
    ("x = [1,2]\n", "unexpected"),
])
def test_rejected_programs(ohlcv, src, msg):
    with pytest.raises(ScriptError) as e:
        lang.run(src, ohlcv)
    assert msg in e.value.message


def test_python_builtin_names_are_just_variables(ohlcv):
    # 'exec', 'eval', 'os' have no meaning in OMS Script: they are ordinary variable names
    res = lang.run("exec = 1\nos = close * 2\n", ohlcv)
    assert res.variables["exec"] == 1


@pytest.mark.parametrize("src", ["x = " + "-" * 5000 + "1\n", "x = " + "not " * 3000 + "true\n",
                                 "x = " + " + ".join(["close"] * 400) + "\n", "x = 2" + " ^ 2" * 3000 + "\n",
                                 "x = " + "abs(" * 300 + "1" + ")" * 300 + "\n"])
def test_pathological_nesting_gives_clean_error(ohlcv, src):
    with pytest.raises(ScriptError):
        lang.run(src, ohlcv)


def test_reasonable_nesting_is_allowed(ohlcv):
    lang.run("x = max(sma(close, 5), ema(abs(close - open), 3)) + min(rsi(close, 14), nz(cci(20), 0)) * (1 + (2 * (3 + (4 - 1))))\n", ohlcv)
    lang.run("x = " + " + ".join(["close"] * 100) + "\n", ohlcv)


def test_function_name_used_as_series_is_rejected(ohlcv):
    with pytest.raises(ScriptError):
        lang.run("x = sma\n", ohlcv)


def test_size_and_depth_limits(ohlcv):
    with pytest.raises(ScriptError):
        lang.parse("x = 1\n" * (lang.MAX_STATEMENTS + 1))
    with pytest.raises(ScriptError):
        lang.parse("x = " + "(" * 200 + "1" + ")" * 200 + "\n")
    with pytest.raises(ScriptError):
        lang.parse("#" * (lang.MAX_SOURCE + 1))


def test_timeout_enforced(ohlcv):
    heavy = "\n".join(f"x{i} = sma(close, {i + 2}) + ema(close, {i + 3}) + rsi(close, {i + 2})" for i in range(250))
    with pytest.raises(ScriptError) as e:
        lang.run(heavy, ohlcv, timeout_s=0.001)
    assert "stopped" in e.value.message


def test_expression_mode(ohlcv):
    v = lang.evaluate_expression("rsi(close, 14) < 30 and close > sma(close, 50)", ohlcv)
    assert v.dtype == float and set(np.unique(v)) <= {0.0, 1.0}
    with pytest.raises(ScriptError):
        lang.evaluate_expression("x = 1", ohlcv)


def test_multiline_call_inside_parens(ohlcv):
    res = lang.run("x = max(\n  sma(close, 5),\n  sma(close, 10)\n)\n", ohlcv)
    assert res.series("x") is not None


def test_function_catalog_complete():
    names = {f["name"] for f in lang.function_catalog()}
    assert {"sma", "ema", "rsi", "macd", "atr", "tf", "crossover", "supertrend", "swing_high", "vwap"} <= names
