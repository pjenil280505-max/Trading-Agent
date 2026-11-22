"""Technical indicators on numpy arrays.

Conventions (documented in docs/reference/INDICATORS.md):

* Every function returns arrays the same length as its input, with ``NaN`` during
  the warm-up period. No value ever depends on a later bar (no look-ahead).
* EMA is seeded with the SMA of its first ``n`` values (TA-Lib convention).
* Wilder smoothing (RMA) is used for RSI, ATR and ADX, seeded with an SMA.
* NaNs inside the input propagate; they are never filled with guesses.
"""

from __future__ import annotations

import numpy as np

NAN = np.nan


def _f(x) -> np.ndarray:
    return np.asarray(x, dtype=np.float64)


def _check_n(n: int, name: str = "length") -> int:
    n = int(n)
    if n < 1:
        raise ValueError(f"{name} must be >= 1")
    return n


# ------------------------------------------------------------------ moving averages
def sma(x, n: int) -> np.ndarray:
    x = _f(x)
    n = _check_n(n)
    out = np.full(x.shape, NAN)
    if len(x) < n:
        return out
    c = np.cumsum(np.insert(x, 0, 0.0))
    out[n - 1:] = (c[n:] - c[:-n]) / n
    # cumsum turns any NaN into NaN forever; recompute windows containing NaN honestly
    if np.isnan(x).any():
        isn = np.isnan(x).astype(np.int64)
        cn = np.cumsum(np.insert(isn, 0, 0))
        bad = (cn[n:] - cn[:-n]) > 0
        good_idx = np.flatnonzero(~bad) + n - 1
        xs = np.nan_to_num(x)
        c2 = np.cumsum(np.insert(xs, 0, 0.0))
        out[n - 1:] = NAN
        out[good_idx] = (c2[good_idx + 1] - c2[good_idx + 1 - n]) / n
    return out


def _recursive(x: np.ndarray, alpha: float, seed_n: int) -> np.ndarray:
    """y[t] = alpha*x[t] + (1-alpha)*y[t-1], seeded with SMA(x[:seed_n]) at the first full window."""
    out = np.full(x.shape, NAN)
    valid = np.flatnonzero(~np.isnan(x))
    if len(valid) == 0:
        return out
    start = valid[0]
    if len(x) - start < seed_n:
        return out
    seed_end = start + seed_n
    window = x[start:seed_end]
    if np.isnan(window).any():
        return out
    prev = float(window.mean())
    out[seed_end - 1] = prev
    a, b = alpha, 1.0 - alpha
    xs = x.tolist()
    res = out.tolist()
    for i in range(seed_end, len(xs)):
        xi = xs[i]
        if xi != xi:  # NaN breaks the chain honestly
            res[i] = NAN
            continue
        if prev != prev:
            prev = xi
        prev = a * xi + b * prev
        res[i] = prev
    return np.asarray(res)


def ema(x, n: int) -> np.ndarray:
    n = _check_n(n)
    return _recursive(_f(x), 2.0 / (n + 1.0), n)


def rma(x, n: int) -> np.ndarray:
    """Wilder's moving average (a.k.a. SMMA)."""
    n = _check_n(n)
    return _recursive(_f(x), 1.0 / n, n)


def wma(x, n: int) -> np.ndarray:
    x = _f(x)
    n = _check_n(n)
    out = np.full(x.shape, NAN)
    if len(x) < n:
        return out
    w = np.arange(1, n + 1, dtype=np.float64)
    windows = np.lib.stride_tricks.sliding_window_view(x, n)
    out[n - 1:] = windows @ w / w.sum()
    return out


def vwma(close, volume, n: int) -> np.ndarray:
    c, v = _f(close), _f(volume)
    num = sma(c * v, n)
    den = sma(v, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den > 0, num / den, NAN)


def hma(x, n: int) -> np.ndarray:
    n = _check_n(n)
    half = max(1, n // 2)
    sq = max(1, int(np.sqrt(n)))
    raw = 2 * wma(x, half) - wma(x, n)
    return _nan_aware(lambda y: wma(y, sq), raw)


def dema(x, n: int) -> np.ndarray:
    e1 = ema(x, n)
    e2 = _nan_aware(lambda y: ema(y, n), e1)
    return 2 * e1 - e2


def tema(x, n: int) -> np.ndarray:
    e1 = ema(x, n)
    e2 = _nan_aware(lambda y: ema(y, n), e1)
    e3 = _nan_aware(lambda y: ema(y, n), e2)
    return 3 * e1 - 3 * e2 + e3


def _nan_aware(fn, y: np.ndarray) -> np.ndarray:
    """Apply ``fn`` to the part of ``y`` after its leading NaNs (warm-up), keep alignment."""
    y = _f(y)
    valid = np.flatnonzero(~np.isnan(y))
    out = np.full(y.shape, NAN)
    if len(valid) == 0:
        return out
    s = valid[0]
    out[s:] = fn(y[s:])
    return out


# ------------------------------------------------------------------ helpers
def highest(x, n: int) -> np.ndarray:
    x = _f(x)
    n = _check_n(n)
    out = np.full(x.shape, NAN)
    if len(x) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(x, n).max(axis=1)
    return out


def lowest(x, n: int) -> np.ndarray:
    x = _f(x)
    n = _check_n(n)
    out = np.full(x.shape, NAN)
    if len(x) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(x, n).min(axis=1)
    return out


def stdev(x, n: int, ddof: int = 0) -> np.ndarray:
    x = _f(x)
    n = _check_n(n)
    out = np.full(x.shape, NAN)
    if len(x) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(x, n).std(axis=1, ddof=ddof)
    return out


def shift(x, k: int) -> np.ndarray:
    """Lag by ``k`` bars (k >= 0 only — negative shifts would look into the future)."""
    x = _f(x)
    k = int(k)
    if k < 0:
        raise ValueError("negative shift would use future data")
    if k == 0:
        return x.copy()
    out = np.full(x.shape, NAN)
    out[k:] = x[:-k]
    return out


def change(x, k: int = 1) -> np.ndarray:
    return _f(x) - shift(x, k)


def true_range(high, low, close) -> np.ndarray:
    h, lo, c = _f(high), _f(low), _f(close)
    pc = shift(c, 1)
    tr = np.nanmax(np.vstack([h - lo, np.abs(h - pc), np.abs(lo - pc)]), axis=0)
    tr[0] = h[0] - lo[0]
    return tr


def crossover(a, b) -> np.ndarray:
    a, b = _f(a), _f(b)
    pa, pb = shift(a, 1), shift(b, 1)
    with np.errstate(invalid="ignore"):
        return (a > b) & (pa <= pb)


def crossunder(a, b) -> np.ndarray:
    a, b = _f(a), _f(b)
    pa, pb = shift(a, 1), shift(b, 1)
    with np.errstate(invalid="ignore"):
        return (a < b) & (pa >= pb)


# ------------------------------------------------------------------ momentum
def rsi(x, n: int = 14) -> np.ndarray:
    x = _f(x)
    d = np.diff(x, prepend=NAN)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    gain[0] = loss[0] = NAN
    ag = rma(gain, n)
    al = rma(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = ag / al
        out = 100.0 - 100.0 / (1.0 + rs)
    out = np.where((al == 0) & (ag > 0), 100.0, out)
    out = np.where((al == 0) & (ag == 0), 50.0, out)
    out[np.isnan(ag)] = NAN
    return out


def macd(x, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, np.ndarray]:
    if fast >= slow:
        raise ValueError("MACD fast length must be shorter than slow length")
    line = ema(x, fast) - ema(x, slow)
    sig = _nan_aware(lambda y: ema(y, signal), line)
    return {"macd": line, "signal": sig, "hist": line - sig}


def stoch(high, low, close, k: int = 14, d: int = 3, smooth: int = 3) -> dict[str, np.ndarray]:
    hh, ll = highest(high, k), lowest(low, k)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.where(hh - ll > 0, 100 * (_f(close) - ll) / (hh - ll), 50.0)
    raw[np.isnan(hh)] = NAN
    kk = _nan_aware(lambda y: sma(y, smooth), raw) if smooth > 1 else raw
    dd = _nan_aware(lambda y: sma(y, d), kk)
    return {"k": kk, "d": dd}


def stochrsi(x, rsi_len: int = 14, stoch_len: int = 14, k: int = 3, d: int = 3) -> dict[str, np.ndarray]:
    r = rsi(x, rsi_len)
    hh, ll = _nan_aware(lambda y: highest(y, stoch_len), r), _nan_aware(lambda y: lowest(y, stoch_len), r)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.where(hh - ll > 0, 100 * (r - ll) / (hh - ll), 50.0)
    raw[np.isnan(hh)] = NAN
    kk = _nan_aware(lambda y: sma(y, k), raw)
    return {"k": kk, "d": _nan_aware(lambda y: sma(y, d), kk)}


def cci(high, low, close, n: int = 20) -> np.ndarray:
    tp = (_f(high) + _f(low) + _f(close)) / 3.0
    ma = sma(tp, n)
    out = np.full(tp.shape, NAN)
    if len(tp) >= n:
        w = np.lib.stride_tricks.sliding_window_view(tp, n)
        md = np.abs(w - w.mean(axis=1, keepdims=True)).mean(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            out[n - 1:] = np.where(md > 0, (tp[n - 1:] - ma[n - 1:]) / (0.015 * md), 0.0)
    return out


def roc(x, n: int = 10) -> np.ndarray:
    prev = shift(x, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(prev != 0, 100 * (_f(x) - prev) / prev, NAN)


def momentum(x, n: int = 10) -> np.ndarray:
    return change(x, n)


def williams_r(high, low, close, n: int = 14) -> np.ndarray:
    hh, ll = highest(high, n), lowest(low, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(hh - ll > 0, -100 * (hh - _f(close)) / (hh - ll), -50.0)
    out[np.isnan(hh)] = NAN
    return out


def mfi(high, low, close, volume, n: int = 14) -> np.ndarray:
    tp = (_f(high) + _f(low) + _f(close)) / 3.0
    flow = tp * _f(volume)
    d = np.diff(tp, prepend=NAN)
    pos = np.where(d > 0, flow, 0.0)
    neg = np.where(d < 0, flow, 0.0)
    pos[0] = neg[0] = NAN
    ps = sma(pos, n) * n
    ns = sma(neg, n) * n
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + ps / ns)
    out = np.where((ns == 0) & (ps > 0), 100.0, out)
    out = np.where((ns == 0) & (ps == 0), 50.0, out)
    out[np.isnan(ps)] = NAN
    return out


# ------------------------------------------------------------------ volatility
def atr(high, low, close, n: int = 14) -> np.ndarray:
    tr = true_range(high, low, close)
    tr[0] = NAN  # first TR has no previous close; Wilder's ATR starts from the second bar
    return rma(tr, n)


def bbands(x, n: int = 20, k: float = 2.0) -> dict[str, np.ndarray]:
    mid = sma(x, n)
    sd = stdev(x, n)
    upper, lower = mid + k * sd, mid - k * sd
    with np.errstate(divide="ignore", invalid="ignore"):
        width = np.where(mid != 0, (upper - lower) / mid, NAN)
        pctb = np.where(upper - lower > 0, (_f(x) - lower) / (upper - lower), NAN)
    return {"mid": mid, "upper": upper, "lower": lower, "width": width, "pctb": pctb}


def keltner(high, low, close, n: int = 20, mult: float = 2.0, atr_len: int = 10) -> dict[str, np.ndarray]:
    mid = ema(close, n)
    a = atr(high, low, close, atr_len)
    return {"mid": mid, "upper": mid + mult * a, "lower": mid - mult * a}


def donchian(high, low, n: int = 20) -> dict[str, np.ndarray]:
    up, dn = highest(high, n), lowest(low, n)
    return {"upper": up, "lower": dn, "mid": (up + dn) / 2}


def hv(close, n: int = 20, periods_per_year: float = 365.0) -> np.ndarray:
    """Annualised historical volatility (%) from log returns."""
    c = _f(close)
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.log(c / shift(c, 1))
    return 100 * _nan_aware(lambda y: stdev(y, n, ddof=1), lr) * np.sqrt(periods_per_year)


# ------------------------------------------------------------------ volume
def obv(close, volume) -> np.ndarray:
    c, v = _f(close), _f(volume)
    d = np.sign(np.diff(c, prepend=c[0]))
    return np.cumsum(d * v)


def ad(high, low, close, volume) -> np.ndarray:
    h, lo, c, v = _f(high), _f(low), _f(close), _f(volume)
    with np.errstate(divide="ignore", invalid="ignore"):
        mfm = np.where(h - lo > 0, ((c - lo) - (h - c)) / (h - lo), 0.0)
    return np.cumsum(mfm * v)


def vwap(ts, high, low, close, volume, anchor: str = "D") -> np.ndarray:
    """Volume-weighted average price, reset at each UTC day ('D'), week ('W') or never ('none')."""
    tp = (_f(high) + _f(low) + _f(close)) / 3.0
    v = _f(volume)
    t = np.asarray(ts, dtype=np.int64)
    if anchor == "D":
        key = t // 86_400_000
    elif anchor == "W":
        key = (t - 4 * 86_400_000) // 604_800_000
    else:
        key = np.zeros_like(t)
    starts = np.r_[True, key[1:] != key[:-1]]
    group = np.cumsum(starts) - 1
    pv = tp * v
    cpv = np.cumsum(pv)
    cv = np.cumsum(v)
    first = np.flatnonzero(starts)
    base_pv = np.r_[0.0, cpv[first[1:] - 1]] if len(first) > 1 else np.array([0.0])
    base_v = np.r_[0.0, cv[first[1:] - 1]] if len(first) > 1 else np.array([0.0])
    num = cpv - base_pv[group]
    den = cv - base_v[group]
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den > 0, num / den, tp)


def volume_profile(high, low, close, volume, bins: int = 24) -> dict[str, list[float]]:
    """Volume distributed over price bins (each bar's volume split evenly across its range)."""
    h, lo, v = _f(high), _f(low), _f(volume)
    if len(h) == 0:
        return {"edges": [], "volume": []}
    pmin, pmax = float(np.nanmin(lo)), float(np.nanmax(h))
    if pmax <= pmin:
        return {"edges": [pmin, pmax], "volume": [float(np.nansum(v))]}
    edges = np.linspace(pmin, pmax, bins + 1)
    out = np.zeros(bins)
    for hi_, lo_, vol in zip(h, lo, v):
        if not np.isfinite(vol) or vol <= 0:
            continue
        a = np.searchsorted(edges, lo_, side="right") - 1
        b = np.searchsorted(edges, hi_, side="left")
        a, b = max(a, 0), min(max(b, a + 1), bins)
        out[a:b] += vol / (b - a)
    poc = int(np.argmax(out))
    return {"edges": edges.tolist(), "volume": out.tolist(), "poc_price": float((edges[poc] + edges[poc + 1]) / 2)}


# ------------------------------------------------------------------ trend systems
def supertrend(high, low, close, n: int = 10, mult: float = 3.0) -> dict[str, np.ndarray]:
    h, lo, c = _f(high), _f(low), _f(close)
    a = atr(h, lo, c, n)
    hl2 = (h + lo) / 2
    upper_basic, lower_basic = hl2 + mult * a, hl2 - mult * a
    m = len(c)
    upper = np.full(m, NAN)
    lower = np.full(m, NAN)
    line = np.full(m, NAN)
    direction = np.full(m, NAN)  # +1 up-trend, -1 down-trend
    start = np.flatnonzero(~np.isnan(a))
    if len(start) == 0:
        return {"line": line, "direction": direction}
    s = start[0]
    upper[s], lower[s] = upper_basic[s], lower_basic[s]
    direction[s] = 1.0
    line[s] = lower[s]
    for i in range(s + 1, m):
        upper[i] = upper_basic[i] if (upper_basic[i] < upper[i - 1] or c[i - 1] > upper[i - 1]) else upper[i - 1]
        lower[i] = lower_basic[i] if (lower_basic[i] > lower[i - 1] or c[i - 1] < lower[i - 1]) else lower[i - 1]
        if direction[i - 1] == 1.0:
            direction[i] = -1.0 if c[i] < lower[i] else 1.0
        else:
            direction[i] = 1.0 if c[i] > upper[i] else -1.0
        line[i] = lower[i] if direction[i] == 1.0 else upper[i]
    return {"line": line, "direction": direction}


def adx(high, low, close, n: int = 14) -> dict[str, np.ndarray]:
    h, lo, c = _f(high), _f(low), _f(close)
    up = np.diff(h, prepend=NAN)
    dn = -np.diff(lo, prepend=NAN)
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    plus_dm[0] = minus_dm[0] = NAN
    tr = true_range(h, lo, c)
    tr[0] = NAN
    atr_ = rma(tr, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * rma(plus_dm, n) / atr_
        mdi = 100 * rma(minus_dm, n) / atr_
        dx = np.where(pdi + mdi > 0, 100 * np.abs(pdi - mdi) / (pdi + mdi), 0.0)
    dx[np.isnan(pdi)] = NAN
    return {"adx": _nan_aware(lambda y: rma(y, n), dx), "plus_di": pdi, "minus_di": mdi}


def psar(high, low, step: float = 0.02, max_step: float = 0.2) -> np.ndarray:
    """Wilder's Parabolic SAR, following the TA-Lib reference algorithm.

    Initial direction comes from the first bar's directional movement; on a reversal the
    SAR is reset to the extreme point but kept outside today's and yesterday's range.
    """
    h, lo = _f(high), _f(low)
    m = len(h)
    out = np.full(m, NAN)
    if m < 2:
        return out
    up_move, down_move = h[1] - h[0], lo[0] - lo[1]
    is_long = not (down_move > 0 and down_move > up_move)
    af = step
    ep, sar = (h[1], lo[0]) if is_long else (lo[1], h[0])
    new_l, new_h = lo[1], h[1]
    for i in range(1, m):
        prev_l, prev_h = new_l, new_h
        new_l, new_h = lo[i], h[i]
        if is_long:
            if new_l <= sar:
                is_long, af = False, step
                sar = max(ep, prev_h, new_h)
                out[i] = sar
                ep = new_l
                sar = max(sar + af * (ep - sar), prev_h, new_h)
            else:
                out[i] = sar
                if new_h > ep:
                    ep, af = new_h, min(af + step, max_step)
                sar = min(sar + af * (ep - sar), prev_l, new_l)
        else:
            if new_h >= sar:
                is_long, af = True, step
                sar = min(ep, prev_l, new_l)
                out[i] = sar
                ep = new_h
                sar = min(sar + af * (ep - sar), prev_l, new_l)
            else:
                out[i] = sar
                if new_l < ep:
                    ep, af = new_l, min(af + step, max_step)
                sar = max(sar + af * (ep - sar), prev_h, new_h)
    return out


def ichimoku(high, low, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52, displacement: int = 26) -> dict[str, np.ndarray]:
    """Ichimoku lines aligned to the bar where they APPLY, computed only from past data.

    ``span_a``/``span_b`` at bar t were computed at t-displacement (the cloud the chart
    shows over bar t). Chikou (close plotted backwards) is omitted because aligning it to
    past bars would leak future data into those bars.
    """
    h, lo = _f(high), _f(low)
    t = (highest(h, tenkan) + lowest(lo, tenkan)) / 2
    k = (highest(h, kijun) + lowest(lo, kijun)) / 2
    a = (t + k) / 2
    b = (highest(h, senkou_b) + lowest(lo, senkou_b)) / 2
    return {"tenkan": t, "kijun": k, "span_a": shift(a, displacement), "span_b": shift(b, displacement)}
