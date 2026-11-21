"""Candle validation, de-duplication, gap detection and a data-quality score.

Invalid bars are dropped and *counted*, never repaired with invented values.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from omos.marketdata.models import Candles
from omos.marketdata.sessions import get_session
from omos.marketdata.timeframes import parse


def clean(candles: Candles, *, session_id: str = "24x7", now_ms: int | None = None) -> Candles:
    """Return a sorted, de-duplicated, validated copy with a ``quality`` report attached."""
    n0 = len(candles)
    report: dict[str, Any] = {"input_bars": n0}
    if n0 == 0:
        candles.quality = {**report, "score": 0.0, "bars": 0, "issues": ["no data"]}
        return candles

    order = np.argsort(candles.ts, kind="stable")
    ts = candles.ts[order]
    o, h, lo, c, v = (a[order] for a in (candles.open, candles.high, candles.low, candles.close, candles.volume))

    # duplicates: keep the LAST occurrence of each timestamp (the freshest update)
    rev_unique = np.unique(ts[::-1], return_index=True)[1]
    keep = np.sort(len(ts) - 1 - rev_unique)
    dupes = len(ts) - len(keep)
    ts, o, h, lo, c, v = (a[keep] for a in (ts, o, h, lo, c, v))

    finite = np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) & np.isfinite(c) & np.isfinite(v)
    positive = (o > 0) & (h > 0) & (lo > 0) & (c > 0) & (v >= 0)
    consistent = (h >= np.maximum(o, c) - 1e-12 * np.abs(h)) & (lo <= np.minimum(o, c) + 1e-12 * np.abs(lo)) & (h >= lo)
    valid = finite & positive & consistent
    invalid = int((~valid).sum())
    ts, o, h, lo, c, v = (a[valid] for a in (ts, o, h, lo, c, v))

    future = 0
    if now_ms is not None:
        not_future = ts <= now_ms + 60_000
        future = int((~not_future).sum())
        ts, o, h, lo, c, v = (a[not_future] for a in (ts, o, h, lo, c, v))

    out = Candles(ts=ts, open=o, high=h, low=lo, close=c, volume=v, instrument_id=candles.instrument_id,
                  timeframe=candles.timeframe, status=candles.status, source=candles.source,
                  fetched_at=candles.fetched_at, last_closed=candles.last_closed)
    gaps = find_gaps(out, session_id=session_id)
    missing = sum(g["missing_bars"] for g in gaps)
    issues = []
    if dupes:
        issues.append(f"{dupes} duplicate bar(s) removed")
    if invalid:
        issues.append(f"{invalid} invalid bar(s) dropped (non-finite, non-positive or high<low)")
    if future:
        issues.append(f"{future} bar(s) with future timestamps dropped")
    if missing:
        issues.append(f"{missing} expected bar(s) missing across {len(gaps)} gap(s)")
    expected = len(out) + missing
    completeness = len(out) / expected if expected else 0.0
    penalty = (invalid + future) / max(n0, 1)
    score = round(max(0.0, completeness - penalty) * 100, 1)
    out.quality = {
        **report,
        "bars": len(out),
        "duplicates_removed": dupes,
        "invalid_dropped": invalid,
        "future_dropped": future,
        "gaps": gaps[:50],
        "gap_count": len(gaps),
        "missing_bars": missing,
        "score": score,
        "issues": issues,
    }
    return out


def find_gaps(candles: Candles, *, session_id: str = "24x7", max_report: int = 500) -> list[dict[str, Any]]:
    """Report runs of expected-but-missing bars.

    For session-bound markets, a missing slot only counts when the session was
    open at that time (overnight and weekend pauses are not gaps). Holidays are
    not modelled, so they are reported as gaps — honestly, rather than hidden.
    """
    if len(candles) < 2 or not candles.timeframe:
        return []
    tf = parse(candles.timeframe)
    if tf.unit in ("d", "w", "M"):
        if session_id != "24x7":
            return []  # daily bars for session markets skip weekends/holidays; not judged here
    step = tf.ms
    session = get_session(session_id)
    gaps: list[dict[str, Any]] = []
    diffs = np.diff(candles.ts)
    for i in np.flatnonzero(diffs > step * 1.5):
        a, b = int(candles.ts[i]), int(candles.ts[i + 1])
        if tf.unit == "M":
            continue
        slots = range(a + step, b, step)
        if session.id == "24x7":
            missing = max(0, math.floor((b - a) / step) - 1)
        else:
            missing = sum(1 for s in slots if session.is_open(s))
        if missing > 0:
            gaps.append({"after": a, "before": b, "missing_bars": int(missing)})
            if len(gaps) >= max_report:
                break
    return gaps
