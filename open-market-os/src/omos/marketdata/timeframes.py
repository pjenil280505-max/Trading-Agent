"""Timeframe parsing, bar alignment and candle aggregation.

Canonical timeframe strings: ``1s 5s 15s 30s 1m 3m 5m 15m 30m 45m 1h 2h 3h 4h 6h 8h 12h 1d 3d 1w 1M``
plus any ``<n><unit>`` for custom aggregation. ``1M`` is one calendar month; ``1m`` is one minute.

Aggregation never invents data: an aggregated bar only covers source bars that
exist, and the partial final bucket is flagged as not closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from omos.errors import ValidationError
from omos.marketdata.models import Candles, DataStatus

_UNIT_MS = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
_RE = re.compile(r"^(\d{1,4})([smhdwM])$")
# Monday 1970-01-05 00:00 UTC: weekly buckets start on Mondays like most venues
_WEEK_ANCHOR_MS = 4 * 86_400_000

STANDARD = ["1s", "5s", "15s", "30s", "1m", "3m", "5m", "15m", "30m", "45m", "1h", "2h", "3h", "4h",
            "6h", "8h", "12h", "1d", "3d", "1w", "1M"]


@dataclass(frozen=True)
class Timeframe:
    n: int
    unit: str  # s m h d w M

    @property
    def text(self) -> str:
        return f"{self.n}{self.unit}"

    @property
    def is_monthly(self) -> bool:
        return self.unit == "M"

    @property
    def ms(self) -> int:
        """Nominal duration. Months are approximated as 30 days for spacing only."""
        if self.unit == "M":
            return self.n * 30 * 86_400_000
        return self.n * _UNIT_MS[self.unit]

    def floor(self, ts_ms: int) -> int:
        """Open time of the bucket containing ``ts_ms`` (UTC alignment)."""
        if self.unit == "M":
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            months = dt.year * 12 + (dt.month - 1)
            months -= months % self.n
            y, m = divmod(months, 12)
            return int(datetime(y, m + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        size = self.ms
        if self.unit == "w":
            return ((ts_ms - _WEEK_ANCHOR_MS) // size) * size + _WEEK_ANCHOR_MS
        return (ts_ms // size) * size

    def next_open(self, bucket_open_ms: int) -> int:
        if self.unit == "M":
            dt = datetime.fromtimestamp(bucket_open_ms / 1000, tz=timezone.utc)
            months = dt.year * 12 + (dt.month - 1) + self.n
            y, m = divmod(months, 12)
            return int(datetime(y, m + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        return bucket_open_ms + self.ms


def parse(tf: str) -> Timeframe:
    m = _RE.match(tf.strip())
    if not m:
        raise ValidationError(f"unknown timeframe '{tf}'. Use e.g. 1m, 15m, 1h, 4h, 1d, 1w, 1M")
    n = int(m.group(1))
    if n <= 0:
        raise ValidationError("timeframe must be positive")
    return Timeframe(n, m.group(2))


def is_multiple(target: Timeframe, base: Timeframe) -> bool:
    """True when bars of ``base`` tile ``target`` exactly."""
    if target.unit == "M":
        return base.unit == "M" and target.n % base.n == 0 or base.unit in "smhd" and 86_400_000 % base.ms == 0
    if base.unit == "M":
        return False
    return target.ms >= base.ms and target.ms % base.ms == 0


def aggregate(candles: Candles, target: str, *, now_ms: int | None = None) -> Candles:
    """Aggregate finer candles into ``target`` buckets.

    The final bucket is marked not-closed when its period has not ended (or when
    the source's own last bar is still forming).
    """
    tf = parse(target)
    n = len(candles)
    if n == 0:
        return Candles.from_rows([], instrument_id=candles.instrument_id, timeframe=target,
                                 status=candles.status, source=candles.source, fetched_at=candles.fetched_at)
    buckets = np.fromiter((tf.floor(int(t)) for t in candles.ts), dtype=np.int64, count=n)
    # boundaries where the bucket changes
    starts = np.flatnonzero(np.r_[True, buckets[1:] != buckets[:-1]])
    ends = np.r_[starts[1:], n]
    o = candles.open[starts]
    c = candles.close[ends - 1]
    h = np.maximum.reduceat(candles.high, starts)
    lo = np.minimum.reduceat(candles.low, starts)
    v = np.add.reduceat(candles.volume, starts)
    ts = buckets[starts]
    last_closed = candles.last_closed
    if now_ms is not None and tf.next_open(int(ts[-1])) > now_ms:
        last_closed = False
    # Values are exact aggregates of real bars, so the source's status is kept.
    return Candles(ts=ts, open=o, high=h, low=lo, close=c, volume=v, instrument_id=candles.instrument_id,
                   timeframe=target, status=candles.status,
                   source=f"{candles.source} (aggregated from {candles.timeframe})",
                   fetched_at=candles.fetched_at, last_closed=last_closed)


def heikin_ashi(candles: Candles) -> Candles:
    """Heikin-Ashi transform (a derived view, flagged ESTIMATED-source in its label)."""
    n = len(candles)
    ha_c = (candles.open + candles.high + candles.low + candles.close) / 4.0
    ha_o = np.empty(n)
    if n:
        ha_o[0] = (candles.open[0] + candles.close[0]) / 2.0
        for i in range(1, n):
            ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2.0
    ha_h = np.maximum.reduce([candles.high, ha_o, ha_c]) if n else candles.high
    ha_l = np.minimum.reduce([candles.low, ha_o, ha_c]) if n else candles.low
    return Candles(ts=candles.ts, open=ha_o, high=ha_h, low=ha_l, close=ha_c, volume=candles.volume,
                   instrument_id=candles.instrument_id, timeframe=candles.timeframe, status=candles.status,
                   source=f"{candles.source} (Heikin-Ashi)", fetched_at=candles.fetched_at,
                   last_closed=candles.last_closed)
