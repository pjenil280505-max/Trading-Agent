"""The execution host: wires every service together and runs the background loops.

Loops (each a daemon thread, each with its own heartbeat):
  market   — polls subscribed (instrument, timeframe) pairs, pushes candle updates, feeds paper fills
  bots     — BotRunner: evaluates running bots on each closed bar
  alerts   — evaluates active alerts
  automation — workflow triggers + scheduled screeners
  maintenance — provider health, equity snapshots, daily backup, token cleanup
  watchdog — restarts any loop whose thread died and records it

24/7 means "while this computer is on, awake and online". The host cannot run while the
machine sleeps; see docs/BOT_GUIDE.md for power/sleep settings per OS.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from omos import __version__
from omos.ai.agents import AgentService, EvidenceBuilder
from omos.ai.providers import AIProviderService
from omos.alerts.service import AlertService, NotificationService
from omos.automation.workflows import WorkflowEngine
from omos.backtest.engine import BacktestConfig, backtest_script
from omos.backtest.strategies import StrategyService
from omos.bots.service import BotRunner, BotService
from omos.charts import ChartService
from omos.config import HostConfig
from omos.device.profile import detect
from omos.errors import OmosError
from omos.events import EventBus
from omos.execution.gateway import ExecutionGateway
from omos.marketdata.service import MarketDataService
from omos.marketdata.sessions import get_session
from omos.mcp.client import MCPClientService
from omos.plugins.manager import PluginManager
from omos.portfolio.service import PortfolioService, PriceBook
from omos.research.calendar import CalendarService
from omos.research.fundamentals import FundamentalsService
from omos.research.news import NewsService
from omos.risk.engine import RiskEngine
from omos.screener.service import ScreenerService, WatchlistService
from omos.security.auth import AuthService
from omos.security.secrets import SecretStore
from omos.storage.backup import backup_database, list_backups, recover_corrupted
from omos.storage.db import Database
from omos.util import now_ms

log = logging.getLogger(__name__)


class Loop:
    def __init__(self, name: str, fn: Callable[[], None], interval_s: float, db: Database):
        self.name, self.fn, self.interval, self.db = name, fn, interval_s, db
        self.stop_evt = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_ok: int | None = None
        self.last_error: str | None = None
        self.runs = 0
        self.restarts = 0

    def start(self) -> None:
        self.stop_evt.clear()
        self.thread = threading.Thread(target=self._run, name=f"omos-{self.name}", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_evt.is_set():
            t0 = time.monotonic()
            try:
                self.fn()
                self.last_ok = now_ms()
                self.runs += 1
                self.db.heartbeat(f"loop:{self.name}", {"runs": self.runs})
            except Exception as exc:  # keep running; record and back off a little
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                log.exception("loop %s failed", self.name)
            self.stop_evt.wait(max(0.5, self.interval - (time.monotonic() - t0)))

    def alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "alive": self.alive(), "interval_s": self.interval, "last_ok": self.last_ok,
                "last_error": self.last_error, "runs": self.runs, "restarts": self.restarts}


class Host:
    def __init__(self, cfg: HostConfig, *, start_loops: bool = True, providers: dict | None = None):
        self.cfg = cfg
        cfg.ensure_dirs()
        restored = recover_corrupted(cfg.db_path, cfg.backups_path)
        if restored:
            log.error("database was corrupt; restored from backup %s", restored)
        self.db = Database(cfg.db_path)
        self.started_at = now_ms()
        self.bus = EventBus()
        self.secrets = SecretStore(cfg.data_path)
        self.auth = AuthService(self.db)
        self.market = MarketDataService(self.db, providers=providers)
        self.prices = PriceBook()
        self.portfolio = PortfolioService(self.db, self.prices)
        self.risk = RiskEngine(self.db)
        self.gateway = ExecutionGateway(self.db, self.risk, self.portfolio, self.prices, self.quote, publish=self.bus.publish)
        self.bots = BotService(self.db, self.market, self.gateway, self.risk, self.bus)
        self.runner = BotRunner(self.bots, tick_s=10.0)
        self.notifier = NotificationService(self.db, self.bus)
        self.alerts = AlertService(self.db, self.market, self.notifier)
        self.watchlists = WatchlistService(self.db)
        self.screener = ScreenerService(self.db, self.market)
        self.strategies = StrategyService(self.db)
        self.charts = ChartService(self.db)
        self.news = NewsService(self.db)
        self.fundamentals = FundamentalsService(self.db)
        self.calendar = CalendarService(self.db, self.market, self.secrets)
        self.ai = AIProviderService(self.db, self.secrets)
        self.evidence = EvidenceBuilder(self.market, self.news, self.fundamentals)
        self.agents = AgentService(self.db, self.ai, self.evidence, gateway=self.gateway, backtest_fn=self.run_backtest)
        self.mcp_client = MCPClientService(self.db)
        self.plugins = PluginManager(self.db, cfg.plugins_path)
        self.workflows = WorkflowEngine(self.db, {
            "market": self.market, "gateway": self.gateway, "portfolio": self.portfolio, "notifier": self.notifier,
            "screener": self.screener, "bots": self.bots, "agents": self.agents, "mcp_client": self.mcp_client,
            "backtest_fn": self.run_backtest, "strategy_source": lambda sid: self.strategies.get(sid)["source"]}, self.bus)
        self.device = detect(str(cfg.data_path))
        self.load_plugin_providers()
        self.subscriptions: dict[tuple[str, str], dict[str, Any]] = {}
        self._sub_lock = threading.Lock()
        self.loops: dict[str, Loop] = {}
        self._bootstrap_defaults()
        if start_loops:
            self.start()

    def load_plugin_providers(self) -> list[str]:
        """(Re)register REST data providers from enabled plugins."""
        import json as _json

        from omos.plugins.rest_provider import RestCandleProvider

        loaded = []
        for name in [n for n, p in self.market.providers.items() if getattr(p, "spec", None) is not None]:
            self.market.unregister_provider(name)
        for p in self.plugins.enabled("data_provider_rest"):
            try:
                spec = _json.loads(self.plugins.read_entry(p["id"]))
                hosts = [x.split(":", 1)[1] for x in p["granted_permissions"] if x.startswith("network:")]
                prov = RestCandleProvider(spec, hosts)
                self.market.register_provider(prov)
                loaded.append(prov.name)
            except OmosError as exc:
                log.warning("plugin provider %s not loaded: %s", p["id"], exc.message)
        return loaded

    # -- defaults ------------------------------------------------------------------------
    def _bootstrap_defaults(self) -> None:
        if not self.db.query_one("SELECT 1 FROM accounts LIMIT 1"):
            self.portfolio.create_account("Paper", 10_000.0)
        if not self.db.query_one("SELECT 1 FROM watchlists LIMIT 1"):
            wl = self.watchlists.create("Starter")
            for iid in ("OKX:BTC-USDT", "OKX:ETH-USDT", "YAHOO:SPY", "YAHOO:AAPL", "YAHOO:^NSEI", "YAHOO:GC=F",
                        "YAHOO:EURUSD=X"):
                self.watchlists.add(wl["id"], iid)
        if self.db.get_setting("trading.live_enabled") is None:
            self.db.set_setting("trading.live_enabled", False)

    # -- quotes for the gateway -------------------------------------------------------------
    def quote(self, instrument_id: str) -> dict[str, Any]:
        q = self.market.get_quote(instrument_id)
        inst = self.market.instrument(instrument_id)
        atr = None
        try:
            c = self.market.get_candles(instrument_id, "1h" if inst.session == "24x7" else "1d", limit=60, max_age_s=300)
            if len(c) > 20:
                from omos.indicators.core import atr as atr_fn
                v = atr_fn(c.high, c.low, c.close, 14)[-1]
                atr = float(v) if np.isfinite(v) else None
        except OmosError:
            pass
        return {"price": q.price, "status": q.status.value, "bid": q.bid, "ask": q.ask,
                "session_open": get_session(inst.session).is_open(now_ms()), "atr": atr}

    def run_backtest(self, source: str, instrument_id: str, timeframe: str, *, limit: int = 2000,
                     config: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        candles = self.market.get_candles(instrument_id, timeframe, limit=limit, max_age_s=120)
        inst = self.market.instrument(instrument_id)
        cfg = BacktestConfig(**{**(config or {}), "session": inst.session})
        res = backtest_script(source, candles, cfg, params).to_dict()
        return res

    # -- live candle subscriptions ------------------------------------------------------------
    def subscribe(self, instrument_id: str, timeframe: str) -> None:
        with self._sub_lock:
            self.subscriptions.setdefault((instrument_id, timeframe), {"since": now_ms(), "last_ts": None})["touched"] = now_ms()

    def _poll_market(self) -> None:
        cutoff = now_ms() - 120_000  # clients re-subscribe every minute; drop stale subscriptions
        with self._sub_lock:
            for k in [k for k, v in self.subscriptions.items() if v.get("touched", 0) < cutoff]:
                self.subscriptions.pop(k, None)
            subs = list(self.subscriptions.items())
        for (iid, tf), state in subs:
            try:
                c = self.market.get_candles(iid, tf, limit=3)
            except OmosError as exc:
                self.bus.publish("data", {"instrument": iid, "timeframe": tf, "error": exc.message})
                continue
            if not len(c):
                continue
            last = c.rows()[-1]
            self.gateway.on_price(iid, float(last[4]), c.status.value)
            self.bus.publish("candle", {"instrument": iid, "timeframe": tf, "bar": last, "closed": c.last_closed,
                                        "status": c.status.value, "source": c.source})
            state["last_ts"] = last[0]

    def _maintenance(self) -> None:
        self.market.persist_health()
        for a in self.db.query("SELECT id FROM accounts"):
            try:
                eq = self.portfolio.record_equity(a["id"])
                self.risk.monitor(a["id"], eq)
            except OmosError:
                log.exception("equity snapshot failed")
        self.auth.cleanup()
        last = self.db.get_setting("backup.last_auto", 0)
        if now_ms() - last > 86_400_000 and self.db.get_setting("backup.auto", True):
            path = backup_database(self.cfg.db_path, self.cfg.backups_path, label="auto")
            self.db.set_setting("backup.last_auto", now_ms())
            keep = int(self.db.get_setting("backup.keep", 14))
            autos = sorted(self.cfg.backups_path.glob("omos-*-auto.db"))
            for old in autos[:-keep]:
                old.unlink(missing_ok=True)
            log.info("automatic backup written to %s", path)
        self.db.heartbeat("host", {"version": __version__, "pid": __import__("os").getpid()})

    def _automation(self) -> None:
        self.workflows.tick()
        for sid in self.screener.due():
            try:
                res = self.screener.run_saved(sid)
                if res["results"]:
                    self.bus.publish("screener_match", {"screener_id": sid, "matched": [r["instrument_id"] for r in res["results"]]})
            except OmosError as exc:
                log.warning("scheduled screener %s failed: %s", sid, exc.message)

    def _watchdog(self) -> None:
        for name, loop in self.loops.items():
            if name == "watchdog":
                continue
            if not loop.alive():
                loop.restarts += 1
                log.error("loop %s died; restarting (restart #%s)", name, loop.restarts)
                self.notifier.notify(f"Background task '{name}' restarted", "The watchdog restarted it after a failure.",
                                     level="warning", category="system")
                loop.start()

    # -- lifecycle -------------------------------------------------------------------------------
    def start(self) -> None:
        resumed = self.runner.recover_after_restart()
        if resumed:
            self.notifier.notify("Bots resumed after restart", f"{len(resumed)} bot(s) were reconciled and resumed.",
                                 category="bot")
        poll = float(self.db.get_setting("data.poll_seconds", self.device["profile"]["poll_seconds"]))
        self.loops = {
            "market": Loop("market", self._poll_market, max(2.0, poll), self.db),
            "bots": Loop("bots", self.runner.tick, 10.0, self.db),
            "alerts": Loop("alerts", self.alerts.evaluate_all, 15.0, self.db),
            "automation": Loop("automation", self._automation, 20.0, self.db),
            "maintenance": Loop("maintenance", self._maintenance, 60.0, self.db),
        }
        for lp in self.loops.values():
            lp.start()
        self.loops["watchdog"] = Loop("watchdog", self._watchdog, 15.0, self.db)
        self.loops["watchdog"].start()
        log.info("Open Market OS host %s started (data: %s)", __version__, self.cfg.data_path)

    def stop(self) -> None:
        if getattr(self, "_stopped", False):  # idempotent: the shutdown hook and `finally` may both call it
            return
        self._stopped = True
        for lp in self.loops.values():
            lp.stop_evt.set()
        for lp in self.loops.values():
            if lp.thread:
                lp.thread.join(timeout=10)
        self.market.persist_health()
        self.db.heartbeat("host", {"stopped": True})
        log.info("host stopped cleanly")

    def status(self) -> dict[str, Any]:
        hb = {r["component"]: r["ts"] for r in self.db.query("SELECT component, ts FROM heartbeats")}
        return {
            "version": __version__, "started_at": self.started_at, "uptime_s": (now_ms() - self.started_at) / 1000,
            "data_dir": str(self.cfg.data_path), "bind": self.cfg.effective_bind(), "port": self.cfg.port,
            "remote_access": self.cfg.allow_remote, "loops": [lp.status() for lp in self.loops.values()],
            "heartbeats": hb, "providers": self.market.health_report(), "subscriptions": len(self.subscriptions),
            "secrets": self.secrets.status(), "device": self.device, "live_trading_enabled": bool(self.db.get_setting("trading.live_enabled", False)),
            "emergency_stop": self.risk.global_halt(), "schema_version": self.db.schema_version(),
            "backups": list_backups(self.cfg.backups_path)[:5],
            "automation_enabled": bool(self.db.get_setting("automation.enabled", True)),
            "note_24x7": "Bots and alerts run only while this computer is on, awake and online.",
        }
