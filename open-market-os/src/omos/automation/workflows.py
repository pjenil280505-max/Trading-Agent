"""Automation workflows: WHEN <trigger> -> steps.

Triggers: schedule | price | indicator | market_open | market_close | event
Steps:    condition | ai_analysis | risk_check | notify | paper_order | run_screener |
          backtest | journal | bot | mcp_tool

Safety:
* workflows are saved DISABLED; a global switch (``automation.enabled``) stops them all;
* every run is logged step by step; ``dry_run`` executes conditions but only *describes*
  side-effect steps;
* paper_order goes through the execution gateway with origin ``workflow`` (risk engine applies)
  and is refused for live accounts;
* per-workflow limits: max runs per day, cooldown between runs, retries per step;
* step templates support only ``{{step_name.field}}`` substitution — no code.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from typing import Any

from omos.errors import NotFound, OmosError, PermissionDenied, ValidationError
from omos.events import EventBus
from omos.marketdata.sessions import market_state
from omos.script.lang import evaluate_expression, parse
from omos.storage.db import Database
from omos.util import dumps, loads, now_ms

log = logging.getLogger(__name__)

TRIGGERS = ("schedule", "price", "indicator", "market_open", "market_close", "event")
STEPS = ("condition", "ai_analysis", "risk_check", "notify", "paper_order", "run_screener", "backtest", "journal", "bot",
         "mcp_tool")
SIDE_EFFECTS = {"ai_analysis", "notify", "paper_order", "journal", "bot", "mcp_tool"}
EVENT_TOPICS = ("order", "risk", "bot_state", "notification", "proposal", "screener_match", "data")
_TPL = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\}\}")


class WorkflowEngine:
    def __init__(self, db: Database, services: dict[str, Any], bus: EventBus | None = None):
        """``services`` provides: market, gateway, portfolio, notifier, screener, bots, agents, mcp_client, backtest_fn."""
        self.db = db
        self.s = services
        self.bus = bus
        self._events: deque[dict[str, Any]] = deque(maxlen=1000)
        self._state: dict[int, dict[str, Any]] = {}
        if bus:
            bus.subscribe(self._on_event)

    def _on_event(self, evt: dict[str, Any]) -> None:
        if evt.get("topic") != "workflow":
            self._events.append(evt)

    # -- definitions -------------------------------------------------------------------
    def validate(self, d: dict[str, Any]) -> dict[str, Any]:
        t = d.get("trigger") or {}
        if t.get("type") not in TRIGGERS:
            raise ValidationError(f"trigger type must be one of {', '.join(TRIGGERS)}")
        if t["type"] == "schedule" and not 1 <= int(t.get("every_minutes", 0)) <= 10_080:
            raise ValidationError("schedule triggers need every_minutes between 1 and 10080")
        if t["type"] == "price":
            if t.get("op") not in (">", "<") or not isinstance(t.get("level"), int | float) or not t.get("instrument"):
                raise ValidationError("price triggers need instrument, op ('>' or '<') and level")
        if t["type"] == "indicator":
            if not t.get("instrument") or not t.get("expression"):
                raise ValidationError("indicator triggers need instrument and expression")
            parse(f"__v = {t['expression']}\n")
        if t["type"] in ("market_open", "market_close") and not t.get("session"):
            raise ValidationError("market open/close triggers need a session id (e.g. xnys)")
        if t["type"] == "event" and t.get("topic") not in EVENT_TOPICS:
            raise ValidationError(f"event triggers need a topic in {', '.join(EVENT_TOPICS)}")
        steps = d.get("steps") or []
        if not 1 <= len(steps) <= 25:
            raise ValidationError("a workflow needs 1–25 steps")
        names = set()
        for i, st in enumerate(steps):
            if st.get("type") not in STEPS:
                raise ValidationError(f"step {i + 1}: type must be one of {', '.join(STEPS)}")
            name = st.get("name") or f"step{i + 1}"
            if not re.match(r"^[a-zA-Z0-9_]{1,40}$", name) or name in names:
                raise ValidationError(f"step {i + 1}: names must be unique letters/digits/_")
            names.add(name)
            st["name"] = name
            if st["type"] == "condition":
                parse(f"__v = {st.get('expression', '')}\n")
                if not st.get("instrument"):
                    raise ValidationError(f"step {name}: condition needs an instrument")
            if st["type"] == "paper_order":
                if st.get("side") not in ("buy", "sell") or not float(st.get("qty", 0)) > 0 or not st.get("account_id"):
                    raise ValidationError(f"step {name}: paper_order needs account_id, side and a positive qty")
            if st["type"] == "bot" and st.get("action") not in ("start", "pause", "stop"):
                raise ValidationError(f"step {name}: bot action must be start, pause or stop")
        lim = d.setdefault("limits", {})
        lim["max_runs_per_day"] = int(lim.get("max_runs_per_day", 24))
        lim["cooldown_minutes"] = float(lim.get("cooldown_minutes", 5))
        lim["max_retries"] = int(lim.get("max_retries", 1))
        if not 1 <= lim["max_runs_per_day"] <= 1440 or lim["cooldown_minutes"] < 0 or not 0 <= lim["max_retries"] <= 5:
            raise ValidationError("limits: 1–1440 runs/day, cooldown >= 0, retries 0–5")
        return d

    def save(self, name: str, definition: dict[str, Any], *, actor: str = "ui") -> dict[str, Any]:
        d = self.validate(definition)
        row = self.db.query_one("SELECT * FROM workflows WHERE name = ?", (name.strip()[:80],))
        ts = now_ms()
        if row:
            version = row["current_version"] + 1
            self.db.execute("UPDATE workflows SET current_version = ?, updated_at = ? WHERE id = ?", (version, ts, row["id"]))
            wid = row["id"]
        else:
            version = 1
            wid = int(self.db.execute("INSERT INTO workflows(name, enabled, current_version, created_at, updated_at) "
                                      "VALUES (?, 0, 1, ?, ?)", (name.strip()[:80], ts, ts)).lastrowid)
        self.db.execute("INSERT INTO workflow_versions(workflow_id, version, definition, created_at) VALUES (?,?,?,?)",
                        (wid, version, dumps(d), ts))
        self.db.audit(actor, "workflow.save", str(wid), {"version": version})
        return self.get(wid)

    def get(self, wid: int, version: int | None = None) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM workflows WHERE id = ?", (wid,))
        if not row:
            raise NotFound(f"workflow {wid} not found")
        v = version or row["current_version"]
        d = self.db.query_one("SELECT definition FROM workflow_versions WHERE workflow_id = ? AND version = ?", (wid, v))
        row["definition"] = loads(d["definition"]) if d else None
        row["versions"] = [r["version"] for r in self.db.query("SELECT version FROM workflow_versions WHERE workflow_id = ? "
                                                              "ORDER BY version", (wid,))]
        return row

    def list(self) -> list[dict[str, Any]]:
        return [self.get(r["id"]) for r in self.db.query("SELECT id FROM workflows ORDER BY id")]

    def set_enabled(self, wid: int, enabled: bool, actor: str = "ui") -> dict[str, Any]:
        self.get(wid)
        self.db.execute("UPDATE workflows SET enabled = ? WHERE id = ?", (int(enabled), wid))
        self.db.audit(actor, "workflow.enable" if enabled else "workflow.disable", str(wid))
        return self.get(wid)

    def disable_all(self, actor: str) -> None:
        self.db.set_setting("automation.enabled", False)
        self.db.audit(actor, "workflow.disable_all")

    def runs(self, wid: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM workflow_runs WHERE workflow_id = ? ORDER BY id DESC LIMIT ?", (wid, limit))
        for r in rows:
            r["log"] = loads(r["log"], [])
            r["trigger_data"] = loads(r["trigger_data"])
        return rows

    # -- triggers ------------------------------------------------------------------------
    def tick(self) -> list[dict[str, Any]]:
        if not self.db.get_setting("automation.enabled", True):
            self._events.clear()
            return []
        events = list(self._events)
        self._events.clear()
        out = []
        for w in self.list():
            if not w["enabled"] or not w["definition"]:
                continue
            try:
                hit = self._triggered(w, events)
            except OmosError as exc:
                log.warning("workflow %s trigger check failed: %s", w["id"], exc.message)
                continue
            if hit is not None:
                out.append(self.run(w["id"], trigger_data=hit))
        return out

    def _triggered(self, w: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
        t = w["definition"]["trigger"]
        st = self._state.setdefault(w["id"], {})
        ts = now_ms()
        typ = t["type"]
        if typ == "schedule":
            last = st.get("last_fire", 0)
            if ts - last >= t["every_minutes"] * 60_000:
                st["last_fire"] = ts
                return {"type": "schedule", "at": ts}
            return None
        if typ in ("price", "indicator"):
            c = self.s["market"].get_candles(t["instrument"], t.get("timeframe", "1m"), limit=300, max_age_s=20)
            if typ == "price":
                price = float(c.close[-1])
                cond = price > t["level"] if t["op"] == ">" else price < t["level"]
                data = {"type": "price", "instrument": t["instrument"], "price": price}
            else:
                closed = c if c.last_closed else c.slice(0, len(c) - 1)
                v = evaluate_expression(t["expression"], closed)
                cond = bool(v[-1])
                data = {"type": "indicator", "instrument": t["instrument"], "bar": int(closed.ts[-1])}
            was = st.get("cond", False)
            st["cond"] = cond
            return data if cond and not was else None  # edge-triggered
        if typ in ("market_open", "market_close"):
            state = market_state(t["session"], ts)
            prev = st.get("state")
            st["state"] = state
            if prev is None:
                return None
            if typ == "market_open" and prev == "closed" and state == "open":
                return {"type": typ, "session": t["session"]}
            if typ == "market_close" and prev == "open" and state == "closed":
                return {"type": typ, "session": t["session"]}
            return None
        if typ == "event":
            for e in events:
                if e["topic"] == t["topic"]:
                    return {"type": "event", "topic": e["topic"], "data": e["data"]}
        return None

    # -- execution ------------------------------------------------------------------------
    def run(self, wid: int, *, dry_run: bool = False, trigger_data: dict[str, Any] | None = None,
            actor: str = "workflow") -> dict[str, Any]:
        w = self.get(wid)
        d = w["definition"]
        lim = d["limits"]
        ts = now_ms()
        if not dry_run:
            day_start = ts - ts % 86_400_000
            today = self.db.scalar("SELECT COUNT(*) FROM workflow_runs WHERE workflow_id = ? AND dry_run = 0 AND started_at >= ?",
                                   (wid, day_start)) or 0
            if today >= lim["max_runs_per_day"]:
                return self._record(w, ts, "skipped", dry_run, trigger_data,
                                    [{"step": "-", "status": "skipped", "message": "daily run limit reached"}])
            last = self.db.scalar("SELECT MAX(started_at) FROM workflow_runs WHERE workflow_id = ? AND dry_run = 0 "
                                  "AND status != 'skipped'", (wid,))
            if last and ts - last < lim["cooldown_minutes"] * 60_000:
                return self._record(w, ts, "skipped", dry_run, trigger_data,
                                    [{"step": "-", "status": "skipped", "message": "cooldown active"}])
        ctx: dict[str, Any] = {"trigger": trigger_data or {}}
        logs: list[dict[str, Any]] = []
        status = "completed"
        for st in d["steps"]:
            attempt = 0
            while True:
                try:
                    result = self._step(st, ctx, dry_run=dry_run, actor=actor)
                    ctx[st["name"]] = result
                    logs.append({"step": st["name"], "type": st["type"], "status": "ok", "result": _short(result)})
                    break
                except PermissionDenied as exc:  # never retried
                    logs.append({"step": st["name"], "type": st["type"], "status": "denied", "message": exc.message})
                    status = "failed"
                    break
                except OmosError as exc:
                    attempt += 1
                    if attempt > lim["max_retries"]:
                        logs.append({"step": st["name"], "type": st["type"], "status": "error", "message": exc.message})
                        status = "failed"
                        break
            if status == "failed":
                break
            if isinstance(ctx.get(st["name"]), dict) and ctx[st["name"]].get("stop"):
                logs.append({"step": st["name"], "type": "control", "status": "stopped",
                             "message": ctx[st["name"]].get("reason", "condition false")})
                status = "stopped"
                break
        return self._record(w, ts, status, dry_run, trigger_data, logs)

    def _record(self, w, ts, status, dry_run, trigger_data, logs) -> dict[str, Any]:
        cur = self.db.execute("INSERT INTO workflow_runs(workflow_id, version, started_at, finished_at, status, dry_run, "
                              "trigger_data, log) VALUES (?,?,?,?,?,?,?,?)",
                              (w["id"], w["current_version"], ts, now_ms(), status, int(dry_run), dumps(trigger_data or {}),
                               dumps(logs)))
        if self.bus:
            self.bus.publish("workflow", {"workflow_id": w["id"], "status": status, "dry_run": dry_run})
        return {"run_id": cur.lastrowid, "workflow_id": w["id"], "status": status, "dry_run": dry_run, "log": logs}

    def _tpl(self, text: str, ctx: dict[str, Any]) -> str:
        def sub(m):
            v = ctx.get(m.group(1), {})
            return str(v.get(m.group(2), "")) if isinstance(v, dict) else ""
        return _TPL.sub(sub, str(text))[:2000]

    def _step(self, st: dict[str, Any], ctx: dict[str, Any], *, dry_run: bool, actor: str) -> dict[str, Any]:
        typ = st["type"]
        if dry_run and typ in SIDE_EFFECTS:
            return {"dry_run": True, "would": f"{typ} {', '.join(f'{k}={v}' for k, v in st.items() if k not in ('type', 'name'))}"[:300]}
        if typ == "condition":
            c = self.s["market"].get_candles(st["instrument"], st.get("timeframe", "1h"), limit=300, max_age_s=30)
            closed = c if c.last_closed else c.slice(0, len(c) - 1)
            v = evaluate_expression(st["expression"], closed)
            ok = bool(v[-1])
            return {"value": ok, "stop": not ok, "reason": f"condition '{st['expression']}' is false"}
        if typ == "risk_check":
            halted, why = self.s["gateway"].risk.is_halted(int(st["account_id"]))
            return {"halted": halted, "stop": halted, "reason": why or "ok"}
        if typ == "notify":
            n = self.s["notifier"].notify(self._tpl(st.get("title", "Workflow"), ctx), self._tpl(st.get("body", ""), ctx),
                                          category="workflow")
            return {"notification_id": n.get("id")}
        if typ == "journal":
            j = self.s["portfolio"].add_journal(self._tpl(st.get("title", "Workflow note"), ctx),
                                                self._tpl(st.get("body", ""), ctx), tags=["workflow"])
            return {"journal_id": j.get("id")}
        if typ == "paper_order":
            from omos.execution.models import OrderRequest

            acct = self.s["portfolio"].account(int(st["account_id"]))
            if acct["kind"] != "paper":
                raise PermissionDenied("workflows may only place orders in PAPER accounts")
            price = self.s["market"].get_quote(st["instrument"]).price
            sl = st.get("stop_loss_pct")
            stop = price * (1 - float(sl) / 100) if sl and st["side"] == "buy" else price * (1 + float(sl) / 100) if sl else None
            req = OrderRequest.from_dict({"account_id": acct["id"], "instrument_id": st["instrument"], "side": st["side"],
                                          "qty": float(st["qty"]), "origin": "workflow", "stop_loss": stop,
                                          "reason": "workflow step",
                                          "client_order_id": f"wf:{st['name']}:{ctx.get('trigger', {}).get('at') or now_ms()}"})
            r = self.s["gateway"].submit(req, actor=actor)
            return {"order_id": r["order"]["id"], "status": r["order"]["status"], "reason": r["order"].get("reason")}
        if typ == "run_screener":
            res = self.s["screener"].run_saved(int(st["screener_id"]))
            if res["results"] and self.bus:
                self.bus.publish("screener_match", {"screener_id": st["screener_id"],
                                                    "matched": [r["instrument_id"] for r in res["results"]]})
            return {"matched": [r["instrument_id"] for r in res["results"]], "count": res["matched"],
                    "stop": bool(st.get("stop_if_empty")) and not res["results"], "reason": "no matches"}
        if typ == "backtest":
            r = self.s["backtest_fn"](st.get("source") or self.s["strategy_source"](int(st["strategy_id"])),
                                      st["instrument"], st.get("timeframe", "1d"))
            m = r["metrics"]
            return {k: m.get(k) for k in ("total_return_pct", "sharpe", "max_drawdown_pct", "trades")}
        if typ == "ai_analysis":
            out = self.s["agents"].analyze(int(st["provider_id"]), st["instrument"], timeframe=st.get("timeframe", "1d"),
                                           role=st.get("role", "market_analyst"))
            res = out["result"]
            return {"summary": res.get("summary", ""), "stance": res.get("stance"), "confidence": res.get("confidence")}
        if typ == "bot":
            fn = {"start": self.s["bots"].start, "pause": self.s["bots"].pause, "stop": self.s["bots"].stop}[st["action"]]
            b = fn(int(st["bot_id"]), actor=actor)
            return {"bot_id": b["id"], "state": b["state"]}
        if typ == "mcp_tool":
            r = self.s["mcp_client"].call(int(st["server_id"]), st["tool"], st.get("args") or {}, actor=actor)
            return {"is_error": r["is_error"], "content": r["content"][:500]}
        raise ValidationError(f"unknown step type {typ}")


def _short(v: Any) -> Any:
    text = dumps(v)
    return v if len(text) < 2000 else {"truncated": text[:2000]}
