"""Market-data value types shared by every provider and consumer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np


class DataStatus(str, Enum):
    """How fresh/real a piece of data is. Shown to the user on every chart."""

    LIVE = "LIVE"              # streamed from the venue's live feed right now
    REALTIME = "REALTIME"      # polled from the venue with no stated delay (shown as LIVE with its poll interval)
    DELAYED = "DELAYED"        # the source states or is known to impose a delay
    HISTORICAL = "HISTORICAL"  # end-of-day or archival data, not live
    SIMULATED = "SIMULATED"    # generated; never real prices (test/demo mode only)
    ESTIMATED = "ESTIMATED"    # derived/aggregated by us from other data
    STALE = "STALE"            # was live, but no update arrived for too long: not safe to trade on
    UNAVAILABLE = "UNAVAILABLE"


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"
    FOREX = "forex"
    COMMODITY = "commodity"
    FUTURE = "future"
    OPTION = "option"
    BOND = "bond"
    RATE = "rate"
    ECONOMIC = "economic"
    FUND = "fund"


@dataclass
class Instrument:
    id: str                    # "<PROVIDER>:<provider symbol>" — never reused across venues
    provider: str
    provider_symbol: str
    asset_class: AssetClass
    name: str = ""
    exchange: str = ""
    base: str = ""
    quote: str = ""
    currency: str = ""
    timezone: str = "UTC"
    session: str = "24x7"      # session calendar id, see sessions.py
    tick_size: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "provider_symbol": self.provider_symbol,
            "asset_class": self.asset_class.value,
            "name": self.name,
            "exchange": self.exchange,
            "base": self.base,
            "quote": self.quote,
            "currency": self.currency,
            "timezone": self.timezone,
            "session": self.session,
            "tick_size": self.tick_size,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Instrument:
        return cls(
            id=d["id"],
            provider=d["provider"],
            provider_symbol=d["provider_symbol"],
            asset_class=AssetClass(d["asset_class"]),
            name=d.get("name") or "",
            exchange=d.get("exchange") or "",
            base=d.get("base") or "",
            quote=d.get("quote") or "",
            currency=d.get("currency") or "",
            timezone=d.get("timezone") or "UTC",
            session=d.get("session") or "24x7",
            tick_size=d.get("tick_size"),
            meta=d.get("meta") or {},
        )


@dataclass
class Candles:
    """A column-oriented OHLCV series. ``ts`` is the bar open time in UTC ms."""

    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    instrument_id: str = ""
    timeframe: str = ""
    status: DataStatus = DataStatus.HISTORICAL
    source: str = ""
    fetched_at: int = 0
    last_closed: bool = True   # False when the final bar is still forming
    quality: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.ts.shape[0])

    @classmethod
    def from_rows(cls, rows: list[list[float]] | list[tuple], **meta: Any) -> Candles:
        if not rows:
            empty = np.empty(0, dtype=np.float64)
            return cls(np.empty(0, dtype=np.int64), empty, empty.copy(), empty.copy(), empty.copy(), empty.copy(), **meta)
        arr = np.asarray(rows, dtype=np.float64)
        return cls(
            ts=arr[:, 0].astype(np.int64),
            open=arr[:, 1],
            high=arr[:, 2],
            low=arr[:, 3],
            close=arr[:, 4],
            volume=arr[:, 5] if arr.shape[1] > 5 else np.zeros(len(arr)),
            **meta,
        )

    def rows(self) -> list[list[float]]:
        return [
            [int(t), float(o), float(h), float(lo), float(c), float(v)]
            for t, o, h, lo, c, v in zip(self.ts, self.open, self.high, self.low, self.close, self.volume)
        ]

    def slice(self, start: int, stop: int | None = None) -> Candles:
        s = slice(start, stop)
        return Candles(
            self.ts[s], self.open[s], self.high[s], self.low[s], self.close[s], self.volume[s],
            instrument_id=self.instrument_id, timeframe=self.timeframe, status=self.status,
            source=self.source, fetched_at=self.fetched_at, last_closed=self.last_closed, quality=self.quality,
        )

    def meta_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "timeframe": self.timeframe,
            "status": self.status.value,
            "source": self.source,
            "fetched_at": self.fetched_at,
            "last_closed": self.last_closed,
            "quality": self.quality,
            "count": len(self),
        }


@dataclass
class Quote:
    instrument_id: str
    price: float
    ts: int
    status: DataStatus
    source: str
    bid: float | None = None
    ask: float | None = None
    change_pct_24h: float | None = None
    volume_24h: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "price": self.price,
            "ts": self.ts,
            "status": self.status.value,
            "source": self.source,
            "bid": self.bid,
            "ask": self.ask,
            "change_pct_24h": self.change_pct_24h,
            "volume_24h": self.volume_24h,
        }
