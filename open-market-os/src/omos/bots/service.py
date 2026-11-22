"""Bot definitions, lifecycle state machine and the runner that executes them.

Lifecycle::

    created -> configured -> validated -> backtested -> paper_tested -> approved
                               |   \\________________\\_________________\\
                               v                                       v
                            running <-> paused        (paper bots may run from 'validated' on:
                               |   \\                  running IS the paper test)
                               v    v
                     error -> recovering -> running
                               stopped -> archived

Live mode additionally requires ``approved`` (which requires ``paper_tested``), live trading
enabled globally and a verified live broker — none ships in this release.

Duplicate-order safety: every order a bot sends uses a deterministic client order id
``bot<id>:<bar ts>:<action>``. If the host crashes after submitting and restarts, the replay
hits the same id and the gateway returns the existing order instead of placing a new one.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from omos.backtest.engine import BacktestConfig, backtest_script
from omos.bots import layered
from omos.errors import InvalidState, NotFound, OmosError, ValidationError
from omos.events import EventBus
from omos.execution.gateway import ExecutionGateway, TradingContext
from omos.marketdata.service import MarketDataService
from omos.marketdata.timeframes import parse as parse_tf
from omos.risk.engine import RiskEngine
from omos.script.lang import evaluate_expression, parse, run
from omos.storage.db import Database
from omos.util import dumps, loads, now_ms

log = logging.getLogger(__name__)

STATES = ("created", "configured", "validated", "backtested", "paper_tested", "approved", "running", "paused",
          "error", "recovering", "stopped", "archived")
RUNNABLE_FROM = {"validated", "backtested", "paper_tested", "approved", "paused", "stopped", "error", "recovering"}
TRANSITIONS: dict[str, set[str]] = {
    "created": {"configured", "archived"},
    "configured": {"validated", "configured", "archived"},
    "validated": {"backtested", "running", "configured", "archived"},
    "backtested": {"backtested", "paper_tested", "running", "configured", "archived"},
    "paper_tested": {"approved", "running", "configured", "archived", "backtested"},
    "approved": {"running", "configured", "archived", "backtested"},
    "running": {"paused", "stopped", "error", "recovering"},
    "paused": {"running", "stopped", "archived", "configured"},  # reconfiguring needs re-validation
    "error": {"recovering", "running", "stopped", "archived", "configured"},
    "recovering": {"running", "error", "stopped", "paused"},
    "stopped": {"running", "configured", "archived", "backtested", "paper_tested"},
    "archived": set(),
}
KINDS = ("rule", "dca", "grid", "layered")
MAX_CONSECUTIVE_ERRORS = 5


def _bot_row(row: dict[str, Any]) -> dict[str, Any]:
    row["config"] = loads(row["config"], {})
    row["flags"] = loads(row["flags"], {})
    row["runtime"] = loads(row.get("runtime"), {})
    return row


class BotService:
    def __init__(self, db: Database, market: MarketDataService, gateway: ExecutionGateway, risk: RiskEngine,
                 bus: EventBus | None = None):
        self.db = db
        self.market = market
        self.gateway = gateway
        self.risk = risk
        self.bus = bus or EventBus()

    # -- CRUD --------------------------------------------------------------------------
    def create(self, name: str, kind: str, instrument_id: str, timeframe: str, *, account_id: int,
               config: dict[str, Any] | None = None, strategy_id: int | None = None, actor: str = "ui") -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 60:
            raise ValidationError("bot name must be 1–60 characters")
        if kind not in KINDS:
            raise ValidationError(f"bot kind must be one of {', '.join(KINDS)}")
        parse_tf(timeframe)
        self.market.split_id(instrument_id)
        self.gateway.portfolio.account(account_id)
        ts = now_ms()
        cur = self.db.execute(
            "INSERT INTO bots(name, kind, state, mode, account_id, instrument_id, timeframe, strategy_id, config, flags, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, kind, "created", "paper", account_id, instrument_id, timeframe, strategy_id, dumps(config or {}), "{}", ts, ts))
        bot_id = int(cur.lastrowid)
        self._history(bot_id, None, "created", f"created by {actor}")
        if config:
            self.configure(bot_id, config, actor=actor)
        return self.get(bot_id)

    def get(self, bot_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM bots WHERE id = ?", (bot_id,))
        if not row:
            raise NotFound(f"bot {bot_id} not found")
        return _bot_row(row)

    def list(self, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM bots" + ("" if include_archived else " WHERE state != 'archived'") + " ORDER BY id"
        return [_bot_row(r) for r in self.db.query(sql)]

    def clone(self, bot_id: int, new_name: str, actor: str = "ui") -> dict[str, Any]:
        b = self.get(bot_id)
        return self.create(new_name, b["kind"], b["instrument_id"], b["timeframe"], account_id=b["account_id"],
                           config=b["config"], strategy_id=b["strategy_id"], actor=actor)

    def configure(self, bot_id: int, config: dict[str, Any], *, actor: str = "ui") -> dict[str, Any]:
        b = self.get(bot_id)
        if b["state"] in ("running", "recovering"):
            raise InvalidState("pause or stop the bot before changing its configuration")
        cfg = self._normalise_config(b["kind"], config)
        self.db.execute("UPDATE bots SET config = ?, version = version + 1, updated_at = ?, flags = '{}' WHERE id = ?",
                        (dumps(cfg), now_ms(), bot_id))
        self._transition(b, "configured", f"configured by {actor}")
        return self.get(bot_id)

    def _normalise_config(self, kind: str, cfg: dict[str, Any]) -> dict[str, Any]:
        out = dict(cfg)
        sizing = out.setdefault("sizing", "pct_equity")
        if sizing not in ("pct_equity", "fixed_qty", "fixed_cash", "risk_pct"):
            raise ValidationError("sizing must be pct_equity, fixed_qty, fixed_cash or risk_pct")
        out["size_value"] = float(out.get("size_value", 10.0))
        if out["size_value"] <= 0:
            raise ValidationError("size value must be positive")
        out["lookback_bars"] = int(out.get("lookback_bars", 300))
        if not 50 <= out["lookback_bars"] <= 5000:
            raise ValidationError("lookback must be between 50 and 5000 bars")
        if kind == "rule":
            src = out.get("script", "")
            prog = parse(src)
            if not prog.is_strategy:
                raise ValidationError("rule bots need a strategy script that sets long_entry and/or short_entry")
            out["allow_short"] = bool(out.get("allow_short", False))
        elif kind == "dca":
            out["every_bars"] = int(out.get("every_bars", 1))
            out["max_invested"] = float(out.get("max_invested", 1000.0))
            if out["every_bars"] < 1 or out["max_invested"] <= 0:
                raise ValidationError("DCA needs every_bars >= 1 and a positive max_invested")
            if out.get("condition"):
                parse(f"__c = {out['condition']}\n")
        elif kind == "grid":
            lo, hi, levels = float(out.get("lower", 0)), float(out.get("upper", 0)), int(out.get("levels", 0))
            if not (0 < lo < hi) or not 2 <= levels <= 100:
                raise ValidationError("grid needs 0 < lower < upper and 2–100 levels")
            out["qty_per_level"] = float(out.get("qty_per_level", 0))
            if out["qty_per_level"] <= 0:
                raise ValidationError("grid needs a positive qty_per_level")
            out.update(lower=lo, upper=hi, levels=levels)
        elif kind == "layered":
            out.update(layered.normalise(out))
            if out["entry_condition"]:
                parse(f"__c = {out['entry_condition']}\n")
        return out

    def validate(self, bot_id: int, *, actor: str = "ui") -> dict[str, Any]:
        b = self.get(bot_id)
        candles = self.market.get_candles(b["instrument_id"], b["timeframe"], limit=min(500, b["config"].get("lookback_bars", 300)))
        if len(candles) < 50:
            raise ValidationError(f"only {len(candles)} bars of data available; need at least 50 to validate")
        if b["kind"] == "rule":
            run(b["config"]["script"], candles)  # proves the script evaluates on real data
        if b["kind"] == "layered":
            if b["config"].get("entry_condition"):
                evaluate_expression(b["config"]["entry_condition"], candles)
            lim = self.risk.limits(b["account_id"])
            if b["config"]["basket_risk_pct"] > lim.max_position_risk_pct:
                raise ValidationError(f"the basket risk budget ({b['config']['basket_risk_pct']}% of equity) is above this "
                                      f"account's position risk limit ({lim.max_position_risk_pct}%): every basket would "
                                      "be rejected. Lower the budget or raise the limit on the Risk page.")
        self._set_flag(bot_id, "validated_at", now_ms())
        self._transition(b, "validated", f"validated by {actor} on {len(candles)} {candles.status.value} bars")
        return self.get(bot_id)

    def backtest(self, bot_id: int, *, limit: int = 2000, actor: str = "ui") -> dict[str, Any]:
        b = self.get(bot_id)
        if b["kind"] != "rule":
            raise ValidationError("backtests are available for rule bots; layered bots are tested with Simulate")
        candles = self.market.get_candles(b["instrument_id"], b["timeframe"], limit=limit)
        cfg = BacktestConfig(sizing=b["config"]["sizing"], size_value=b["config"]["size_value"],
                             allow_short=b["config"].get("allow_short", False))
        res = backtest_script(b["config"]["script"], candles, cfg)
        self._set_flag(bot_id, "backtest", {"at": now_ms(), "metrics": {k: res.metrics[k] for k in (
            "total_return_pct", "max_drawdown_pct", "sharpe", "trades", "win_rate_pct")}, "bars": len(candles),
            "data_status": candles.status.value})
        if b["state"] in ("validated", "backtested", "paper_tested", "approved", "stopped"):
            self._transition(self.get(bot_id), "backtested", f"backtested by {actor}")
        return res.to_dict()

    def mark_paper_tested(self, bot_id: int, *, actor: str = "ui", min_hours: float = 24.0) -> dict[str, Any]:
        b = self.get(bot_id)
        runtime_h = (b["flags"].get("paper_run_ms", 0)) / 3_600_000
        if runtime_h < min_hours:
            raise ValidationError(f"bot has paper-traded for {runtime_h:.1f} h; at least {min_hours:g} h is required")
        self._transition(b, "paper_tested", f"marked paper-tested by {actor}")
        return self.get(bot_id)

    def approve_live(self, bot_id: int, *, actor: str, confirm: str) -> dict[str, Any]:
        b = self.get(bot_id)
        if actor.split(":")[0] not in ("ui", "cli"):
            raise ValidationError("only a person can approve a bot for live trading")
        if confirm != f"APPROVE {b['name']}":
            raise ValidationError(f"type exactly 'APPROVE {b['name']}' to confirm")
        if b["state"] != "paper_tested":
            raise InvalidState("a bot must be paper-tested before it can be approved")
        self._transition(b, "approved", f"approved for live by {actor}")
        return self.get(bot_id)

    def delete(self, bot_id: int, *, actor: str = "ui") -> None:
        b = self.get(bot_id)
        if b["state"] in ("running", "recovering"):
            raise InvalidState("stop the bot before archiving it")
        self._transition(b, "archived", f"archived by {actor}")

    # -- control ----------------------------------------------------------------------------
    def start(self, bot_id: int, *, actor: str = "ui") -> dict[str, Any]:
        b = self.get(bot_id)
        if b["state"] not in RUNNABLE_FROM:
            raise InvalidState(f"a bot in state '{b['state']}' cannot be started (validate it first)")
        if b["mode"] == "live" and b["state"] != "approved":
            raise InvalidState("live bots must be approved first")
        active = self.db.scalar("SELECT COUNT(*) FROM bots WHERE state = 'running'") or 0
        ok, why = self.risk.check_bot_start(b["account_id"], active)
        if not ok:
            raise InvalidState(why)
        self.db.execute("UPDATE bots SET error_count = 0, last_error = NULL WHERE id = ?", (bot_id,))
        self._set_flag(bot_id, "started_at", now_ms())
        self._transition(b, "running", f"started by {actor}")
        self.log(bot_id, "info", f"started by {actor}")
        return self.get(bot_id)

    def pause(self, bot_id: int, *, actor: str = "ui", reason: str = "") -> dict[str, Any]:
        b = self.get(bot_id)
        self._accumulate_runtime(b)
        self._transition(b, "paused", reason or f"paused by {actor}")
        self.log(bot_id, "info", reason or f"paused by {actor}")
        return self.get(bot_id)

    def resume(self, bot_id: int, *, actor: str = "ui") -> dict[str, Any]:
        return self.start(bot_id, actor=actor)

    def stop(self, bot_id: int, *, actor: str = "ui", cancel_orders: bool = True) -> dict[str, Any]:
        b = self.get(bot_id)
        if b["state"] == "running":
            self._accumulate_runtime(b)
        if cancel_orders:
            for o in self.gateway.portfolio.orders(b["account_id"], open_only=True):
                if o.get("bot_id") == bot_id:
                    self.gateway.cancel(o["id"], actor=f"bot:{bot_id}")
        self._transition(b, "stopped", f"stopped by {actor}")
        self.log(bot_id, "info", f"stopped by {actor}")
        return self.get(bot_id)

    def pause_all(self, *, actor: str, reason: str) -> int:
        n = 0
        for b in self.list():
            if b["state"] in ("running", "recovering"):
                self.pause(b["id"], actor=actor, reason=reason)
                n += 1
        return n

    def emergency_stop(self, *, actor: str, flatten: bool = False) -> dict[str, Any]:
        """Kill switch: halt all trading, pause every bot, cancel every open order, optionally flatten."""
        self.risk.set_global_halt(True, f"emergency stop by {actor}", actor)
        paused = self.pause_all(actor=actor, reason="emergency stop")
        cancelled = self.gateway.cancel_all(actor=actor)
        flattened = []
        if flatten:
            for a in self.db.query("SELECT id FROM accounts"):
                flattened += self.gateway.flatten(a["id"], actor=actor, origin="emergency")
        self.db.audit(actor, "emergency_stop", None, {"paused": paused, "cancelled": cancelled, "flatten": flatten})
        self.bus.publish("risk", {"emergency_stop": True, "paused_bots": paused, "cancelled_orders": cancelled})
        return {"paused_bots": paused, "cancelled_orders": cancelled, "flattened": len(flattened)}

    # -- logs, history -----------------------------------------------------------------------
    def log(self, bot_id: int, level: str, message: str, data: dict[str, Any] | None = None) -> None:
        self.db.execute("INSERT INTO bot_logs(bot_id, ts, level, message, data) VALUES (?,?,?,?,?)",
                        (bot_id, now_ms(), level, message[:2000], dumps(data) if data else None))
        self.bus.publish("bot_log", {"bot_id": bot_id, "level": level, "message": message})

    def logs(self, bot_id: int, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM bot_logs WHERE bot_id = ? ORDER BY id DESC LIMIT ?", (bot_id, limit))
        for r in rows:
            r["data"] = loads(r["data"])
        return rows

    def history(self, bot_id: int) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM bot_state_history WHERE bot_id = ? ORDER BY id", (bot_id,))

    def performance(self, bot_id: int) -> dict[str, Any]:
        b = self.get(bot_id)
        fills = self.db.query("SELECT f.* FROM fills f JOIN orders o ON o.id = f.order_id WHERE o.bot_id = ? ORDER BY f.ts",
                              (bot_id,))
        realized = sum(f["realized_pnl"] for f in fills)
        fees = sum(f["fee"] for f in fills)
        pos = self.gateway.portfolio.positions(b["account_id"])
        p = next((x for x in pos if x["instrument_id"] == b["instrument_id"]), None)
        return {"fills": len(fills), "realized_pnl": realized, "fees": fees,
                "open_qty": p["qty"] if p else 0.0, "unrealized_pnl": p["unrealized_pnl"] if p else 0.0,
                "label": "PAPER" if b["mode"] == "paper" else "LIVE"}

    # -- internals -------------------------------------------------------------------------
    def _transition(self, b: dict[str, Any], to: str, reason: str) -> None:
        frm = b["state"]
        if to not in TRANSITIONS.get(frm, set()):
            raise InvalidState(f"bot cannot go from '{frm}' to '{to}'")
        self.db.execute("UPDATE bots SET state = ?, updated_at = ? WHERE id = ?", (to, now_ms(), b["id"]))
        self._history(b["id"], frm, to, reason)
        self.bus.publish("bot_state", {"bot_id": b["id"], "from": frm, "to": to, "reason": reason})

    def _history(self, bot_id: int, frm: str | None, to: str, reason: str) -> None:
        self.db.execute("INSERT INTO bot_state_history(bot_id, ts, from_state, to_state, reason) VALUES (?,?,?,?,?)",
                        (bot_id, now_ms(), frm, to, reason[:500]))

    def _set_flag(self, bot_id: int, key: str, value: Any) -> None:
        b = self.get(bot_id)
        flags = b["flags"]
        flags[key] = value
        self.db.execute("UPDATE bots SET flags = ? WHERE id = ?", (dumps(flags), bot_id))

    def _accumulate_runtime(self, b: dict[str, Any]) -> None:
        started = b["flags"].get("started_at")
        if started and b["mode"] == "paper":
            self._set_flag(b["id"], "paper_run_ms", b["flags"].get("paper_run_ms", 0) + max(0, now_ms() - started))
            self._set_flag(b["id"], "started_at", None)

    def save_runtime(self, bot_id: int, runtime: dict[str, Any]) -> None:
        self.db.execute("UPDATE bots SET runtime = ? WHERE id = ?", (dumps(runtime), bot_id))


@dataclass
class TickResult:
    bot_id: int
    action: str
    detail: str = ""


class BotRunner:
    """Evaluates running bots on each newly CLOSED bar. Thread-based; driven by the host loop."""

    def __init__(self, bots: BotService, *, tick_s: float = 10.0, clock=now_ms):
        self.bots = bots
        self.tick_s = tick_s
        self.clock = clock  # simulations run on simulated time
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_tick = 0
        self._backoff: dict[int, float] = {}

    # lifecycle
    def recover_after_restart(self) -> list[int]:
        """Bots that were running when the host stopped: reconcile orders, then resume them."""
        self.bots.gateway.reconcile()
        resumed = []
        for b in self.bots.list():
            if b["state"] == "running":
                self.bots._transition(b, "recovering", "host restarted")
                self.bots.log(b["id"], "warning", "host restarted; reconciling before resuming")
                self.bots._transition(self.bots.get(b["id"]), "running", "recovered after restart")
                resumed.append(b["id"])
        return resumed

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="bot-runner", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # the runner itself must survive any single failure
                log.exception("bot runner tick failed")
            self._stop.wait(self.tick_s)

    # one pass over all running bots
    def tick(self) -> list[TickResult]:
        self.last_tick = now_ms()
        self.bots.db.heartbeat("bot_runner", {"tick_s": self.tick_s})
        results = []
        for b in self.bots.list():
            if b["state"] == "error" and b["flags"].get("data_error"):
                self._try_data_recovery(b)
                continue
            if b["state"] != "running":
                continue
            if self._backoff.get(b["id"], 0) > time.monotonic():
                continue
            try:
                results.append(self._run_bot(b))
                if b["error_count"]:
                    self.bots.db.execute("UPDATE bots SET error_count = 0 WHERE id = ?", (b["id"],))
                self._backoff.pop(b["id"], None)
            except OmosError as exc:
                self._fail(b, exc.message)
            except Exception as exc:
                log.exception("bot %s failed", b["id"])
                self._fail(b, f"{type(exc).__name__}: {exc}")
            self.bots.db.execute("UPDATE bots SET last_heartbeat = ? WHERE id = ?", (now_ms(), b["id"]))
        return results

    def _try_data_recovery(self, b: dict[str, Any]) -> None:
        """A bot stopped only because market data was stale resumes by itself once fresh data is back."""
        if self._backoff.get(b["id"], 0) > time.monotonic():
            return
        try:
            c = self.bots.market.get_candles(b["instrument_id"], b["timeframe"], limit=5)
            fresh = len(c) > 0 and not c.quality.get("stale")
        except OmosError:
            fresh = False
        if not fresh:
            self._backoff[b["id"]] = time.monotonic() + min(300.0, max(self.tick_s, 0.0) * 6)
            return
        self.bots._set_flag(b["id"], "data_error", False)
        self.bots.db.execute("UPDATE bots SET error_count = 0, last_error = NULL WHERE id = ?", (b["id"],))
        self.bots._transition(b, "recovering", "market data is available again")
        self.bots._transition(self.bots.get(b["id"]), "running", "resumed after a data outage")
        self.bots.log(b["id"], "info", "market data is available again; resumed")
        self._backoff.pop(b["id"], None)

    def _fail(self, b: dict[str, Any], msg: str) -> None:
        if "market data is stale" in msg or "not enough data" in msg or "provider" in msg.lower():
            self.bots._set_flag(b["id"], "data_error", True)
        count = b["error_count"] + 1
        self.bots.db.execute("UPDATE bots SET error_count = ?, last_error = ? WHERE id = ?", (count, msg[:500], b["id"]))
        self.bots.log(b["id"], "error", f"tick failed ({count}/{MAX_CONSECUTIVE_ERRORS}): {msg}")
        self._backoff[b["id"]] = time.monotonic() + min(300.0, self.tick_s * (2 ** count))
        if count >= MAX_CONSECUTIVE_ERRORS:
            self.bots._transition(self.bots.get(b["id"]), "error", f"{count} consecutive failures: {msg}")

    def _context(self, b: dict[str, Any]) -> TradingContext:
        return TradingContext(self.bots.gateway, origin=f"bot:{b['id']}", account_id=b["account_id"], bot_id=b["id"])

    def _run_bot(self, b: dict[str, Any]) -> TickResult:
        halted, why = self.bots.risk.is_halted(b["account_id"])
        ctx = self._context(b)
        if halted:
            pos = ctx.position(b["instrument_id"])
            if not pos or abs(pos["qty"]) < 1e-12 or b["kind"] not in ("rule", "layered"):
                for o in ctx.open_orders():  # no resting entry may fill while halted
                    ctx.cancel(o["id"])
                if b["kind"] == "layered" and b["runtime"].get("basket"):
                    b["runtime"].pop("basket", None)
                    self.bots.save_runtime(b["id"], b["runtime"])
                self.bots.pause(b["id"], actor="risk_engine", reason=f"paused: {why}")
                return TickResult(b["id"], "paused", why)
            # an open position keeps its protective exits; nothing may add to it
            for o in ctx.open_orders():
                if o["side"] == ("buy" if pos["qty"] > 0 else "sell"):
                    ctx.cancel(o["id"])
            if b["runtime"].get("halt_note") != why:
                self.bots.log(b["id"], "warning", f"trading halted ({why}): only managing the open position's exits")
                b["runtime"]["halt_note"] = why
        cfg = b["config"]
        candles = self.bots.market.get_candles(b["instrument_id"], b["timeframe"], limit=cfg.get("lookback_bars", 300))
        if len(candles) < 20:
            raise OmosError(f"not enough data ({len(candles)} bars)")
        if candles.quality.get("stale"):
            # never act on stale prices: no entries, no exits; after repeated failures the bot goes to 'error'
            raise OmosError(f"market data is stale: {candles.quality.get('stale_reason')}")
        # Work only on CLOSED bars; the forming bar is used for protective stops only.
        closed = candles if candles.last_closed else candles.slice(0, len(candles) - 1)
        last_price = float(candles.close[-1])
        self.bots.gateway.on_price(b["instrument_id"], last_price, candles.status.value)
        runtime = b["runtime"]
        result = self._protective_exits(b, ctx, runtime, last_price)
        if result or halted:
            self.bots.save_runtime(b["id"], runtime)
            return result or TickResult(b["id"], "halted", "exits only")
        if not len(closed):
            return TickResult(b["id"], "wait", "no closed bar yet")
        bar_ts = int(closed.ts[-1])
        if b["last_bar_ts"] is not None and bar_ts <= b["last_bar_ts"]:
            return TickResult(b["id"], "idle", "no new closed bar")
        if b["kind"] == "rule":
            res = self._rule(b, ctx, runtime, closed, bar_ts)
        elif b["kind"] == "layered":
            res = self._layered_entry(b, ctx, runtime, closed, bar_ts)
        elif b["kind"] == "dca":
            res = self._dca(b, ctx, runtime, closed, bar_ts)
        else:
            res = self._grid(b, ctx, runtime, last_price, bar_ts)
        self.bots.db.execute("UPDATE bots SET last_bar_ts = ? WHERE id = ?", (bar_ts, b["id"]))
        self.bots.save_runtime(b["id"], runtime)
        return res

    # protective stops/targets checked every tick against the latest price
    def _protective_exits(self, b, ctx: TradingContext, runtime: dict[str, Any], price: float) -> TickResult | None:
        if b["kind"] == "layered":
            return self._layered_manage(b, ctx, runtime, price)
        if b["kind"] != "rule":
            return None
        pos = ctx.position(b["instrument_id"])
        if not pos or abs(pos["qty"]) < 1e-12:
            return None
        side = 1 if pos["qty"] > 0 else -1
        stop, target = runtime.get("stop"), runtime.get("target")
        hit = None
        if stop is not None and (price <= stop if side > 0 else price >= stop):
            hit = "stop_loss"
        elif target is not None and (price >= target if side > 0 else price <= target):
            hit = "take_profit"
        if not hit:
            return None
        coid = f"bot{b['id']}:{runtime.get('entry_bar', 0)}:{hit}"
        r = ctx.submit(b["instrument_id"], "sell" if side > 0 else "buy", abs(pos["qty"]), client_order_id=coid,
                       reason=hit, reduce_only=True)
        self._report(b, hit, r)
        if r["order"]["status"] == "filled":
            runtime.pop("stop", None)
            runtime.pop("target", None)
        return TickResult(b["id"], hit, r["order"]["status"])

    def _size(self, b, ctx: TradingContext, price: float, stop_dist: float | None) -> float:
        cfg = b["config"]
        eq = ctx.equity()
        if cfg["sizing"] == "fixed_qty":
            return cfg["size_value"]
        if cfg["sizing"] == "fixed_cash":
            return cfg["size_value"] / price
        if cfg["sizing"] == "risk_pct" and stop_dist and stop_dist > 0:
            return eq * cfg["size_value"] / 100 / stop_dist
        return eq * cfg["size_value"] / 100 / price

    def _rule(self, b, ctx: TradingContext, runtime, closed, bar_ts: int) -> TickResult:
        cfg = b["config"]
        res = run(cfg["script"], closed, cfg.get("params"))
        last = len(closed) - 1

        def flag(name: str) -> bool:
            s = res.series(name, as_bool=True)
            return bool(s[last]) if s is not None else False

        def num(abs_name: str, pct_name: str) -> float | None:
            v = res.series(abs_name)
            if v is not None and np.isfinite(v[last]):
                return abs(float(v[last]))
            p = res.series(pct_name)
            if p is not None and np.isfinite(p[last]):
                return abs(float(p[last])) / 100 * float(closed.close[last])
            return None

        pos = ctx.position(b["instrument_id"])
        qty_now = pos["qty"] if pos else 0.0
        price = float(closed.close[last])
        actions = []
        if qty_now > 0 and flag("long_exit"):
            r = ctx.submit(b["instrument_id"], "sell", qty_now, client_order_id=f"bot{b['id']}:{bar_ts}:exit_long",
                           reason="long_exit signal", reduce_only=True)
            actions.append(("exit_long", r))
            qty_now = 0.0 if r["order"]["status"] == "filled" else qty_now
        if qty_now < 0 and flag("short_exit"):
            r = ctx.submit(b["instrument_id"], "buy", -qty_now, client_order_id=f"bot{b['id']}:{bar_ts}:exit_short",
                           reason="short_exit signal", reduce_only=True)
            actions.append(("exit_short", r))
            qty_now = 0.0 if r["order"]["status"] == "filled" else qty_now
        stop_d = num("stop_loss", "stop_loss_pct")
        tgt_d = num("take_profit", "take_profit_pct")
        if qty_now == 0 and flag("long_entry"):
            q = self._size(b, ctx, price, stop_d)
            r = ctx.submit(b["instrument_id"], "buy", q, client_order_id=f"bot{b['id']}:{bar_ts}:entry_long",
                           stop_loss=price - stop_d if stop_d else None, take_profit=price + tgt_d if tgt_d else None,
                           reason="long_entry signal")
            actions.append(("entry_long", r))
            if r["order"]["status"] == "filled":
                fill = r["order"]["avg_fill_price"]
                runtime.update(entry_bar=bar_ts, stop=fill - stop_d if stop_d else None, target=fill + tgt_d if tgt_d else None)
        elif qty_now == 0 and cfg.get("allow_short") and flag("short_entry"):
            q = self._size(b, ctx, price, stop_d)
            r = ctx.submit(b["instrument_id"], "sell", q, client_order_id=f"bot{b['id']}:{bar_ts}:entry_short",
                           stop_loss=price + stop_d if stop_d else None, reason="short_entry signal")
            actions.append(("entry_short", r))
            if r["order"]["status"] == "filled":
                fill = r["order"]["avg_fill_price"]
                runtime.update(entry_bar=bar_ts, stop=fill + stop_d if stop_d else None, target=fill - tgt_d if tgt_d else None)
        for name, r in actions:
            self._report(b, name, r)
        return TickResult(b["id"], ",".join(a for a, _ in actions) or "hold")

    def _dca(self, b, ctx: TradingContext, runtime, closed, bar_ts: int) -> TickResult:
        cfg = b["config"]
        count = runtime.get("bars_seen", 0) + 1
        runtime["bars_seen"] = count
        if (count - 1) % cfg["every_bars"]:
            return TickResult(b["id"], "wait", f"next buy in {cfg['every_bars'] - (count - 1) % cfg['every_bars']} bar(s)")
        if cfg.get("condition"):
            v = evaluate_expression(cfg["condition"], closed)
            if not bool(v[-1]):
                return TickResult(b["id"], "skip", "condition not met")
        invested = runtime.get("invested", 0.0)
        price = float(closed.close[-1])
        installment = cfg["size_value"] if cfg["sizing"] == "fixed_cash" else cfg["size_value"] / 100 * ctx.equity()
        amount = min(installment, cfg["max_invested"] - invested)
        if amount <= installment * 0.05:  # what is left is fees/slippage headroom, not worth an order
            return TickResult(b["id"], "done", "max investment reached")
        # size with 0.5% headroom so spread/slippage on the fill cannot push spending past the cap
        r = ctx.submit(b["instrument_id"], "buy", amount / (price * 1.005), client_order_id=f"bot{b['id']}:{bar_ts}:dca",
                       reason="DCA buy")
        self._report(b, "dca_buy", r)
        if r["order"]["status"] == "filled":
            runtime["invested"] = invested + r["order"]["filled_qty"] * r["order"]["avg_fill_price"]
        return TickResult(b["id"], "dca_buy", r["order"]["status"])

    def _grid(self, b, ctx: TradingContext, runtime, price: float, bar_ts: int) -> TickResult:
        cfg = b["config"]
        levels = list(np.linspace(cfg["lower"], cfg["upper"], cfg["levels"]))
        state: dict[str, Any] = runtime.setdefault("grid", {})
        placed = 0
        for i, lvl in enumerate(levels):
            key = str(i)
            slot = state.get(key)
            if slot and slot.get("order_id"):
                o = self.bots.db.query_one("SELECT status, side FROM orders WHERE id = ?", (slot["order_id"],))
                if o and o["status"] == "filled":
                    # flip: a filled buy at level i becomes a sell one level up, and vice versa
                    slot = {"side": "sell" if o["side"] == "buy" else "buy", "order_id": None, "gen": slot.get("gen", 0) + 1}
                    state[key] = slot
                elif o and o["status"] in ("accepted", "partially_filled", "new"):
                    continue
            if slot is None:
                slot = {"side": "buy" if lvl < price else "sell", "order_id": None, "gen": 0}
                state[key] = slot
            if slot["side"] == "sell":
                pos = ctx.position(b["instrument_id"])
                if not pos or pos["qty"] < cfg["qty_per_level"] - 1e-12:
                    continue  # never sell inventory we do not hold (no shorting in grid)
                target = levels[i + 1] if i + 1 < len(levels) else lvl
            else:
                target = lvl
            if (slot["side"] == "buy" and target >= price) or (slot["side"] == "sell" and target <= price):
                continue  # would fill immediately as a taker; wait for the price to move away
            r = ctx.submit(b["instrument_id"], slot["side"], cfg["qty_per_level"], type="limit", limit_price=round(target, 10),
                           client_order_id=f"bot{b['id']}:grid:{i}:{slot['gen']}:{slot['side']}", reason=f"grid level {i}",
                           reduce_only=slot["side"] == "sell")
            if r["order"]["status"] in ("accepted", "filled", "partially_filled"):
                slot["order_id"] = r["order"]["id"]
                placed += 1
            else:
                self._report(b, "grid_rejected", r)
        return TickResult(b["id"], "grid", f"{placed} order(s) placed")

    # -- layered entries ---------------------------------------------------------------------------------
    def _layered_entry(self, b, ctx: TradingContext, runtime, closed, bar_ts: int) -> TickResult:
        """On a new closed bar: plan and place a basket if flat, idle and every pre-condition holds."""
        cfg = b["config"]
        if runtime.get("basket"):
            return TickResult(b["id"], "manage", "basket open")
        if bar_ts < runtime.get("cooldown_until", 0):
            return TickResult(b["id"], "cooldown", "waiting after the last basket")
        pos = ctx.position(b["instrument_id"])
        if pos and abs(pos["qty"]) > 1e-12:
            return TickResult(b["id"], "skip", "a position is already open in this instrument")
        tf_ms = parse_tf(b["timeframe"]).ms
        age_bars = (self.clock() - bar_ts) / tf_ms - 1
        if age_bars > cfg["stale_after_bars"]:
            return TickResult(b["id"], "skip", f"newest closed bar is {age_bars:.0f} bars old: data too old to enter")
        if cfg["min_volume"] and float(closed.volume[-1]) < cfg["min_volume"]:
            return self._skip(b, runtime, bar_ts, f"volume {float(closed.volume[-1]):g} below {cfg['min_volume']:g}")
        try:
            q = self.bots.market.get_quote(b["instrument_id"])
            if q.bid and q.ask and q.ask > q.bid > 0:
                spread = (q.ask - q.bid) / ((q.ask + q.bid) / 2) * 100
                if spread > cfg["max_spread_pct"]:
                    return self._skip(b, runtime, bar_ts, f"spread {spread:.2f}% wider than {cfg['max_spread_pct']}%")
        except OmosError:
            pass  # no quote: the gateway's own price check still applies
        if cfg["entry_condition"]:
            v = evaluate_expression(cfg["entry_condition"], closed)
            if not bool(v[-1]):
                return TickResult(b["id"], "wait", "entry condition not met")
        from omos.indicators.core import atr as atr_fn
        atr_v = atr_fn(closed.high, closed.low, closed.close, cfg["atr_period"])[-1]
        price = float(closed.close[-1])
        lim = self.bots.risk.limits(b["account_id"])
        from omos.risk.engine import MarketContext
        ceiling = self.bots.risk.layer_ceiling(lim, MarketContext(price, closed.status.value,
                                                                  atr=float(atr_v) if np.isfinite(atr_v) else None))
        snap = self.bots.gateway.portfolio.snapshot(b["account_id"])
        cap = min(snap.equity * lim.max_instrument_exposure_pct / 100, snap.equity * lim.max_total_exposure_pct / 100
                  - snap.exposure(), snap.cash if lim.max_leverage <= 1.0 else float("inf")) * 0.98
        p = layered.plan(cfg, price=price, atr=float(atr_v) if np.isfinite(atr_v) else None, equity=snap.equity,
                         ceiling=ceiling, max_notional=max(cap, 0.0))
        if not p.layers:
            return self._skip(b, runtime, bar_ts, f"no basket: {p.why}")
        side = "buy" if cfg["direction"] == "long" else "sell"
        basket_id = f"bot{b['id']}:{bar_ts}"
        r = ctx.submit_basket(b["instrument_id"], side, [{k: lyr[k] for k in ("type", "limit_price", "qty", "stop_loss")}
                                                          for lyr in p.layers], basket_id=basket_id, reason="layered entry")
        stats = runtime.setdefault("stats", {"baskets": 0, "rejected": 0, "take_profit": 0, "stop": 0, "expired": 0,
                                             "trailing_stop": 0})
        if not r["approved"] or r.get("aborted"):
            stats["rejected"] += 1
            reasons = (r.get("decision") or {}).get("reasons") or [r.get("aborted") or "rejected"]
            self.bots.log(b["id"], "warning", f"basket of {len(p.layers)} rejected as a whole: " + "; ".join(reasons),
                          {"basket_id": basket_id, "plan": p.to_dict()})
            runtime["cooldown_until"] = bar_ts + cfg["cooldown_bars"] * tf_ms
            return TickResult(b["id"], "basket_rejected", "; ".join(reasons)[:200])
        stats["baskets"] += 1
        sign = 1 if cfg["direction"] == "long" else -1
        runtime["basket"] = {"id": basket_id, "orders": [o["id"] for o in r["orders"]], "stop": p.stop,
                             "unit": p.unit, "opened_bar": bar_ts, "expires": bar_ts + cfg["expire_bars"] * tf_ms,
                             "extreme": price, "sign": sign, "trailing": False, "exit_attempts": 0,
                             "planned_layers": len(p.layers), "risk": p.risk}
        self.bots.log(b["id"], "info", f"basket placed: {p.why}; stop {p.stop:g}; risk {p.risk:,.2f}",
                      {"basket_id": basket_id, "plan": p.to_dict()})
        return TickResult(b["id"], "basket_placed", p.why)

    def _skip(self, b, runtime, bar_ts: int, why: str) -> TickResult:
        if runtime.get("last_skip") != why:  # log each new reason once, not every bar
            self.bots.log(b["id"], "info", f"no new basket: {why}")
            runtime["last_skip"] = why
        return TickResult(b["id"], "skip", why)

    def _layered_manage(self, b, ctx: TradingContext, runtime, price: float) -> TickResult | None:
        """Every tick while a basket is open: trailing stop, stop, take-profit, expiry, clean-up."""
        bk = runtime.get("basket")
        if not bk:
            return None
        cfg = b["config"]
        orders = [ctx.order(i) for i in bk["orders"]]
        open_orders = [o for o in orders if o and o["status"] in ("new", "accepted", "partially_filled")]
        pos = ctx.position(b["instrument_id"])
        qty = pos["qty"] if pos else 0.0
        sign = bk["sign"]
        tf_ms = parse_tf(b["timeframe"]).ms
        if abs(qty) < 1e-12:
            if not open_orders:
                return self._close_basket(b, runtime, "closed", "no position and no open layer orders", tf_ms)
            if self.clock() >= bk["expires"]:
                for o in open_orders:
                    ctx.cancel(o["id"])
                return self._close_basket(b, runtime, "expired", "ladder expired unfilled", tf_ms)
            return None
        avg = pos["avg_price"]
        bk["extreme"] = max(bk["extreme"], price) if sign > 0 else min(bk["extreme"], price)
        unit = bk["unit"]
        if cfg["trail_activate"] > 0 and (bk["extreme"] - avg) * sign >= cfg["trail_activate"] * unit:
            trail = bk["extreme"] - sign * cfg["trail_distance"] * unit
            if (trail - bk["stop"]) * sign > 0:
                bk["stop"], bk["trailing"] = layered._round(trail), True
        target = avg + sign * cfg["take_profit"] * unit
        hit = None
        if (price - bk["stop"]) * sign <= 0:
            hit = "trailing_stop" if bk["trailing"] else "stop"
        elif (price - target) * sign >= 0:
            hit = "take_profit"
        if not hit:
            return None
        for o in open_orders:  # no layer may fill after the exit
            ctx.cancel(o["id"])
        bk["exit_attempts"] += 1
        r = ctx.submit(b["instrument_id"], "sell" if sign > 0 else "buy", abs(qty), reduce_only=True,
                       client_order_id=f"{bk['id']}:exit{bk['exit_attempts']}", reason=f"basket {hit.replace('_', ' ')}")
        self._report(b, f"basket_{hit}", r)
        if r["order"]["status"] == "rejected":  # retried on the next tick, with back-off; repeated failure -> error
            raise OmosError(f"basket exit ({hit}) was rejected: {r['order'].get('reason') or 'no reason given'}")
        if r["order"]["status"] == "filled":
            filled = sum(1 for o in orders if o and o["filled_qty"] > 0)
            return self._close_basket(b, runtime, hit, f"{hit.replace('_', ' ')} at {r['order']['avg_fill_price']:g} "
                                      f"after {filled} of {bk['planned_layers']} layer(s) filled", tf_ms)
        return TickResult(b["id"], f"basket_{hit}", r["order"]["status"])

    def _close_basket(self, b, runtime, outcome: str, why: str, tf_ms: int) -> TickResult:
        bk = runtime.pop("basket")
        stats = runtime.setdefault("stats", {})
        stats[outcome] = stats.get(outcome, 0) + 1
        runtime["cooldown_until"] = self.clock() + b["config"]["cooldown_bars"] * tf_ms
        runtime.pop("last_skip", None)
        self.bots.log(b["id"], "info", f"basket {bk['id']} {outcome}: {why}", {"basket_id": bk["id"]})
        return TickResult(b["id"], f"basket_{outcome}", why)

    def _report(self, b, action: str, r: dict[str, Any]) -> None:
        o = r["order"]
        if r.get("duplicate"):
            self.bots.log(b["id"], "info", f"{action}: already submitted (idempotent replay), order {o['id']} is {o['status']}")
            return
        if o["status"] == "rejected":
            self.bots.log(b["id"], "warning", f"{action} rejected by risk engine: {o['reason']}")
        else:
            px = o.get("avg_fill_price")
            self.bots.log(b["id"], "info", f"{action}: {o['side']} {o['qty']:.6g} {o['instrument_id']} -> {o['status']}"
                          + (f" @ {px:.6g}" if px else ""), {"order_id": o["id"]})


def safe_float(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None
