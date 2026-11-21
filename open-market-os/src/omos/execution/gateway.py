"""ExecutionGateway — the ONLY path from any actor (UI, bot, AI, MCP, workflow) to a broker.

submit():  validate -> idempotency -> price check -> RiskEngine.check -> record decision
           -> persist order -> broker.place -> post-trade risk monitor -> publish event

Bots, AI agents, MCP handlers and workflows receive a :class:`TradingContext`, which wraps
this gateway with a fixed origin. They never receive a broker object.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from omos.errors import NotFound, RiskRejected, ValidationError
from omos.execution.models import OrderRequest, OrderStatus, OrderType, Side
from omos.execution.paper import PaperBroker
from omos.portfolio.service import PortfolioService, PriceBook
from omos.risk.engine import MarketContext, RiskDecision, RiskEngine
from omos.storage.db import Database
from omos.util import dumps, loads, new_id, now_ms

log = logging.getLogger(__name__)

QuoteFn = Callable[[str], dict[str, Any]]  # instrument_id -> {"price", "status", "bid", "ask", "session_open", "atr"}


class ExecutionGateway:
    def __init__(self, db: Database, risk: RiskEngine, portfolio: PortfolioService, prices: PriceBook,
                 quote_fn: QuoteFn, *, publish: Callable[[str, dict[str, Any]], None] | None = None,
                 live_brokers: dict[str, Any] | None = None):
        self.db = db
        self.risk = risk
        self.portfolio = portfolio
        self.prices = prices
        self.quote_fn = quote_fn
        self.publish = publish or (lambda topic, data: None)
        self.paper = PaperBroker(db)
        self.live_brokers = live_brokers or {}   # none are verified in this release
        self._lock = threading.RLock()

    def live_enabled(self) -> bool:
        return bool(self.db.get_setting("trading.live_enabled", False))

    def _broker_for(self, account: dict[str, Any]):
        if account["kind"] == "paper":
            return self.paper
        broker = self.live_brokers.get(account["broker"])
        if broker is None:
            raise RiskRejected(f"no live broker adapter '{account['broker']}' is installed and verified")
        return broker

    def _market(self, instrument_id: str) -> tuple[MarketContext, dict[str, Any]]:
        q = self.quote_fn(instrument_id)
        self.prices.update(instrument_id, q["price"], q.get("status", "UNAVAILABLE"))
        return MarketContext(price=float(q["price"]), price_status=q.get("status", "UNAVAILABLE"),
                             session_open=bool(q.get("session_open", True)), atr=q.get("atr")), q

    # -- submit -------------------------------------------------------------------------
    def submit(self, req: OrderRequest, *, actor: str) -> dict[str, Any]:
        req.validate()
        account = self.portfolio.account(req.account_id)
        with self._lock:
            if req.client_order_id:
                existing = self.db.query_one("SELECT * FROM orders WHERE client_order_id = ?", (req.client_order_id,))
                if existing:
                    # idempotent replay (e.g. a bot restarting): never place twice
                    return {"order": existing, "duplicate": True, "decision": None}
            else:
                req.client_order_id = new_id("co_")
            try:
                broker = self._broker_for(account)
            except RiskRejected as exc:
                decision = RiskDecision(False, new_id("rd_"), [exc.message], [], 0.0, 0.0)
                return self._reject(req, account, decision, actor)
            try:
                mkt, quote = self._market(req.instrument_id)
            except Exception as exc:  # no price -> no order
                decision = RiskDecision(False, new_id("rd_"), [f"no market price available: {exc}"], [], 0.0, 0.0)
                return self._reject(req, account, decision, actor)
            snapshot = self.portfolio.snapshot(req.account_id)
            active_bots = self.db.scalar("SELECT COUNT(*) FROM bots WHERE state = 'running'") or 0
            decision = self.risk.check(req, snapshot, mkt, live_enabled=self.live_enabled(), active_bots=active_bots)
            if not decision.approved:
                return self._reject(req, account, decision, actor)
            self.risk.log_event(req.account_id, "approved", req.origin, "pre_trade", "order approved",
                                req.to_dict(), decision.decision_id)
            ts = now_ms()
            cur = self.db.execute(
                "INSERT INTO orders(client_order_id, account_id, instrument_id, side, type, qty, limit_price, stop_price, "
                "tif, status, origin, bot_id, reason, risk_decision_id, meta, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (req.client_order_id, req.account_id, req.instrument_id, req.side.value, req.type.value, req.qty,
                 req.limit_price, req.stop_price, req.tif, OrderStatus.NEW.value, req.origin, req.bot_id, req.reason,
                 decision.decision_id, dumps({**req.meta, "stop_loss": req.stop_loss, "take_profit": req.take_profit,
                                              "price_status_at_submit": mkt.price_status}), ts, ts))
            order = self.db.query_one("SELECT * FROM orders WHERE id = ?", (cur.lastrowid,))
            assert order is not None
            order = broker.place(order, mkt.price, bid=quote.get("bid"), ask=quote.get("ask"))
            self.db.audit(actor, "order.submit", str(order["id"]), {"client_order_id": req.client_order_id,
                                                                   "status": order["status"], "origin": req.origin})
        self._after_trade(req.account_id)
        self.publish("order", {"order": order, "account_id": req.account_id})
        return {"order": order, "duplicate": False, "decision": decision.to_dict()}

    def _reject(self, req: OrderRequest, account: dict[str, Any], decision: RiskDecision, actor: str) -> dict[str, Any]:
        ts = now_ms()
        self.db.execute(
            "INSERT INTO orders(client_order_id, account_id, instrument_id, side, type, qty, limit_price, stop_price, tif, "
            "status, origin, bot_id, reason, risk_decision_id, meta, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (req.client_order_id, req.account_id, req.instrument_id, req.side.value, req.type.value, req.qty, req.limit_price,
             req.stop_price, req.tif, OrderStatus.REJECTED.value, req.origin, req.bot_id,
             "; ".join(decision.reasons)[:1000], decision.decision_id, dumps(req.meta), ts, ts))
        self.risk.log_event(req.account_id, "rejected", req.origin, ",".join(c["rule"] for c in decision.checks if not c["passed"]),
                            "; ".join(decision.reasons), req.to_dict(), decision.decision_id)
        self.db.audit(actor, "order.rejected", req.client_order_id, {"reasons": decision.reasons})
        order = self.db.query_one("SELECT * FROM orders WHERE client_order_id = ?", (req.client_order_id,))
        self.publish("order", {"order": order, "account_id": req.account_id})
        return {"order": order, "duplicate": False, "decision": decision.to_dict()}

    def _after_trade(self, account_id: int) -> None:
        equity = self.portfolio.record_equity(account_id)
        state = self.risk.monitor(account_id, equity)
        if state.get("breach"):
            self.publish("risk", {"account_id": account_id, **state})

    # -- other actions --------------------------------------------------------------------
    def cancel(self, order_id: int, *, actor: str) -> dict[str, Any]:
        order = self.db.query_one("SELECT * FROM orders WHERE id = ?", (order_id,))
        if not order:
            raise NotFound(f"order {order_id} not found")
        account = self.portfolio.account(order["account_id"])
        out = self._broker_for(account).cancel(order, reason=f"by {actor}")
        self.db.audit(actor, "order.cancel", str(order_id))
        self.publish("order", {"order": out, "account_id": order["account_id"]})
        return out

    def cancel_all(self, *, actor: str, account_id: int | None = None) -> int:
        sql = "SELECT id FROM orders WHERE status IN ('new','accepted','partially_filled')"
        params: tuple = ()
        if account_id is not None:
            sql += " AND account_id = ?"
            params = (account_id,)
        ids = [r["id"] for r in self.db.query(sql, params)]
        for oid in ids:
            self.cancel(oid, actor=actor)
        return len(ids)

    def flatten(self, account_id: int, *, actor: str, origin: str = "ui") -> list[dict[str, Any]]:
        """Close every position in the account with reduce-only market orders."""
        results = []
        for p in self.portfolio.positions(account_id):
            side = Side.SELL if p["qty"] > 0 else Side.BUY
            req = OrderRequest(account_id=account_id, instrument_id=p["instrument_id"], side=side, qty=abs(p["qty"]),
                               type=OrderType.MARKET, origin=origin, reason="flatten", reduce_only=True)
            results.append(self.submit(req, actor=actor))
        return results

    def on_price(self, instrument_id: str, price: float, status: str) -> list[dict[str, Any]]:
        """Called by the market-data poller: updates prices and triggers resting paper orders."""
        self.prices.update(instrument_id, price, status)
        changed = self.paper.on_price(instrument_id, price)
        for o in changed:
            self._after_trade(o["account_id"])
            self.publish("order", {"order": o, "account_id": o["account_id"]})
        return changed

    def reconcile(self) -> dict[str, Any]:
        """Startup reconciliation: NEW orders that never reached the broker (crash mid-submit) are cancelled,
        resting orders are kept, and every account's risk state is refreshed."""
        stuck = self.db.query("SELECT * FROM orders WHERE status = 'new'")
        for o in stuck:
            self.db.execute("UPDATE orders SET status = 'cancelled', reason = COALESCE(reason,'') || ' [reconcile: not "
                            "confirmed by broker before restart]', updated_at = ? WHERE id = ?", (now_ms(), o["id"]))
        resting = self.db.scalar("SELECT COUNT(*) FROM orders WHERE status IN ('accepted','partially_filled')") or 0
        for a in self.db.query("SELECT id FROM accounts"):
            try:
                self._after_trade(a["id"])
            except Exception:  # pragma: no cover - never block startup on analytics
                log.exception("reconcile: account %s", a["id"])
        return {"cancelled_unconfirmed": len(stuck), "resting_orders": resting}

    # -- AI / automation proposals (human approval) ----------------------------------------------
    def propose(self, req: OrderRequest, *, rationale: str, origin: str, ttl_s: int = 3600) -> dict[str, Any]:
        req.validate()
        self.portfolio.account(req.account_id)
        cur = self.db.execute(
            "INSERT INTO order_proposals(account_id, payload, origin, rationale, status, created_at, expires_at) "
            "VALUES (?,?,?,?, 'pending', ?, ?)",
            (req.account_id, dumps(req.to_dict()), origin, rationale[:4000], now_ms(), now_ms() + ttl_s * 1000))
        prop = self.db.query_one("SELECT * FROM order_proposals WHERE id = ?", (cur.lastrowid,))
        self.publish("proposal", {"proposal": prop})
        return prop or {}

    def decide_proposal(self, proposal_id: int, approve: bool, *, actor: str) -> dict[str, Any]:
        if actor.split(":")[0] not in ("ui", "cli"):
            raise ValidationError("only a person can approve or reject an order proposal")
        prop = self.db.query_one("SELECT * FROM order_proposals WHERE id = ?", (proposal_id,))
        if not prop:
            raise NotFound(f"proposal {proposal_id} not found")
        if prop["status"] != "pending":
            raise ValidationError(f"proposal is already {prop['status']}")
        if prop["expires_at"] and prop["expires_at"] < now_ms():
            self.db.execute("UPDATE order_proposals SET status = 'expired', decided_at = ? WHERE id = ?", (now_ms(), proposal_id))
            raise ValidationError("proposal has expired")
        if not approve:
            self.db.execute("UPDATE order_proposals SET status = 'rejected', decided_at = ? WHERE id = ?", (now_ms(), proposal_id))
            return {"status": "rejected"}
        req = OrderRequest.from_dict(loads(prop["payload"]))
        req.origin = prop["origin"]  # keeps the AI/automation origin for risk rules and audit
        result = self.submit(req, actor=actor)
        self.db.execute("UPDATE order_proposals SET status = 'approved', decided_at = ?, result = ? WHERE id = ?",
                        (now_ms(), dumps({"order_id": result["order"]["id"], "status": result["order"]["status"]}), proposal_id))
        return {"status": "approved", **result}


class TradingContext:
    """What automation gets instead of a broker: a fixed origin and the gateway's checks."""

    def __init__(self, gateway: ExecutionGateway, *, origin: str, account_id: int, bot_id: int | None = None):
        self._gw = gateway
        self.origin = origin
        self.account_id = account_id
        self.bot_id = bot_id

    def submit(self, instrument_id: str, side: str, qty: float, *, client_order_id: str, type: str = "market",
               limit_price: float | None = None, stop_price: float | None = None, stop_loss: float | None = None,
               take_profit: float | None = None, reason: str = "", reduce_only: bool = False) -> dict[str, Any]:
        req = OrderRequest(account_id=self.account_id, instrument_id=instrument_id, side=Side(side), qty=qty,
                           type=OrderType(type), limit_price=limit_price, stop_price=stop_price,
                           client_order_id=client_order_id, origin=self.origin, bot_id=self.bot_id, reason=reason,
                           stop_loss=stop_loss, take_profit=take_profit, reduce_only=reduce_only)
        return self._gw.submit(req, actor=self.origin)

    def position(self, instrument_id: str) -> dict[str, Any] | None:
        for p in self._gw.portfolio.positions(self.account_id):
            if p["instrument_id"] == instrument_id:
                return p
        return None

    def equity(self) -> float:
        return self._gw.portfolio.snapshot(self.account_id).equity

    def open_orders(self) -> list[dict[str, Any]]:
        return [o for o in self._gw.portfolio.orders(self.account_id, open_only=True) if o.get("bot_id") == self.bot_id]

    def cancel(self, order_id: int) -> dict[str, Any]:
        o = self._gw.db.query_one("SELECT bot_id FROM orders WHERE id = ?", (order_id,))
        if not o or o["bot_id"] != self.bot_id:
            raise ValidationError("a bot may only cancel its own orders")
        return self._gw.cancel(order_id, actor=self.origin)
