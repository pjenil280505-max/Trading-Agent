"""Economic calendar.

What is available at zero cost, honestly:
* ACTUAL and PREVIOUS values for key US indicators, computed from FRED series.
* Upcoming release DATES only with a free FRED API key (api.stlouisfed.org); without a key the
  schedule is reported as unavailable.
* FORECAST/consensus values are not available from any free, licensed source we could find,
  so they are always reported as unavailable — never invented.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from omos.errors import OmosError
from omos.marketdata.service import MarketDataService
from omos.security.secrets import SecretStore
from omos.storage.db import Database

EVENTS = [
    {"series": "CPIAUCSL", "event": "Consumer Price Index (CPI, YoY %)", "country": "US", "importance": "high",
     "transform": "yoy", "release_id": 10},
    {"series": "UNRATE", "event": "Unemployment rate (%)", "country": "US", "importance": "high", "transform": "level",
     "release_id": 50},
    {"series": "PAYEMS", "event": "Nonfarm payrolls (change, thousands)", "country": "US", "importance": "high",
     "transform": "diff", "release_id": 50},
    {"series": "FEDFUNDS", "event": "Effective Fed funds rate (%)", "country": "US", "importance": "high",
     "transform": "level", "release_id": None},
    {"series": "GDPC1", "event": "Real GDP (QoQ annualised %)", "country": "US", "importance": "high",
     "transform": "qoq_ann", "release_id": 53},
    {"series": "PCEPI", "event": "PCE price index (YoY %)", "country": "US", "importance": "medium",
     "transform": "yoy", "release_id": 54},
    {"series": "ICSA", "event": "Initial jobless claims", "country": "US", "importance": "medium",
     "transform": "level", "release_id": 180},
    {"series": "RSAFS", "event": "Retail sales (MoM %)", "country": "US", "importance": "medium",
     "transform": "mom", "release_id": 9},
    {"series": "UMCSENT", "event": "UMich consumer sentiment", "country": "US", "importance": "medium",
     "transform": "level", "release_id": 91},
    {"series": "HOUST", "event": "Housing starts (thousands)", "country": "US", "importance": "low",
     "transform": "level", "release_id": 27},
]


def _transform(values: list[float], kind: str) -> list[float | None]:
    out: list[float | None] = []
    for i, v in enumerate(values):
        if kind == "level":
            out.append(v)
        elif kind == "diff":
            out.append(v - values[i - 1] if i >= 1 else None)
        elif kind == "mom":
            out.append((v / values[i - 1] - 1) * 100 if i >= 1 and values[i - 1] else None)
        elif kind == "yoy":
            out.append((v / values[i - 12] - 1) * 100 if i >= 12 and values[i - 12] else None)
        elif kind == "qoq_ann":
            out.append(((v / values[i - 1]) ** 4 - 1) * 100 if i >= 1 and values[i - 1] else None)
    return out


class CalendarService:
    def __init__(self, db: Database, market: MarketDataService, secrets: SecretStore):
        self.db = db
        self.market = market
        self.secrets = secrets

    def events(self) -> dict[str, Any]:
        rows = []
        for ev in EVENTS:
            row = {**ev, "forecast": None, "forecast_note": "no free licensed source for consensus forecasts"}
            try:
                c = self.market.get_candles(f"FRED:{ev['series']}", "1d", limit=400, max_age_s=6 * 3600)
                vals = [float(x) for x in c.close]
                tr = _transform(vals, ev["transform"])
                if tr and tr[-1] is not None:
                    row.update(actual=round(tr[-1], 4), previous=round(tr[-2], 4) if len(tr) > 1 and tr[-2] is not None else None,
                               period=datetime.fromtimestamp(int(c.ts[-1]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                               source="FRED", status=c.status.value)
            except OmosError as exc:
                row.update(actual=None, previous=None, error=exc.message)
            rows.append(row)
        schedule = self.schedule()
        return {"events": rows, "schedule": schedule}

    def schedule(self) -> dict[str, Any]:
        key = None
        try:
            key = self.secrets.get("fred.api_key")
        except OmosError:
            key = None
        if not key:
            return {"available": False, "message": "Add a free FRED API key (Settings > Data sources) to see upcoming "
                                                   "release dates. Forecasts are not available from free sources."}
        cached = self.db.cache_get("fred:release_dates")
        if cached is not None:
            return cached
        try:
            r = httpx.get("https://api.stlouisfed.org/fred/releases/dates",
                          params={"api_key": key, "file_type": "json", "include_release_dates_with_no_data": "true",
                                  "realtime_start": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "limit": 200,
                                  "sort_order": "asc"}, timeout=20)
            r.raise_for_status()
            wanted = {e["release_id"]: e["event"] for e in EVENTS if e["release_id"]}
            dates = [{"date": d["date"], "release": d.get("release_name"), "event": wanted.get(d.get("release_id"))}
                     for d in r.json().get("release_dates", []) if d.get("release_id") in wanted]
            out = {"available": True, "upcoming": dates[:60], "source": "FRED API"}
            self.db.cache_set("fred:release_dates", out, ttl_s=6 * 3600)
            return out
        except (httpx.HTTPError, ValueError) as exc:
            return {"available": False, "message": f"FRED API request failed: {type(exc).__name__}"}
