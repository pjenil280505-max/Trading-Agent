"""Indicator correctness.

Three kinds of evidence:
1. hand-computed exact values on tiny inputs;
2. agreement with the independent `ta` library (MIT, test-only oracle) after warm-up;
3. structural properties (no look-ahead: appending future bars never changes past values).
"""

import numpy as np
import pandas as pd
import pytest

from omos.indicators import core as ind
from omos.indicators import structure as st
from omos.indicators.registry import SPECS, catalog, compute

ta = pytest.importorskip("ta")


def close_enough(ours, theirs, start, rtol=1e-6, atol=1e-6):
    a, b = np.asarray(ours)[start:], np.asarray(theirs)[start:]
    mask = np.isfinite(a) & np.isfinite(b)
    assert mask.sum() > 50, "not enough overlapping values to compare"
    np.testing.assert_allclose(a[mask], b[mask], rtol=rtol, atol=atol)


# ---------------------------------------------------------------- exact small cases
def test_sma_exact():
    np.testing.assert_allclose(ind.sma([1, 2, 3, 4, 5], 3), [np.nan, np.nan, 2, 3, 4])


def test_sma_window_with_nan_is_nan():
    out = ind.sma([1, 2, np.nan, 4, 5, 6], 2)
    assert np.isnan(out[2]) and np.isnan(out[3]) and out[4] == 4.5 and out[5] == 5.5


def test_ema_seed_and_recursion():
    out = ind.ema([1, 2, 3, 4, 5], 3)
    # seed = mean(1,2,3)=2 ; alpha=0.5 -> 3, 4
    np.testing.assert_allclose(out, [np.nan, np.nan, 2, 3, 4])


def test_wma_exact():
    np.testing.assert_allclose(ind.wma([1, 2, 3], 3), [np.nan, np.nan, (1 + 4 + 9) / 6])


def test_rsi_extremes():
    assert np.nanmax(ind.rsi(np.arange(1, 40, dtype=float), 14)) == 100.0
    assert ind.rsi(np.full(40, 5.0), 14)[-1] == 50.0


def test_true_range_and_atr_shape(ohlcv):
    a = ind.atr(ohlcv.high, ohlcv.low, ohlcv.close, 14)
    assert np.isnan(a[:14]).all() and np.isfinite(a[14:]).all()


def test_shift_refuses_future():
    with pytest.raises(ValueError):
        ind.shift([1, 2, 3], -1)


def test_crossover():
    a = np.array([1, 2, 3, 2, 1], float)
    b = np.array([2, 2, 2, 2, 2], float)
    assert list(ind.crossover(a, b)) == [False, False, True, False, False]
    assert list(ind.crossunder(a, b)) == [False, False, False, False, True]


# ---------------------------------------------------------------- oracle comparisons
def _df(c):
    return pd.DataFrame({"open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume})


def test_against_ta_sma_ema_wma(ohlcv):
    s = _df(ohlcv)["close"]
    close_enough(ind.sma(ohlcv.close, 20), ta.trend.SMAIndicator(s, 20).sma_indicator(), 20)
    # `ta` seeds EMA differently (first value); after ~10 time-constants both agree
    close_enough(ind.ema(ohlcv.close, 20), ta.trend.EMAIndicator(s, 20).ema_indicator(), 250, rtol=1e-6)
    close_enough(ind.wma(ohlcv.close, 20), ta.trend.WMAIndicator(s, 20).wma(), 20)


def test_against_ta_rsi(ohlcv):
    s = _df(ohlcv)["close"]
    close_enough(ind.rsi(ohlcv.close, 14), ta.momentum.RSIIndicator(s, 14).rsi(), 300, rtol=1e-5, atol=1e-4)


def test_against_ta_macd(ohlcv):
    s = _df(ohlcv)["close"]
    m = ind.macd(ohlcv.close, 12, 26, 9)
    ref = ta.trend.MACD(s, 26, 12, 9)
    close_enough(m["macd"], ref.macd(), 300, rtol=1e-5, atol=1e-6)
    close_enough(m["signal"], ref.macd_signal(), 350, rtol=1e-5, atol=1e-6)


def test_against_ta_bbands(ohlcv):
    s = _df(ohlcv)["close"]
    bb = ind.bbands(ohlcv.close, 20, 2.0)
    ref = ta.volatility.BollingerBands(s, 20, 2)
    close_enough(bb["upper"], ref.bollinger_hband(), 20)
    close_enough(bb["lower"], ref.bollinger_lband(), 20)


def test_against_ta_atr(ohlcv):
    d = _df(ohlcv)
    ref = ta.volatility.AverageTrueRange(d["high"], d["low"], d["close"], 14).average_true_range()
    close_enough(ind.atr(ohlcv.high, ohlcv.low, ohlcv.close, 14), ref, 300, rtol=1e-4, atol=1e-4)


def test_against_ta_stoch_willr_cci_roc(ohlcv):
    d = _df(ohlcv)
    so = ta.momentum.StochasticOscillator(d["high"], d["low"], d["close"], 14, 3)
    k = ind.stoch(ohlcv.high, ohlcv.low, ohlcv.close, 14, 3, smooth=1)
    close_enough(k["k"], so.stoch(), 20)
    close_enough(k["d"], so.stoch_signal(), 20)
    close_enough(ind.williams_r(ohlcv.high, ohlcv.low, ohlcv.close, 14),
                 ta.momentum.WilliamsRIndicator(d["high"], d["low"], d["close"], 14).williams_r(), 20)
    close_enough(ind.cci(ohlcv.high, ohlcv.low, ohlcv.close, 20),
                 ta.trend.CCIIndicator(d["high"], d["low"], d["close"], 20).cci(), 25, rtol=1e-6, atol=1e-6)
    close_enough(ind.roc(ohlcv.close, 10), ta.momentum.ROCIndicator(d["close"], 10).roc(), 15)


def test_against_ta_obv_mfi_donchian(ohlcv):
    d = _df(ohlcv)
    ours = ind.obv(ohlcv.close, ohlcv.volume)
    ref = ta.volume.OnBalanceVolumeIndicator(d["close"], d["volume"]).on_balance_volume()
    # `ta` starts the cumulative sum from the first volume; compare day-over-day changes
    np.testing.assert_allclose(np.diff(ours), np.diff(ref.to_numpy()))
    close_enough(ind.mfi(ohlcv.high, ohlcv.low, ohlcv.close, ohlcv.volume, 14),
                 ta.volume.MFIIndicator(d["high"], d["low"], d["close"], d["volume"], 14).money_flow_index(), 20,
                 rtol=1e-6, atol=1e-6)
    dc = ind.donchian(ohlcv.high, ohlcv.low, 20)
    ref_dc = ta.volatility.DonchianChannel(d["high"], d["low"], d["close"], 20)
    close_enough(dc["upper"], ref_dc.donchian_channel_hband(), 20)
    close_enough(dc["lower"], ref_dc.donchian_channel_lband(), 20)


def test_against_ta_adx(ohlcv):
    d = _df(ohlcv)
    ref = ta.trend.ADXIndicator(d["high"], d["low"], d["close"], 14)
    ours = ind.adx(ohlcv.high, ohlcv.low, ohlcv.close, 14)
    close_enough(ours["plus_di"], ref.adx_pos(), 300, rtol=1e-3, atol=1e-3)
    close_enough(ours["minus_di"], ref.adx_neg(), 300, rtol=1e-3, atol=1e-3)
    close_enough(ours["adx"], ref.adx(), 400, rtol=1e-3, atol=1e-3)


def test_psar_against_talib(ohlcv):
    # `ta` checks reversal before clamping SAR to the prior two lows/highs; Wilder and TA-Lib
    # clamp first. We follow Wilder/TA-Lib, so TA-Lib is the oracle for PSAR.
    talib = pytest.importorskip("talib")
    ref = talib.SAR(ohlcv.high, ohlcv.low, acceleration=0.02, maximum=0.2)
    ours = ind.psar(ohlcv.high, ohlcv.low, 0.02, 0.2)
    close_enough(ours, ref, 1, rtol=1e-12, atol=1e-9)


# ---------------------------------------------------------------- TA-Lib (C reference library) oracle
@pytest.fixture
def talib():
    return pytest.importorskip("talib")


def test_talib_exact_moving_averages_and_rsi(ohlcv, talib):
    c = ohlcv.close
    close_enough(ind.sma(c, 20), talib.SMA(c, 20), 19, rtol=1e-12, atol=1e-9)
    close_enough(ind.ema(c, 20), talib.EMA(c, 20), 19, rtol=1e-12, atol=1e-9)   # same SMA seed
    close_enough(ind.wma(c, 20), talib.WMA(c, 20), 19, rtol=1e-12, atol=1e-9)
    close_enough(ind.rsi(c, 14), talib.RSI(c, 14), 14, rtol=1e-10, atol=1e-8)
    close_enough(ind.roc(c, 10), talib.ROC(c, 10), 10, rtol=1e-12, atol=1e-9)
    close_enough(ind.momentum(c, 10), talib.MOM(c, 10), 10, rtol=1e-12, atol=1e-9)
    close_enough(ind.dema(c, 10), talib.DEMA(c, 10), 60, rtol=1e-9, atol=1e-7)
    close_enough(ind.tema(c, 10), talib.TEMA(c, 10), 90, rtol=1e-9, atol=1e-7)


def test_talib_exact_volatility(ohlcv, talib):
    h, lo, c, v = ohlcv.high, ohlcv.low, ohlcv.close, ohlcv.volume
    close_enough(ind.atr(h, lo, c, 14), talib.ATR(h, lo, c, 14), 14, rtol=1e-10, atol=1e-8)
    up, mid, dn = talib.BBANDS(c, 20, 2.0, 2.0, 0)
    bb = ind.bbands(c, 20, 2.0)
    close_enough(bb["upper"], up, 19, rtol=1e-9, atol=1e-8)
    close_enough(bb["lower"], dn, 19, rtol=1e-9, atol=1e-8)
    close_enough(ind.cci(h, lo, c, 20), talib.CCI(h, lo, c, 20), 19, rtol=1e-8, atol=1e-6)
    close_enough(ind.williams_r(h, lo, c, 14), talib.WILLR(h, lo, c, 14), 13, rtol=1e-10, atol=1e-8)
    close_enough(ind.mfi(h, lo, c, v, 14), talib.MFI(h, lo, c, v, 14), 14, rtol=1e-8, atol=1e-6)
    close_enough(ind.ad(h, lo, c, v), talib.AD(h, lo, c, v), 0, rtol=1e-9, atol=1e-6)


def test_talib_adx_dmi(ohlcv, talib):
    # We smooth DM/TR with Wilder's RMA seeded by an SMA (as TradingView does); TA-Lib seeds
    # with a partial sum. The seed difference decays by (13/14) per bar, so after ~300 bars the
    # two must agree to 1e-6.
    h, lo, c = ohlcv.high, ohlcv.low, ohlcv.close
    ours = ind.adx(h, lo, c, 14)
    close_enough(ours["plus_di"], talib.PLUS_DI(h, lo, c, 14), 300, rtol=1e-6, atol=1e-6)
    close_enough(ours["minus_di"], talib.MINUS_DI(h, lo, c, 14), 300, rtol=1e-6, atol=1e-6)
    close_enough(ours["adx"], talib.ADX(h, lo, c, 14), 300, rtol=1e-6, atol=1e-6)


def test_talib_stoch_slow(ohlcv, talib):
    h, lo, c = ohlcv.high, ohlcv.low, ohlcv.close
    k, d = talib.STOCH(h, lo, c, fastk_period=14, slowk_period=3, slowk_matype=0, slowd_period=3, slowd_matype=0)
    s = ind.stoch(h, lo, c, 14, 3, 3)
    close_enough(s["k"], k, 20, rtol=1e-9, atol=1e-7)
    close_enough(s["d"], d, 20, rtol=1e-9, atol=1e-7)


def test_vwap_resets_daily():
    ts = np.array([0, 1, 2]) * 3_600_000 + 86_400_000 * 10
    ts = np.r_[ts, ts + 86_400_000]
    h = np.array([2, 3, 4, 10, 11, 12], float)
    lo = h - 1
    c = h - 0.5
    v = np.ones(6)
    out = ind.vwap(ts, h, lo, c, v)
    tp = (h + lo + c) / 3
    assert out[0] == tp[0] and out[3] == tp[3]   # first bar of each day
    assert np.isclose(out[2], tp[:3].mean())


def test_supertrend_direction_flips(ohlcv):
    s = ind.supertrend(ohlcv.high, ohlcv.low, ohlcv.close, 10, 3)
    d = s["direction"][~np.isnan(s["direction"])]
    assert set(np.unique(d)) <= {-1.0, 1.0} and len(np.unique(d)) == 2


# ---------------------------------------------------------------- no look-ahead property for EVERY registered indicator
@pytest.mark.parametrize("ind_id", sorted(SPECS))
def test_no_lookahead(ohlcv, ind_id):
    full = compute(ind_id, ohlcv)["outputs"]
    cut = 400
    part = compute(ind_id, ohlcv.slice(0, cut))["outputs"]
    for name, arr in part.items():
        np.testing.assert_allclose(arr, full[name][:cut], equal_nan=True,
                                   err_msg=f"{ind_id}.{name} changed when future bars were appended")


def test_structure_confirms_after_right_bars():
    high = np.array([1, 2, 5, 2, 1, 1, 1], float)
    low = high - 0.5
    sp = st.swing_points(high, low, 2, 2)
    assert np.isnan(sp["swing_high"][2]) and sp["swing_high"][4] == 5 and sp["high_at"][4] == 2


def test_fvg_detection():
    high = np.array([10, 11, 13, 14], float)
    low = np.array([9, 10, 12, 12.5], float)  # low[2]=12 > high[0]=10 -> bullish gap at bar 2
    f = st.fair_value_gaps(high, low)
    assert f["bullish"][2] == 1 and f["zones"][0]["bottom"] == 10


def test_bos_and_choch_sequence():
    # higher highs and higher lows, then a close below the last swing low
    h = np.array([10, 12, 11, 14, 12.5, 16, 13.5, 15, 12, 10, 9], float)
    lo = h - 1.0
    c = h - 0.4
    o = h - 0.6
    ms = st.market_structure(o, h, lo, c, left=1, right=1)
    types = [(e["type"], e["direction"]) for e in ms["events"]]
    assert ("BOS", "bullish") in types          # breaking a prior swing high in an uptrend
    assert ("CHoCH", "bearish") in types        # first break of a swing low against the trend
    assert all(e["bar"] > e["pivot_bar"] for e in ms["events"])  # reported after the pivot existed


def test_swing_plateau_rule():
    high = np.array([1, 2, 5, 5, 2, 1, 1], float)
    sp = st.swing_points(high, high - 0.5, 1, 1)
    assert sp["high_at"][3] == 2 and np.isnan(sp["swing_high"][4])  # first bar of a flat top


def test_pivot_points_use_previous_day():
    day = 86_400_000
    ts = np.array([0, 3600_000, day, day + 3600_000]) + 10 * day
    h = np.array([10, 12, 20, 21], float)
    lo = np.array([8, 9, 15, 16], float)
    c = np.array([9, 11, 18, 19], float)
    pp = st.pivot_points(ts, h, lo, c)
    assert np.isnan(pp["p"][0]) and np.isclose(pp["p"][2], (12 + 8 + 11) / 3)


def test_catalog_serialisable_and_param_validation(ohlcv):
    import json

    json.dumps(catalog())
    from omos.errors import ValidationError

    with pytest.raises(ValidationError):
        compute("rsi", ohlcv, {"length": 0})
    with pytest.raises(ValidationError):
        compute("rsi", ohlcv, {"bogus": 1})
    with pytest.raises(ValidationError):
        compute("nope", ohlcv)


def test_volume_profile(ohlcv):
    vp = ind.volume_profile(ohlcv.high, ohlcv.low, ohlcv.close, ohlcv.volume, bins=20)
    assert len(vp["volume"]) == 20
    assert np.isclose(sum(vp["volume"]), ohlcv.volume.sum())
