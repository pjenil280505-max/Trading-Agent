"""Market-structure tools with explicit, non-repainting methodology.

A swing high at bar ``i`` (``high[i]`` greater than the ``left`` bars before and the
``right`` bars after) can only be *known* at bar ``i + right``. Every function here
reports structure at the bar where it becomes known, so strategies and alerts never
use information from the future.

Definitions
-----------
* **Swing high/low** — pivot with ``left``/``right`` strength, reported at confirmation.
* **BOS (break of structure)** — close beyond the most recent confirmed swing in the
  direction of the prevailing trend (continuation).
* **CHoCH (change of character)** — close beyond the most recent confirmed swing
  *against* the prevailing trend (first sign of reversal).
* **FVG (fair-value gap)** — three-bar imbalance: bullish when ``low[i] > high[i-2]``,
  bearish when ``high[i] < low[i-2]``; known at the close of bar ``i``.
* **Order block** — the last opposite-colour candle before the impulse that produced
  a BOS/CHoCH, reported at the break bar. A heuristic zone, not a guarantee.
* **Liquidity zone** — two or more confirmed swing highs (or lows) within
  ``tolerance`` of each other ("equal highs/lows").
* **Pivot points (classic)** — from the previous UTC day's high/low/close.
"""

from __future__ import annotations

from typing import Any

import numpy as np

NAN = np.nan


def swing_points(high, low, left: int = 3, right: int = 3) -> dict[str, np.ndarray]:
    """Arrays aligned to the CONFIRMATION bar: value = pivot price, NaN elsewhere.

    ``high_at`` / ``low_at`` hold the index of the pivot bar itself.
    """
    h, lo = np.asarray(high, float), np.asarray(low, float)
    n = len(h)
    sh, sl = np.full(n, NAN), np.full(n, NAN)
    sh_at, sl_at = np.full(n, -1), np.full(n, -1)
    # Strictly beyond the left bars, at least equal to the right bars: on a flat top the
    # FIRST bar of the plateau is the pivot (documented tie rule).
    for i in range(left, n - right):
        if h[i] > h[i - left:i].max() and h[i] >= h[i + 1:i + right + 1].max():
            sh[i + right], sh_at[i + right] = h[i], i
        if lo[i] < lo[i - left:i].min() and lo[i] <= lo[i + 1:i + right + 1].min():
            sl[i + right], sl_at[i + right] = lo[i], i
    return {"swing_high": sh, "swing_low": sl, "high_at": sh_at, "low_at": sl_at}


def market_structure(open_, high, low, close, left: int = 3, right: int = 3) -> dict[str, Any]:
    o, h, lo, c = (np.asarray(a, float) for a in (open_, high, low, close))
    n = len(c)
    sp = swing_points(h, lo, left, right)
    bos = np.zeros(n)     # +1 bullish BOS, -1 bearish BOS
    choch = np.zeros(n)   # +1 bullish CHoCH, -1 bearish CHoCH
    trend = np.zeros(n)
    events: list[dict[str, Any]] = []
    order_blocks: list[dict[str, Any]] = []
    last_high = last_low = NAN
    last_high_i = last_low_i = -1
    broken_high = broken_low = True
    cur = 0
    for i in range(n):
        if not np.isnan(sp["swing_high"][i]):
            last_high, last_high_i, broken_high = sp["swing_high"][i], int(sp["high_at"][i]), False
        if not np.isnan(sp["swing_low"][i]):
            last_low, last_low_i, broken_low = sp["swing_low"][i], int(sp["low_at"][i]), False
        if not broken_high and not np.isnan(last_high) and c[i] > last_high:
            kind = "BOS" if cur >= 0 else "CHoCH"
            (bos if kind == "BOS" else choch)[i] = 1
            cur = 1
            broken_high = True
            events.append({"bar": i, "type": kind, "direction": "bullish", "level": float(last_high), "pivot_bar": last_high_i})
            ob = _last_opposite(o, c, start=max(last_low_i, 0), end=i, want_bearish=True)
            if ob is not None:
                order_blocks.append({"bar": i, "direction": "bullish", "candle": ob, "top": float(h[ob]), "bottom": float(lo[ob])})
        elif not broken_low and not np.isnan(last_low) and c[i] < last_low:
            kind = "BOS" if cur <= 0 else "CHoCH"
            (bos if kind == "BOS" else choch)[i] = -1
            cur = -1
            broken_low = True
            events.append({"bar": i, "type": kind, "direction": "bearish", "level": float(last_low), "pivot_bar": last_low_i})
            ob = _last_opposite(o, c, start=max(last_high_i, 0), end=i, want_bearish=False)
            if ob is not None:
                order_blocks.append({"bar": i, "direction": "bearish", "candle": ob, "top": float(h[ob]), "bottom": float(lo[ob])})
        trend[i] = cur
    return {"bos": bos, "choch": choch, "trend": trend, "events": events, "order_blocks": order_blocks,
            "swing_high": sp["swing_high"], "swing_low": sp["swing_low"]}


def _last_opposite(o, c, *, start: int, end: int, want_bearish: bool) -> int | None:
    for j in range(end - 1, start - 1, -1):
        if (c[j] < o[j]) if want_bearish else (c[j] > o[j]):
            return j
    return None


def fair_value_gaps(high, low) -> dict[str, Any]:
    h, lo = np.asarray(high, float), np.asarray(low, float)
    n = len(h)
    bull, bear = np.zeros(n), np.zeros(n)
    zones = []
    for i in range(2, n):
        if lo[i] > h[i - 2]:
            bull[i] = 1
            zones.append({"bar": i, "direction": "bullish", "top": float(lo[i]), "bottom": float(h[i - 2])})
        elif h[i] < lo[i - 2]:
            bear[i] = 1
            zones.append({"bar": i, "direction": "bearish", "top": float(lo[i - 2]), "bottom": float(h[i])})
    return {"bullish": bull, "bearish": bear, "zones": zones}


def liquidity_zones(high, low, left: int = 3, right: int = 3, tolerance: float = 0.001) -> list[dict[str, Any]]:
    sp = swing_points(high, low, left, right)
    zones = []
    for key, kind in (("swing_high", "equal_highs"), ("swing_low", "equal_lows")):
        idx = np.flatnonzero(~np.isnan(sp[key]))
        prices = sp[key][idx]
        used = set()
        for a in range(len(idx)):
            if a in used:
                continue
            group = [a]
            for b in range(a + 1, len(idx)):
                if b not in used and abs(prices[b] - prices[a]) <= tolerance * prices[a]:
                    group.append(b)
            if len(group) >= 2:
                used.update(group)
                zones.append({"type": kind, "price": float(np.mean(prices[group])),
                              "touches": len(group), "confirmed_bars": [int(idx[g]) for g in group]})
    return zones


def support_resistance(high, low, close, left: int = 5, right: int = 5, max_levels: int = 6,
                       cluster: float = 0.005) -> list[dict[str, Any]]:
    """Cluster confirmed swing levels; rank by touches. Levels are as of the last bar."""
    sp = swing_points(high, low, left, right)
    levels = np.r_[sp["swing_high"][~np.isnan(sp["swing_high"])], sp["swing_low"][~np.isnan(sp["swing_low"])]]
    if len(levels) == 0:
        return []
    levels.sort()
    clusters: list[list[float]] = [[levels[0]]]
    for p in levels[1:]:
        if abs(p - np.mean(clusters[-1])) <= cluster * p:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    last = float(np.asarray(close, float)[-1])
    out = [{"price": float(np.mean(cl)), "touches": len(cl),
            "kind": "resistance" if np.mean(cl) > last else "support"} for cl in clusters]
    out.sort(key=lambda d: (-d["touches"], abs(d["price"] - last)))
    return out[:max_levels]


def pivot_points(ts, high, low, close) -> dict[str, np.ndarray]:
    """Classic floor pivots from the PREVIOUS UTC day, aligned to every bar of the current day."""
    t = np.asarray(ts, np.int64)
    h, lo, c = (np.asarray(a, float) for a in (high, low, close))
    day = t // 86_400_000
    n = len(t)
    keys = {k: np.full(n, NAN) for k in ("p", "r1", "s1", "r2", "s2", "r3", "s3")}
    uniq, first = np.unique(day, return_index=True)
    bounds = list(first) + [n]
    prev = None
    for k in range(len(uniq)):
        a, b = bounds[k], bounds[k + 1]
        if prev is not None:
            ph, pl, pc = prev
            p = (ph + pl + pc) / 3
            vals = {"p": p, "r1": 2 * p - pl, "s1": 2 * p - ph, "r2": p + (ph - pl), "s2": p - (ph - pl),
                    "r3": ph + 2 * (p - pl), "s3": pl - 2 * (ph - p)}
            for name, v in vals.items():
                keys[name][a:b] = v
        prev = (h[a:b].max(), lo[a:b].min(), c[b - 1])
    return keys
