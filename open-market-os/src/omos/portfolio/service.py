"""Accounts, positions, P&L, equity history, analytics and the trade journal.

Paper accounts are always labelled PAPER; their values are simulated and are never
mixed with live broker balances.
"""

from __future__ import annotations

import csv
import io
import math
import threading
from typing import Any

import numpy as np

from omos.errors import NotFound, ValidationError
from omos.risk.engine import AccountSnapshot, PositionView
from omos.storage.db import Database
from omos.util import dumps, loads, now_ms


class PriceBook:
    """Last known price per instrument, with its data-status label and time."""

    def __init__(self):
        self._prices: dict[str, tuple[float, str, int]] = {}
        self._lock = threading.Lock()

    def update(self, instrument_id: str, price: float, status: str, ts: int | None = None) -> None:
        if math.isfinite(price) and price > 0:
            with self._lock:
                self._prices[instrument_id] = (float(price), status, ts or now_ms())

    def get(self, instrument_id: str) -> tuple[float, str, int] | None:
        with self._lock:
            return self._prices.get(instrument_id)

    def prices(self) -> dict[str, float]:
        with self._lock:
            return {k: v[0] for k, v in self._prices.items()}


class PortfolioService:
    def __init__(self, db: Database, prices: PriceBook):
        self.db = db
        self.prices = prices

    # -- accounts ----------------------------------------------------------------------
    def create_account(self, name: str, initial_cash: float, *, kind: str = "paper", broker: str = "paper",
                       currency: str = "USD") -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 60:
            raise ValidationError("account name must be 1–60 characters")
        if not math.isfinite(initial_cash) or initial_cash <= 0 or initial_cash > 1e12:
            raise ValidationError("initial cash must be a positive amount")
        if kind not in ("paper", "live"):
            raise ValidationError("account kind must be paper or live")
        cur = self.db.execute(
            "INSERT INTO accounts(name, kind, broker, currency, initial_cash, cash, created_at, meta) VALUES (?,?,?,?,?,?,?,?)",
            (name, kind, broker, currency.upper()[:8], initial_cash, initial_cash, now_ms(), "{}"))
        return self.account(int(cur.lastrowid))

    def account(self, account_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not row:
            raise NotFound(f"account {account_id} not found")
        row["meta"] = loads(row["meta"], {})
        row["label"] = "PAPER" if row["kind"] == "paper" else "LIVE"
        return row

    def accounts(self) -> list[dict[str, Any]]:
        out = []
        for r in self.db.query("SELECT id FROM accounts ORDER BY id"):
            out.append(self.summary(r["id"]))
        return out

    def ensure_default_paper_account(self) -> dict[str, Any]:
        row = self.db.query_one("SELECT id FROM accounts WHERE kind = 'paper' ORDER BY id LIMIT 1")
        if row:
            return self.account(row["id"])
        return self.create_account("Paper", 10_000.0)

    def reset_account(self, account_id: int, actor: str) -> dict[str, Any]:
        acct = self.account(account_id)
        if acct["kind"] != "paper":
            raise ValidationError("only paper accounts can be reset")
        with self.db.tx():
            self.db.execute("DELETE FROM positions WHERE account_id = ?", (account_id,))
            self.db.execute("UPDATE orders SET status = 'cancelled', updated_at = ? WHERE account_id = ? AND status IN "
                            "('new','accepted','partially_filled')", (now_ms(), account_id))
            self.db.execute("UPDATE accounts SET cash = initial_cash WHERE id = ?", (account_id,))
            self.db.execute("DELETE FROM equity_snapshots WHERE account_id = ?", (account_id,))
            self.db.execute("DELETE FROM risk_state WHERE account_id = ?", (account_id,))
        self.db.audit(actor, "account.reset", str(account_id))
        return self.account(account_id)

    # -- positions / snapshot ------------------------------------------------------------------
    def positions(self, account_id: int) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM positions WHERE account_id = ? AND ABS(qty) > 1e-12 ORDER BY instrument_id",
                             (account_id,))
        for r in rows:
            p = self.prices.get(r["instrument_id"])
            price = p[0] if p else r["avg_price"]
            r["price"] = price
            r["price_status"] = p[1] if p else "UNAVAILABLE"
            r["market_value"] = r["qty"] * price
            r["unrealized_pnl"] = (price - r["avg_price"]) * r["qty"]
            r["unrealized_pnl_pct"] = (price / r["avg_price"] - 1) * 100 * (1 if r["qty"] > 0 else -1) if r["avg_price"] else 0.0
        return rows

    def snapshot(self, account_id: int) -> AccountSnapshot:
        acct = self.account(account_id)
        pos = self.positions(account_id)
        equity = acct["cash"] + sum(p["market_value"] for p in pos)
        return AccountSnapshot(
            account_id=account_id, kind=acct["kind"], equity=equity, cash=acct["cash"],
            positions={p["instrument_id"]: PositionView(p["qty"], p["avg_price"], p["layers"]) for p in pos},
            prices={p["instrument_id"]: p["price"] for p in pos})

    def summary(self, account_id: int) -> dict[str, Any]:
        acct = self.account(account_id)
        pos = self.positions(account_id)
        mv = sum(p["market_value"] for p in pos)
        equity = acct["cash"] + mv
        gross = sum(abs(p["market_value"]) for p in pos)
        realized = self.db.scalar("SELECT COALESCE(SUM(realized_pnl), 0) FROM fills WHERE account_id = ?", (account_id,)) or 0.0
        fees = self.db.scalar("SELECT COALESCE(SUM(fee), 0) FROM fills WHERE account_id = ?", (account_id,)) or 0.0
        weights = [abs(p["market_value"]) / gross for p in pos] if gross else []
        return {**acct, "equity": equity, "market_value": mv, "unrealized_pnl": sum(p["unrealized_pnl"] for p in pos),
                "realized_pnl": realized, "fees_paid": fees, "total_pnl": equity - acct["initial_cash"],
                "total_return_pct": (equity / acct["initial_cash"] - 1) * 100, "gross_exposure": gross,
                "exposure_pct": gross / equity * 100 if equity else 0.0, "open_positions": len(pos),
                "concentration_hhi": float(sum(w * w for w in weights)), "positions": pos,
                "stale_prices": [p["instrument_id"] for p in pos if p["price_status"] == "UNAVAILABLE"]}

    def record_equity(self, account_id: int) -> float:
        s = self.summary(account_id)
        self.db.execute("INSERT OR REPLACE INTO equity_snapshots(account_id, ts, equity, cash, exposure) VALUES (?,?,?,?,?)",
                        (account_id, now_ms(), s["equity"], s["cash"], s["gross_exposure"]))
        return s["equity"]

    def equity_curve(self, account_id: int, limit: int = 5000) -> list[dict[str, Any]]:
        return self.db.query("SELECT ts, equity, cash, exposure FROM (SELECT * FROM equity_snapshots WHERE account_id = ? "
                             "ORDER BY ts DESC LIMIT ?) ORDER BY ts", (account_id, limit))

    def analytics(self, account_id: int, closes: dict[str, np.ndarray] | None = None) -> dict[str, Any]:
        curve = self.equity_curve(account_id)
        eq = np.array([c["equity"] for c in curve], dtype=float)
        out: dict[str, Any] = {"points": len(eq)}
        if len(eq) > 2:
            rets = np.diff(eq) / eq[:-1]
            peaks = np.maximum.accumulate(eq)
            out.update(max_drawdown_pct=float((1 - eq / peaks).max() * 100), volatility_per_step=float(np.std(rets)),
                       best_step_pct=float(rets.max() * 100), worst_step_pct=float(rets.min() * 100))
        s = self.summary(account_id)
        out["allocation"] = [{"instrument_id": p["instrument_id"], "weight_pct": abs(p["market_value"]) / s["gross_exposure"] * 100}
                             for p in s["positions"]] if s["gross_exposure"] else []
        if closes and len(closes) >= 2:
            names = list(closes)
            m = min(len(v) for v in closes.values())
            rets = np.array([np.diff(np.log(closes[k][-m:])) for k in names])
            corr = np.corrcoef(rets)
            out["correlation"] = {"instruments": names, "matrix": [[float(x) for x in row] for row in corr]}
        return out

    # -- trades / export ----------------------------------------------------------------------
    def fills(self, account_id: int, limit: int = 500) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM fills WHERE account_id = ? ORDER BY id DESC LIMIT ?", (account_id, limit))

    def orders(self, account_id: int, *, open_only: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        sql = "SELECT * FROM orders WHERE account_id = ?"
        if open_only:
            sql += " AND status IN ('new','accepted','partially_filled')"
        rows = self.db.query(sql + " ORDER BY id DESC LIMIT ?", (account_id, limit))
        for r in rows:
            r["meta"] = loads(r["meta"], {})
        return rows

    def export_fills_csv(self, account_id: int) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["time_utc_ms", "instrument", "side", "qty", "price", "fee", "realized_pnl", "order_id", "account"])
        label = self.account(account_id)["kind"].upper()
        for f in reversed(self.fills(account_id, limit=100_000)):
            w.writerow([f["ts"], f["instrument_id"], f["side"], f["qty"], f["price"], f["fee"], f["realized_pnl"],
                        f["order_id"], label])
        return buf.getvalue()

    # -- journal ------------------------------------------------------------------------
    def add_journal(self, title: str, body: str = "", *, account_id: int | None = None, instrument_id: str | None = None,
                    tags: list[str] | None = None, trade_ref: str | None = None) -> dict[str, Any]:
        if not title.strip() or len(title) > 200:
            raise ValidationError("journal title must be 1–200 characters")
        if len(body) > 20_000:
            raise ValidationError("journal entry is too long")
        tags = [t.strip()[:30] for t in (tags or []) if t.strip()][:20]
        cur = self.db.execute("INSERT INTO journal(ts, account_id, instrument_id, title, body, tags, trade_ref) VALUES (?,?,?,?,?,?,?)",
                              (now_ms(), account_id, instrument_id, title.strip(), body, dumps(tags), trade_ref))
        return self.db.query_one("SELECT * FROM journal WHERE id = ?", (cur.lastrowid,)) or {}

    def journal(self, *, tag: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM journal ORDER BY ts DESC LIMIT ?", (limit,))
        for r in rows:
            r["tags"] = loads(r["tags"], [])
        if tag:
            rows = [r for r in rows if tag in r["tags"]]
        return rows

    def delete_journal(self, entry_id: int) -> None:
        self.db.execute("DELETE FROM journal WHERE id = ?", (entry_id,))
