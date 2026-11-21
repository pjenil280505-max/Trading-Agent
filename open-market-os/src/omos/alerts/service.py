"""Alerts and notifications.

Alert kinds:
  price_above / price_below — last price vs a level (edge-triggered: re-arms when false again)
  price_cross               — price crosses a level in either direction
  pct_change                — move of at least ``pct`` % over ``bars`` bars
  expression                — an OMS Script formula that is true on the last CLOSED bar
Frequency: once | once_per_bar | every (rate-limited to one per minute)

Notifications are stored locally and pushed to connected UIs; desktop notifications use the
OS notifier when one is available. Nothing is sent to any cloud service.
"""

from __future__ import annotations

import logging
import shutil
import subprocess  # noqa: S404 - only used with a fixed argv for the local desktop notifier
from typing import Any

import numpy as np

from omos.errors import NotFound, ValidationError
from omos.events import EventBus
from omos.marketdata.service import MarketDataService
from omos.marketdata.timeframes import parse as parse_tf
from omos.script.lang import evaluate_expression, parse
from omos.storage.db import Database
from omos.util import dumps, loads, now_ms

log = logging.getLogger(__name__)

KINDS = ("price_above", "price_below", "price_cross", "pct_change", "expression")
FREQUENCIES = ("once", "once_per_bar", "every")


class NotificationService:
    def __init__(self, db: Database, bus: EventBus):
        self.db = db
        self.bus = bus

    def notify(self, title: str, body: str = "", *, level: str = "info", category: str = "general",
               data: dict[str, Any] | None = None, desktop: bool | None = None) -> dict[str, Any]:
        cur = self.db.execute("INSERT INTO notifications(ts, level, category, title, body, data) VALUES (?,?,?,?,?,?)",
                              (now_ms(), level, category, title[:200], body[:2000], dumps(data or {})))
        n = self.db.query_one("SELECT * FROM notifications WHERE id = ?", (cur.lastrowid,)) or {}
        self.bus.publish("notification", n)
        want_desktop = desktop if desktop is not None else bool(self.db.get_setting("notify.desktop", False))
        if want_desktop:
            self._desktop(title, body)
        return n

    @staticmethod
    def _desktop(title: str, body: str) -> None:
        exe = shutil.which("notify-send")
        if not exe:
            return
        try:
            subprocess.run([exe, "--app-name=Open Market OS", title[:100], body[:300]], timeout=5, check=False)  # noqa: S603
        except (OSError, subprocess.SubprocessError):
            log.debug("desktop notification failed", exc_info=True)

    def list(self, *, unread_only: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM notifications" + (" WHERE read = 0" if unread_only else "") + " ORDER BY id DESC LIMIT ?"
        rows = self.db.query(sql, (limit,))
        for r in rows:
            r["data"] = loads(r["data"], {})
        return rows

    def mark_read(self, ids: list[int] | None = None) -> None:
        if ids is None:
            self.db.execute("UPDATE notifications SET read = 1")
        else:
            self.db.executemany("UPDATE notifications SET read = 1 WHERE id = ?", [(i,) for i in ids])


class AlertService:
    def __init__(self, db: Database, market: MarketDataService, notifier: NotificationService):
        self.db = db
        self.market = market
        self.notifier = notifier

    # -- CRUD ------------------------------------------------------------------------
    def create(self, *, name: str, instrument_id: str, kind: str, params: dict[str, Any], timeframe: str = "1m",
               frequency: str = "once", expires_at: int | None = None, notify: dict[str, Any] | None = None) -> dict[str, Any]:
        self._validate(kind, params, timeframe, frequency)
        self.market.split_id(instrument_id)
        ts = now_ms()
        cur = self.db.execute(
            "INSERT INTO alerts(name, instrument_id, timeframe, kind, params, state, frequency, expires_at, notify, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ((name or f"{kind} {instrument_id}")[:100], instrument_id, timeframe, kind, dumps(params), "active", frequency,
             expires_at, dumps(notify or {"sound": True, "desktop": False}), ts, ts))
        return self.get(int(cur.lastrowid))

    def _validate(self, kind: str, params: dict[str, Any], timeframe: str, frequency: str) -> None:
        if kind not in KINDS:
            raise ValidationError(f"alert kind must be one of {', '.join(KINDS)}")
        if frequency not in FREQUENCIES:
            raise ValidationError(f"frequency must be one of {', '.join(FREQUENCIES)}")
        parse_tf(timeframe)
        if kind in ("price_above", "price_below", "price_cross"):
            lvl = params.get("level")
            if not isinstance(lvl, int | float) or lvl <= 0:
                raise ValidationError("price alerts need a positive 'level'")
        elif kind == "pct_change":
            if not isinstance(params.get("pct"), int | float) or params["pct"] == 0:
                raise ValidationError("pct_change alerts need a non-zero 'pct'")
            if not 1 <= int(params.get("bars", 1)) <= 1000:
                raise ValidationError("'bars' must be between 1 and 1000")
        elif kind == "expression":
            expr = params.get("expression", "")
            if not expr or len(expr) > 2000:
                raise ValidationError("expression alerts need a formula (max 2000 characters)")
            parse(f"__v = {expr}\n")

    def get(self, alert_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,))
        if not row:
            raise NotFound(f"alert {alert_id} not found")
        row["params"] = loads(row["params"], {})
        row["notify"] = loads(row["notify"], {})
        return row

    def list(self) -> list[dict[str, Any]]:
        return [self.get(r["id"]) for r in self.db.query("SELECT id FROM alerts ORDER BY id DESC")]

    def update_state(self, alert_id: int, state: str, *, snooze_minutes: int | None = None) -> dict[str, Any]:
        self.get(alert_id)
        if state not in ("active", "disabled", "snoozed"):
            raise ValidationError("state must be active, disabled or snoozed")
        until = now_ms() + int(snooze_minutes or 60) * 60_000 if state == "snoozed" else None
        self.db.execute("UPDATE alerts SET state = ?, snoozed_until = ?, updated_at = ? WHERE id = ?",
                        (state, until, now_ms(), alert_id))
        return self.get(alert_id)

    def delete(self, alert_id: int) -> None:
        self.db.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))

    def history(self, alert_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if alert_id:
            return self.db.query("SELECT * FROM alert_events WHERE alert_id = ? ORDER BY id DESC LIMIT ?", (alert_id, limit))
        return self.db.query("SELECT * FROM alert_events ORDER BY id DESC LIMIT ?", (limit,))

    def acknowledge(self, event_id: int) -> None:
        self.db.execute("UPDATE alert_events SET acknowledged = 1 WHERE id = ?", (event_id,))

    def test_fire(self, alert_id: int) -> dict[str, Any]:
        a = self.get(alert_id)
        return self._fire(a, value=a.get("last_value"), message=f"TEST: {a['name']}", test=True)

    # -- evaluation ----------------------------------------------------------------------
    def evaluate_all(self) -> list[dict[str, Any]]:
        fired = []
        ts = now_ms()
        by_series: dict[tuple[str, str], Any] = {}
        for a in self.list():
            if a["state"] == "snoozed" and (a["snoozed_until"] or 0) <= ts:
                self.db.execute("UPDATE alerts SET state = 'active', snoozed_until = NULL WHERE id = ?", (a["id"],))
                a["state"] = "active"
            if a["state"] != "active":
                continue
            if a["expires_at"] and a["expires_at"] < ts:
                self.db.execute("UPDATE alerts SET state = 'expired', updated_at = ? WHERE id = ?", (ts, a["id"]))
                continue
            key = (a["instrument_id"], a["timeframe"])
            try:
                if key not in by_series:
                    by_series[key] = self.market.get_candles(a["instrument_id"], a["timeframe"], limit=300, max_age_s=20)
                ev = self.evaluate(a, by_series[key])
                if ev:
                    fired.append(ev)
            except Exception as exc:  # one broken alert must not stop the others
                log.warning("alert %s evaluation failed: %s", a["id"], exc)
                self.db.execute("UPDATE alerts SET updated_at = ? WHERE id = ?", (ts, a["id"]))
        return fired

    def evaluate(self, a: dict[str, Any], candles) -> dict[str, Any] | None:
        if not len(candles):
            return None
        p = a["params"]
        price = float(candles.close[-1])
        prev_value = a["last_value"]
        closed = candles if candles.last_closed else candles.slice(0, len(candles) - 1)
        bar_ts = int(closed.ts[-1]) if len(closed) else int(candles.ts[-1])
        cond, value, msg = False, price, ""
        if a["kind"] == "price_above":
            cond, msg = price > p["level"], f"{a['instrument_id']} is above {p['level']:g} (now {price:g})"
        elif a["kind"] == "price_below":
            cond, msg = price < p["level"], f"{a['instrument_id']} is below {p['level']:g} (now {price:g})"
        elif a["kind"] == "price_cross":
            lvl = p["level"]
            if prev_value is not None:
                cond = (prev_value - lvl) * (price - lvl) < 0
            msg = f"{a['instrument_id']} crossed {lvl:g} (now {price:g})"
        elif a["kind"] == "pct_change":
            n = int(p.get("bars", 1))
            if len(closed) > n:
                ch = (float(closed.close[-1]) / float(closed.close[-1 - n]) - 1) * 100
                value = ch
                cond = ch >= p["pct"] if p["pct"] > 0 else ch <= p["pct"]
                msg = f"{a['instrument_id']} moved {ch:+.2f}% over {n} bar(s)"
        elif a["kind"] == "expression":
            if len(closed) >= 2:
                v = evaluate_expression(p["expression"], closed)
                value = float(v[-1]) if np.isfinite(v[-1]) else 0.0
                cond = bool(v[-1])
                was = bool(v[-2])
                if a["frequency"] != "every":
                    cond = cond and not was  # edge: false -> true on the last closed bar
                msg = f"{a['name']}: condition true on {a['instrument_id']} {a['timeframe']}"
        armed = self._armed(a, cond)
        self.db.execute("UPDATE alerts SET last_value = ?, updated_at = ? WHERE id = ?", (value, now_ms(), a["id"]))
        if not cond or not armed:
            return None
        if a["frequency"] == "once_per_bar" and a["last_bar_ts"] == bar_ts:
            return None
        if a["frequency"] == "every" and a["last_triggered_at"] and now_ms() - a["last_triggered_at"] < 60_000:
            return None
        return self._fire(a, value=value, message=msg, bar_ts=bar_ts)

    def _armed(self, a: dict[str, Any], cond: bool) -> bool:
        """Edge-trigger price_above/below: after firing, wait until the condition is false once."""
        if a["kind"] not in ("price_above", "price_below") or a["frequency"] == "once":
            return True
        st = a["params"].get("_disarmed", False)
        if st and not cond:
            a["params"]["_disarmed"] = False
            self.db.execute("UPDATE alerts SET params = ? WHERE id = ?", (dumps(a["params"]), a["id"]))
            return False
        return not st

    def _fire(self, a: dict[str, Any], *, value: float | None, message: str, bar_ts: int | None = None,
              test: bool = False) -> dict[str, Any]:
        ts = now_ms()
        cur = self.db.execute("INSERT INTO alert_events(alert_id, ts, message, value, data) VALUES (?,?,?,?,?)",
                              (a["id"], ts, message, value, dumps({"test": test, "bar_ts": bar_ts})))
        if not test:
            new_state = "triggered" if a["frequency"] == "once" else "active"
            params = a["params"]
            if a["kind"] in ("price_above", "price_below") and a["frequency"] != "once":
                params["_disarmed"] = True
            self.db.execute("UPDATE alerts SET state = ?, last_triggered_at = ?, trigger_count = trigger_count + 1, "
                            "last_bar_ts = ?, params = ?, updated_at = ? WHERE id = ?",
                            (new_state, ts, bar_ts, dumps(params), ts, a["id"]))
        notify = a.get("notify") or {}
        self.notifier.notify(f"Alert: {a['name']}", message, level="warning", category="alert",
                             data={"alert_id": a["id"], "event_id": cur.lastrowid, "sound": notify.get("sound", True),
                                   "test": test}, desktop=notify.get("desktop"))
        return {"alert_id": a["id"], "event_id": cur.lastrowid, "message": message, "value": value, "test": test}
