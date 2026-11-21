"""Paper broker: simulated fills against real (or clearly labelled) market prices.

Fill model:
* market orders fill immediately at the last price, plus half the spread (from the quote's
  bid/ask when available, otherwise ``default_spread_bps``) and ``slippage_bps``, both adverse;
* buy limits fill when the price trades at or below the limit, at the LIMIT price (no price
  improvement is assumed); sell limits mirror that;
* stop orders become market orders once the price crosses the stop, and fill at that price
  plus slippage;
* commission is ``commission_pct`` of notional per fill.

All prices, positions and P&L here are simulated and labelled PAPER.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omos.execution.models import OrderStatus, OrderType, Side, check_transition
from omos.storage.db import Database
from omos.util import loads, now_ms


@dataclass
class PaperConfig:
    commission_pct: float = 0.1
    slippage_bps: float = 5.0
    default_spread_bps: float = 4.0


class PaperBroker:
    name = "paper"
    live = False

    def __init__(self, db: Database, config: PaperConfig | None = None):
        self.db = db
        self.cfg = config or PaperConfig()

    # -- order entry ------------------------------------------------------------------
    def place(self, order: dict[str, Any], price: float, *, bid: float | None = None, ask: float | None = None) -> dict[str, Any]:
        """Accept a NEW order. Market orders fill now; others rest until ``on_price`` triggers them."""
        if order["type"] == OrderType.MARKET.value:
            px = self._market_price(order["side"], price, bid, ask)
            return self._fill(order, order["qty"], px)
        self._set_status(order, OrderStatus.ACCEPTED)
        order = self._reload(order["id"])
        triggered = self.on_price(order["instrument_id"], price, only_order=order["id"])
        return triggered[0] if triggered else order

    def cancel(self, order: dict[str, Any], reason: str = "cancelled") -> dict[str, Any]:
        status = OrderStatus(order["status"])
        if not status.is_open:
            return order
        check_transition(status, OrderStatus.CANCELLED)
        self.db.execute("UPDATE orders SET status = ?, reason = COALESCE(reason, '') || ?, updated_at = ? WHERE id = ?",
                        (OrderStatus.CANCELLED.value, f" [{reason}]", now_ms(), order["id"]))
        return self._reload(order["id"])

    def on_price(self, instrument_id: str, price: float, *, only_order: int | None = None) -> list[dict[str, Any]]:
        """Evaluate resting orders against a new price. Returns the orders that changed."""
        sql = "SELECT * FROM orders WHERE instrument_id = ? AND status IN ('accepted','partially_filled')"
        params: list[Any] = [instrument_id]
        if only_order is not None:
            sql += " AND id = ?"
            params.append(only_order)
        changed = []
        for o in self.db.query(sql, params):
            fill_px = self._trigger_price(o, price)
            if fill_px is not None:
                remaining = o["qty"] - o["filled_qty"]
                changed.append(self._fill(o, remaining, fill_px))
        return changed

    # -- internals -----------------------------------------------------------------------
    def _market_price(self, side: str, price: float, bid: float | None, ask: float | None) -> float:
        slip = self.cfg.slippage_bps / 10_000
        if side == Side.BUY.value:
            base = ask if ask and ask > 0 else price * (1 + self.cfg.default_spread_bps / 20_000)
            return base * (1 + slip)
        base = bid if bid and bid > 0 else price * (1 - self.cfg.default_spread_bps / 20_000)
        return base * (1 - slip)

    def _trigger_price(self, o: dict[str, Any], price: float) -> float | None:
        side, typ = o["side"], o["type"]
        slip = self.cfg.slippage_bps / 10_000
        if typ == OrderType.LIMIT.value:
            if side == "buy" and price <= o["limit_price"]:
                return o["limit_price"]
            if side == "sell" and price >= o["limit_price"]:
                return o["limit_price"]
        elif typ == OrderType.STOP.value:
            if side == "buy" and price >= o["stop_price"]:
                return price * (1 + slip)
            if side == "sell" and price <= o["stop_price"]:
                return price * (1 - slip)
        elif typ == OrderType.STOP_LIMIT.value:
            meta = loads(o.get("meta"), {})
            armed = meta.get("armed", False)
            if not armed and ((side == "buy" and price >= o["stop_price"]) or (side == "sell" and price <= o["stop_price"])):
                meta["armed"] = True
                from omos.util import dumps
                self.db.execute("UPDATE orders SET meta = ? WHERE id = ?", (dumps(meta), o["id"]))
                armed = True
            if armed and ((side == "buy" and price <= o["limit_price"]) or (side == "sell" and price >= o["limit_price"])):
                return o["limit_price"]
        return None

    def _set_status(self, order: dict[str, Any], status: OrderStatus) -> None:
        check_transition(OrderStatus(order["status"]), status)
        self.db.execute("UPDATE orders SET status = ?, updated_at = ? WHERE id = ?", (status.value, now_ms(), order["id"]))

    def _reload(self, order_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM orders WHERE id = ?", (order_id,))
        assert row is not None
        return row

    def _fill(self, order: dict[str, Any], qty: float, price: float) -> dict[str, Any]:
        """Apply a fill atomically: fill row, position, cash, order status."""
        ts = now_ms()
        side = Side(order["side"])
        fee = abs(qty * price) * self.cfg.commission_pct / 100
        with self.db.tx():
            fresh = self._reload(order["id"])
            status = OrderStatus(fresh["status"])
            if not status.is_open:
                return fresh  # already filled/cancelled by another path: idempotent
            pos = self.db.query_one("SELECT * FROM positions WHERE account_id = ? AND instrument_id = ?",
                                    (order["account_id"], order["instrument_id"]))
            old_qty = pos["qty"] if pos else 0.0
            old_avg = pos["avg_price"] if pos else 0.0
            layers = pos["layers"] if pos else 0
            signed = qty * side.sign
            new_qty = old_qty + signed
            realized = 0.0
            if old_qty == 0 or (old_qty > 0) == (signed > 0):
                # opening or adding: weighted average cost; each add is one more layer
                new_avg = (old_avg * abs(old_qty) + price * qty) / abs(new_qty) if new_qty else 0.0
                layers += 1
            else:
                closing = min(abs(signed), abs(old_qty))
                realized = (price - old_avg) * closing * (1 if old_qty > 0 else -1)
                if abs(new_qty) < 1e-12:
                    new_avg, new_qty, layers = 0.0, 0.0, 0
                elif (new_qty > 0) == (old_qty > 0):
                    new_avg = old_avg  # partial close keeps the cost basis
                else:
                    new_avg, layers = price, 1  # flipped through zero: remainder opens at this price
            realized -= fee
            self.db.execute(
                "INSERT INTO fills(order_id, account_id, instrument_id, side, qty, price, fee, realized_pnl, ts) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (order["id"], order["account_id"], order["instrument_id"], side.value, qty, price, fee, realized, ts))
            self.db.execute(
                "INSERT INTO positions(account_id, instrument_id, qty, avg_price, realized_pnl, fees, layers, opened_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id, instrument_id) DO UPDATE SET qty = excluded.qty, "
                "avg_price = excluded.avg_price, realized_pnl = positions.realized_pnl + ?, fees = positions.fees + ?, "
                "layers = excluded.layers, updated_at = excluded.updated_at",
                (order["account_id"], order["instrument_id"], new_qty, new_avg, realized, fee, layers, ts, ts, realized, fee))
            self.db.execute("UPDATE accounts SET cash = cash - ? WHERE id = ?", (signed * price + fee, order["account_id"]))
            filled = fresh["filled_qty"] + qty
            avg_fill = ((fresh["avg_fill_price"] or 0) * fresh["filled_qty"] + price * qty) / filled
            new_status = OrderStatus.FILLED if filled >= fresh["qty"] - 1e-12 else OrderStatus.PARTIALLY_FILLED
            check_transition(status, new_status)
            self.db.execute("UPDATE orders SET status = ?, filled_qty = ?, avg_fill_price = ?, updated_at = ? WHERE id = ?",
                            (new_status.value, filled, avg_fill, ts, order["id"]))
        return self._reload(order["id"])
