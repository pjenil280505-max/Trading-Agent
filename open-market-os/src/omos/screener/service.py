"""Market screener over a user-chosen universe.

Coverage is exactly the instruments in the universe whose data could be loaded; the
response lists every instrument that failed so the screen never implies wider coverage
than it has.

Definition::

    {"universe": {"watchlist_id": 1} | {"instruments": ["OKX:BTC-USDT", ...]},
     "timeframe": "1d",
     "filter": {"op": "and", "rules": [
         {"field": "rsi14", "cmp": "<", "value": 30},
         {"expr": "close > sma(close, 200)"},
         {"op": "or", "rules": [...]}]},
     "sort": {"field": "change_pct", "desc": true}}
"""

from __future__ import annotations

import csv
import io
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from omos.errors import NotFound, OmosError, ValidationError
from omos.indicators import core as ta
from omos.marketdata.service import MarketDataService
from omos.script.lang import evaluate_expression, parse
from omos.storage.db import Database
from omos.util import dumps, loads, now_ms

MAX_UNIVERSE = 200
CMPS = {"<": np.less, "<=": np.less_equal, ">": np.greater, ">=": np.greater_equal, "==": np.equal, "!=": np.not_equal}

FIELDS = {
    "price": "last close",
    "change_pct": "% change over the last bar",
    "change_pct_5": "% change over 5 bars",
    "change_pct_20": "% change over 20 bars",
    "volume": "last bar volume",
    "avg_volume_20": "20-bar average volume",
    "rel_volume": "volume / 20-bar average",
    "rsi14": "RSI(14)",
    "sma20": "SMA(20)", "sma50": "SMA(50)", "sma200": "SMA(200)",
    "dist_sma50_pct": "% distance from SMA(50)",
    "dist_sma200_pct": "% distance from SMA(200)",
    "atr_pct": "ATR(14) as % of price",
    "hv20": "20-bar historical volatility (%/yr, 365-day basis)",
    "macd_hist": "MACD(12,26,9) histogram",
    "adx14": "ADX(14)",
    "high_52w_pct": "% below the 252-bar high",
}


def compute_fields(c) -> dict[str, float | None]:
    close, n = c.close, len(c)

    def last(a: np.ndarray) -> float | None:
        return float(a[-1]) if len(a) and np.isfinite(a[-1]) else None

    def chg(k: int) -> float | None:
        return (float(close[-1]) / float(close[-1 - k]) - 1) * 100 if n > k and close[-1 - k] > 0 else None

    sma50, sma200 = ta.sma(close, 50), ta.sma(close, 200)
    vol = c.volume.astype(float)
    avg_vol = ta.sma(vol, 20)
    atr = ta.atr(c.high, c.low, close, 14)
    out = {
        "price": last(close), "change_pct": chg(1), "change_pct_5": chg(5), "change_pct_20": chg(20),
        "volume": last(vol), "avg_volume_20": last(avg_vol),
        "rel_volume": (float(vol[-1]) / float(avg_vol[-1])) if n >= 20 and avg_vol[-1] > 0 else None,
        "rsi14": last(ta.rsi(close, 14)), "sma20": last(ta.sma(close, 20)), "sma50": last(sma50), "sma200": last(sma200),
        "dist_sma50_pct": (float(close[-1]) / float(sma50[-1]) - 1) * 100 if n >= 50 and np.isfinite(sma50[-1]) else None,
        "dist_sma200_pct": (float(close[-1]) / float(sma200[-1]) - 1) * 100 if n >= 200 and np.isfinite(sma200[-1]) else None,
        "atr_pct": float(atr[-1]) / float(close[-1]) * 100 if n > 15 and np.isfinite(atr[-1]) else None,
        "hv20": last(ta.hv(close, 20)), "macd_hist": last(ta.macd(close)["hist"]) if n > 40 else None,
        "adx14": last(ta.adx(c.high, c.low, close, 14)["adx"]) if n > 40 else None,
        "high_52w_pct": (float(close[-1]) / float(np.max(c.high[-252:])) - 1) * 100 if n else None,
    }
    return out


class ScreenerService:
    def __init__(self, db: Database, market: MarketDataService):
        self.db = db
        self.market = market

    def validate(self, definition: dict[str, Any]) -> None:
        uni = definition.get("universe") or {}
        if not uni.get("watchlist_id") and not uni.get("instruments"):
            raise ValidationError("choose a watchlist or list of instruments as the universe")
        self._validate_filter(definition.get("filter") or {"op": "and", "rules": []}, depth=0)

    def _validate_filter(self, f: dict[str, Any], depth: int) -> None:
        if depth > 5:
            raise ValidationError("filter groups are nested too deeply (max 5)")
        if f.get("op", "and") not in ("and", "or"):
            raise ValidationError("filter op must be 'and' or 'or'")
        rules = f.get("rules", [])
        if len(rules) > 50:
            raise ValidationError("too many filter rules (max 50)")
        for r in rules:
            if "rules" in r:
                self._validate_filter(r, depth + 1)
            elif "expr" in r:
                if len(r["expr"]) > 1000:
                    raise ValidationError("formula too long")
                parse(f"__v = {r['expr']}\n")
            else:
                if r.get("field") not in FIELDS:
                    raise ValidationError(f"unknown field '{r.get('field')}'")
                if r.get("cmp") not in CMPS:
                    raise ValidationError(f"unknown comparison '{r.get('cmp')}'")
                if not isinstance(r.get("value"), int | float):
                    raise ValidationError("rule value must be a number")

    def universe(self, definition: dict[str, Any]) -> list[str]:
        uni = definition.get("universe") or {}
        if uni.get("watchlist_id"):
            rows = self.db.query("SELECT instrument_id FROM watchlist_items WHERE watchlist_id = ? ORDER BY position",
                                 (int(uni["watchlist_id"]),))
            ids = [r["instrument_id"] for r in rows]
        else:
            ids = list(dict.fromkeys(uni.get("instruments") or []))
        if len(ids) > MAX_UNIVERSE:
            raise ValidationError(f"universe has {len(ids)} instruments; the maximum is {MAX_UNIVERSE}")
        return ids

    def _match(self, f: dict[str, Any], fields: dict[str, Any], candles) -> bool:
        results = []
        for r in f.get("rules", []):
            if "rules" in r:
                results.append(self._match(r, fields, candles))
            elif "expr" in r:
                v = evaluate_expression(r["expr"], candles)
                results.append(bool(len(v) and v[-1]))
            else:
                val = fields.get(r["field"])
                results.append(val is not None and bool(CMPS[r["cmp"]](val, r["value"])))
        if not results:
            return True
        return all(results) if f.get("op", "and") == "and" else any(results)

    def run(self, definition: dict[str, Any], *, max_age_s: float = 300) -> dict[str, Any]:
        self.validate(definition)
        tf = definition.get("timeframe", "1d")
        flt = definition.get("filter") or {"op": "and", "rules": []}
        ids = self.universe(definition)
        rows: list[dict[str, Any]] = []
        errors: dict[str, str] = {}

        def one(iid: str):
            try:
                c = self.market.get_candles(iid, tf, limit=300, max_age_s=max_age_s)
                if len(c) < 2:
                    return iid, None, "not enough data"
                fields = compute_fields(c)
                return iid, {"instrument_id": iid, **fields, "matched": self._match(flt, fields, c),
                             "data_status": c.status.value, "bars": len(c)}, None
            except OmosError as exc:
                return iid, None, exc.message

        with ThreadPoolExecutor(max_workers=4) as pool:
            for iid, row, err in pool.map(one, ids):
                if err:
                    errors[iid] = err
                else:
                    rows.append(row)
        matched = [r for r in rows if r["matched"]]
        sort = definition.get("sort") or {}
        if sort.get("field") in FIELDS:
            matched.sort(key=lambda r: (r.get(sort["field"]) is None,
                                        -(r.get(sort["field"]) or 0) if sort.get("desc", True) else (r.get(sort["field"]) or 0)))
        return {"timeframe": tf, "universe_size": len(ids), "scanned": len(rows), "matched": len(matched),
                "results": matched, "errors": errors, "ran_at": now_ms(),
                "coverage_note": f"Scanned {len(rows)} of {len(ids)} instruments in your universe; "
                                 f"{len(errors)} could not be loaded. Nothing outside this universe was screened."}

    # -- saved screens -------------------------------------------------------------------
    def save(self, name: str, definition: dict[str, Any], schedule_minutes: int | None = None) -> dict[str, Any]:
        self.validate(definition)
        if schedule_minutes is not None and not 5 <= schedule_minutes <= 1440:
            raise ValidationError("schedule must be between 5 and 1440 minutes")
        self.db.execute("INSERT INTO screeners(name, definition, schedule_minutes, updated_at) VALUES (?,?,?,?) "
                        "ON CONFLICT(name) DO UPDATE SET definition = excluded.definition, "
                        "schedule_minutes = excluded.schedule_minutes, updated_at = excluded.updated_at",
                        (name.strip()[:80], dumps(definition), schedule_minutes, now_ms()))
        return self.get_by_name(name.strip()[:80])

    def get(self, screener_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM screeners WHERE id = ?", (screener_id,))
        if not row:
            raise NotFound(f"screener {screener_id} not found")
        row["definition"] = loads(row["definition"], {})
        return row

    def get_by_name(self, name: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT id FROM screeners WHERE name = ?", (name,))
        if not row:
            raise NotFound(f"screener '{name}' not found")
        return self.get(row["id"])

    def list(self) -> list[dict[str, Any]]:
        return [self.get(r["id"]) for r in self.db.query("SELECT id FROM screeners ORDER BY name")]

    def duplicate(self, screener_id: int, new_name: str) -> dict[str, Any]:
        s = self.get(screener_id)
        return self.save(new_name, s["definition"], s["schedule_minutes"])

    def delete(self, screener_id: int) -> None:
        self.db.execute("DELETE FROM screeners WHERE id = ?", (screener_id,))

    def run_saved(self, screener_id: int) -> dict[str, Any]:
        s = self.get(screener_id)
        result = self.run(s["definition"])
        self.db.execute("INSERT INTO screener_runs(screener_id, ts, results) VALUES (?,?,?)",
                        (screener_id, now_ms(), dumps({"matched": [r["instrument_id"] for r in result["results"]],
                                                       "errors": result["errors"]})))
        self.db.execute("UPDATE screeners SET last_run_at = ? WHERE id = ?", (now_ms(), screener_id))
        return result

    def due(self) -> list[int]:
        out = []
        for s in self.db.query("SELECT id, schedule_minutes, last_run_at FROM screeners WHERE schedule_minutes IS NOT NULL"):
            if not s["last_run_at"] or now_ms() - s["last_run_at"] >= s["schedule_minutes"] * 60_000:
                out.append(s["id"])
        return out

    @staticmethod
    def to_csv(result: dict[str, Any]) -> str:
        buf = io.StringIO()
        cols = ["instrument_id", *FIELDS, "data_status"]
        w = csv.writer(buf)
        w.writerow(cols)
        for r in result["results"]:
            w.writerow(["" if r.get(c) is None else (round(r[c], 6) if isinstance(r.get(c), float) and math.isfinite(r[c])
                                                      else r.get(c)) for c in cols])
        return buf.getvalue()


class WatchlistService:
    def __init__(self, db: Database):
        self.db = db

    def create(self, name: str) -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 60:
            raise ValidationError("watchlist name must be 1–60 characters")
        cur = self.db.execute("INSERT INTO watchlists(name, created_at, updated_at) VALUES (?,?,?)", (name, now_ms(), now_ms()))
        return self.get(int(cur.lastrowid))

    def get(self, wl_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM watchlists WHERE id = ?", (wl_id,))
        if not row:
            raise NotFound(f"watchlist {wl_id} not found")
        row["items"] = [r["instrument_id"] for r in self.db.query(
            "SELECT instrument_id FROM watchlist_items WHERE watchlist_id = ? ORDER BY position, instrument_id", (wl_id,))]
        return row

    def list(self) -> list[dict[str, Any]]:
        return [self.get(r["id"]) for r in self.db.query("SELECT id FROM watchlists ORDER BY id")]

    def add(self, wl_id: int, instrument_id: str) -> dict[str, Any]:
        self.get(wl_id)
        if ":" not in instrument_id or len(instrument_id) > 80:
            raise ValidationError("instrument ids look like PROVIDER:SYMBOL")
        pos = self.db.scalar("SELECT COALESCE(MAX(position), 0) + 1 FROM watchlist_items WHERE watchlist_id = ?", (wl_id,))
        self.db.execute("INSERT OR IGNORE INTO watchlist_items(watchlist_id, instrument_id, position) VALUES (?,?,?)",
                        (wl_id, instrument_id, pos))
        self.db.execute("UPDATE watchlists SET updated_at = ? WHERE id = ?", (now_ms(), wl_id))
        return self.get(wl_id)

    def remove(self, wl_id: int, instrument_id: str) -> dict[str, Any]:
        self.db.execute("DELETE FROM watchlist_items WHERE watchlist_id = ? AND instrument_id = ?", (wl_id, instrument_id))
        return self.get(wl_id)

    def reorder(self, wl_id: int, ordered: list[str]) -> dict[str, Any]:
        with self.db.tx():
            for i, iid in enumerate(ordered):
                self.db.execute("UPDATE watchlist_items SET position = ? WHERE watchlist_id = ? AND instrument_id = ?",
                                (i, wl_id, iid))
        return self.get(wl_id)

    def rename(self, wl_id: int, name: str) -> dict[str, Any]:
        self.db.execute("UPDATE watchlists SET name = ?, updated_at = ? WHERE id = ?", (name.strip()[:60], now_ms(), wl_id))
        return self.get(wl_id)

    def delete(self, wl_id: int) -> None:
        self.db.execute("DELETE FROM watchlists WHERE id = ?", (wl_id,))
