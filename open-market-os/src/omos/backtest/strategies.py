"""Saved strategies with version history, plus built-in example templates."""

from __future__ import annotations

from typing import Any

from omos.errors import NotFound, ValidationError
from omos.script.lang import parse
from omos.storage.db import Database
from omos.util import now_ms

TEMPLATES: dict[str, dict[str, str]] = {
    "ema_cross": {"name": "EMA crossover (trend)", "source": """# Trend-following: go long when the fast EMA crosses above the slow EMA.
param fast = 12 (5, 30, 1)
param slow = 26 (20, 100, 2)
f = ema(close, fast)
s = ema(close, slow)
long_entry = crossover(f, s)
long_exit = crossunder(f, s)
stop_loss = 2 * atr(14)
plot(f, "fast EMA")
plot(s, "slow EMA")
"""},
    "rsi_reversion": {"name": "RSI(2) mean reversion (Connors-style)", "source": """# Buy short-term dips in an uptrend; exit on the first bounce.
param rsi_len = 2 (2, 5, 1)
param entry_level = 10 (5, 30, 5)
trend_ok = close > sma(close, 200)
long_entry = trend_ok and rsi(close, rsi_len) < entry_level
long_exit = close > sma(close, 5)
stop_loss_pct = 8
"""},
    "donchian_breakout": {"name": "Donchian breakout (turtle-style)", "source": """# Breakout of the 20-bar high, exit on the 10-bar low.
param entry_len = 20 (10, 60, 5)
param exit_len = 10 (5, 30, 5)
long_entry = close > dc_upper(entry_len)[1]
long_exit = close < dc_lower(exit_len)[1]
trailing_stop = 3 * atr(20)
"""},
    "supertrend_mtf": {"name": "Supertrend with higher-timeframe filter", "source": """# Trade in the direction of the 4h trend, using Supertrend flips on the chart timeframe.
htf_up = tf("4h", close > ema(close, 50))
dir = supertrend_dir(10, 3)
long_entry = htf_up and dir == 1 and dir[1] == -1
long_exit = dir == -1
stop_loss = 2.5 * atr(14)
"""},
    "macd_momentum": {"name": "MACD momentum with ADX filter", "source": """param fast = 12 (8, 16, 2)
param slow = 26 (20, 34, 2)
hist = macd_hist(close, fast, slow, 9)
strong = adx(14) > 20
long_entry = strong and crossover(hist, 0)
long_exit = crossunder(hist, 0)
take_profit = 3 * atr(14)
stop_loss = 1.5 * atr(14)
"""},
}


class StrategyService:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def check(source: str) -> dict[str, Any]:
        prog = parse(source)
        return {"ok": True, "is_strategy": prog.is_strategy, "params": prog.describe_params(),
                "plots": [s.name for s in prog.statements if s.kind == "plot"],
                "outputs": sorted(prog.assigned)}

    def create(self, name: str, source: str, note: str = "") -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 60:
            raise ValidationError("strategy name must be 1–60 characters")
        self.check(source)
        ts = now_ms()
        cur = self.db.execute("INSERT INTO strategies(name, kind, current_version, created_at, updated_at) VALUES (?, 'script', 1, ?, ?)",
                              (name, ts, ts))
        sid = int(cur.lastrowid)
        self.db.execute("INSERT INTO strategy_versions(strategy_id, version, source, note, created_at) VALUES (?, 1, ?, ?, ?)",
                        (sid, source, note[:500], ts))
        return self.get(sid)

    def update(self, sid: int, source: str, note: str = "") -> dict[str, Any]:
        s = self.get(sid)
        self.check(source)
        v = s["current_version"] + 1
        ts = now_ms()
        self.db.execute("INSERT INTO strategy_versions(strategy_id, version, source, note, created_at) VALUES (?,?,?,?,?)",
                        (sid, v, source, note[:500], ts))
        self.db.execute("UPDATE strategies SET current_version = ?, updated_at = ? WHERE id = ?", (v, ts, sid))
        return self.get(sid)

    def get(self, sid: int, version: int | None = None) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM strategies WHERE id = ?", (sid,))
        if not row:
            raise NotFound(f"strategy {sid} not found")
        v = version or row["current_version"]
        src = self.db.query_one("SELECT source, note, created_at FROM strategy_versions WHERE strategy_id = ? AND version = ?",
                                (sid, v))
        if not src:
            raise NotFound(f"strategy {sid} has no version {v}")
        return {**row, "version": v, "source": src["source"], "note": src["note"]}

    def versions(self, sid: int) -> list[dict[str, Any]]:
        return self.db.query("SELECT version, note, created_at FROM strategy_versions WHERE strategy_id = ? ORDER BY version",
                             (sid,))

    def list(self) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM strategies ORDER BY name")

    def delete(self, sid: int) -> None:
        self.db.execute("DELETE FROM strategies WHERE id = ?", (sid,))

    def export(self, sid: int) -> dict[str, Any]:
        s = self.get(sid)
        return {"format": "omos-strategy-v1", "name": s["name"], "source": s["source"], "version": s["version"]}

    def import_(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("format") != "omos-strategy-v1":
            raise ValidationError("not an Open Market OS strategy export")
        return self.create(str(data["name"])[:60], str(data["source"]), note="imported")
