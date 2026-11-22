"""Stress-test a bot on synthetic market scenarios before it runs.

Each scenario runs the bot's real logic - the same BotRunner, risk engine, gateway and paper broker -
in a throwaway database on generated prices, bar by bar, with the price moving through each bar's
open, low/high and close so stops and limit orders trigger as they would live. Nothing touches the
user's accounts. Results are labelled SIMULATED.

Scenarios: trending, dip and recovery, reversal/crash, high volatility, gap through the ladder,
sideways chop, spread blow-out, thin market with partial fills, data outage, host restart mid-basket,
and a broker that rejects orders.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from omos.errors import ProviderError
from omos.marketdata.models import AssetClass, Candles, DataStatus, Instrument, Quote
from omos.marketdata.providers.base import Provider, ProviderInfo
from omos.marketdata.timeframes import parse as parse_tf

START = 1_700_000_000_000
WARMUP = 120


def _path(kind: str, warmup: int, bars: int, seed: int) -> tuple[np.ndarray, dict[str, Any]]:
    """Close prices (warm-up, then the scenario), plus effects keyed by bar index within the scenario."""
    rng = np.random.default_rng(seed)
    n = warmup + bars
    noise = rng.normal(0, 0.004, n)
    u = np.clip((np.arange(n, dtype=float) - warmup) / bars, -1, 1)  # <0 during warm-up, 0..1 in the scenario
    effects: dict[str, Any] = {}
    down_up = np.where(u < 0, 0.0, np.where(u < 0.45, -0.004, 0.005))
    if kind == "trend_up":
        drift = np.where(u < 0, 0.0, 0.0025)
    elif kind == "dip_and_recover":
        drift = down_up
    elif kind == "crash":
        drift = np.where(u < 0.1, 0.0, -0.01)
    elif kind == "high_volatility":
        noise = np.where(u < 0, noise, rng.normal(0, 0.035, n))
        drift = np.zeros(n)
    elif kind == "gap_down":
        drift = np.zeros(n)
        effects["gap_at"], effects["gap_pct"] = int(bars * 0.3), -12.0
    elif kind == "sideways":
        drift = np.where(u < 0, 0.0, 0.004 * np.sin(np.arange(n) / 6.0))
    elif kind == "spread_blowout":
        drift = down_up
        effects["wide_spread"] = (int(bars * 0.1), int(bars * 0.6), 3.0)
    elif kind == "thin_market":
        drift = down_up
        effects["volume"], effects["fill_fraction"] = 3.0, 0.3
    elif kind == "data_outage":
        drift = down_up
        effects["outage"] = (int(bars * 0.3), int(bars * 0.42))
    elif kind == "restart":
        drift = down_up
        effects["restart_at"] = int(bars * 0.3)
    elif kind == "rejected_orders":
        drift = down_up
        effects["reject_from"] = int(bars * 0.25)
    else:
        raise ValueError(kind)
    closes = 100.0 * np.exp(np.cumsum(drift + noise))
    if "gap_at" in effects:
        closes[warmup + effects["gap_at"]:] *= 1 + effects["gap_pct"] / 100
    return closes, effects


SCENARIOS: dict[str, str] = {
    "trend_up": "Steady uptrend",
    "dip_and_recover": "Decline, then recovery",
    "crash": "Reversal into a sustained crash",
    "high_volatility": "High volatility without direction",
    "gap_down": "Price gaps 12% down through the ladder",
    "sideways": "Sideways chop",
    "spread_blowout": "Quoted spread widens to 3%",
    "thin_market": "Thin market: tiny volume, orders fill in parts",
    "data_outage": "Market data unavailable for a while",
    "restart": "The host restarts while a basket is open",
    "rejected_orders": "The broker rejects new orders",
}


class ScenarioProvider(Provider):
    name = "SCENARIO"
    rate_per_s = 1e9
    burst = 1e9

    def __init__(self, symbol: str, step_ms: int):
        super().__init__(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
        self.symbol, self.step = symbol, step_ms
        self.rows: list[list[float]] = []
        self.closed = True
        self.down = False
        self.spread_pct = 0.0

    def info(self):
        return ProviderInfo("SCENARIO", "Scenario simulator", ["simulation"], DataStatus.SIMULATED, False, "", "",
                            ["1m"], "", "", "", True)

    def native_timeframes(self):
        return ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]

    def instrument(self, sym):
        return Instrument(f"SCENARIO:{sym}", "SCENARIO", sym, AssetClass.CRYPTO, name=sym, session="24x7")

    def search(self, q, limit=20):
        return [self.instrument(self.symbol)]

    def fetch_candles(self, inst, tf, *, start_ms=None, end_ms=None, limit=500):
        if self.down:
            raise ProviderError("scenario: market data unavailable")
        rows = [list(r) for r in self.rows[-limit:]]
        return Candles.from_rows(rows, instrument_id=inst.id, timeframe=tf, status=DataStatus.SIMULATED,
                                 source="scenario simulator", fetched_at=int(rows[-1][0]) if rows else 0,
                                 last_closed=self.closed)

    def fetch_quote(self, inst):
        if self.down:
            raise ProviderError("scenario: market data unavailable")
        last = self.rows[-1]
        half = last[4] * self.spread_pct / 200
        return Quote(inst.id, last[4], int(last[0]), DataStatus.SIMULATED, "scenario simulator",
                     bid=last[4] - half if half else None, ask=last[4] + half if half else None)


def simulate(bot: dict[str, Any], *, equity: float, limits: dict[str, Any], scenarios: list[str] | None = None,
             bars: int = 160, seed: int = 7) -> dict[str, Any]:
    """Run ``bot`` (a bot row: kind, timeframe, config) through each scenario; returns per-scenario results."""
    out = []
    for kind in scenarios or list(SCENARIOS):
        try:
            out.append(_run_one(bot, kind, equity=equity, limits=limits, bars=bars, seed=seed))
        except Exception as exc:  # noqa: BLE001 - one broken scenario must not hide the others
            out.append({"scenario": kind, "title": SCENARIOS.get(kind, kind), "error": f"{type(exc).__name__}: {exc}"})
    budget = bot["config"].get("basket_risk_pct")
    worst = min((r.get("worst_basket_loss_pct", 0) for r in out if "error" not in r), default=0)
    return {"status": "SIMULATED", "equity": equity, "bars_per_scenario": bars, "results": out,
            "note": "The risk budget limits the loss when the stop fills at its price. Gaps, fees and slippage "
                    "can make a real loss larger: see the gap scenario.",
            "summary": {"scenarios": len(out), "errors": sum(1 for r in out if "error" in r),
                        "worst_basket_loss_pct": worst, "basket_risk_budget_pct": budget,
                        "duplicate_orders": sum(r.get("duplicate_orders", 0) for r in out if "error" not in r)}}


def _run_one(bot: dict[str, Any], kind: str, *, equity: float, limits: dict[str, Any], bars: int,
             seed: int) -> dict[str, Any]:
    from omos.bots.service import BotRunner, BotService
    from omos.events import EventBus
    from omos.execution.gateway import ExecutionGateway
    from omos.execution.paper import PaperBroker, PaperConfig
    from omos.marketdata.service import MarketDataService
    from omos.portfolio.service import PortfolioService, PriceBook
    from omos.risk.engine import RiskEngine
    from omos.storage.db import Database

    tf = parse_tf(bot["timeframe"])
    closes, fx = _path(kind, WARMUP, bars, seed)
    tmp = Path(tempfile.mkdtemp(prefix="omos-sim-"))
    clock = {"now": START}
    try:
        db = Database(tmp / "sim.db")
        prov = ScenarioProvider("SIM", tf.ms)
        market = MarketDataService(db, providers={"SCENARIO": prov})
        prices = PriceBook()
        portfolio = PortfolioService(db, prices)
        risk = RiskEngine(db)

        def quote_fn(iid: str) -> dict:
            q = market.get_quote(iid)
            return {"price": q.price, "status": q.status.value, "bid": q.bid, "ask": q.ask, "session_open": True}

        bus = EventBus()
        gateway = ExecutionGateway(db, risk, portfolio, prices, quote_fn, publish=bus.publish)
        paper = PaperBroker(db, PaperConfig(max_fill_fraction=fx.get("fill_fraction", 1.0)))
        gateway.paper = paper
        acct = portfolio.create_account("Simulation", equity)
        # the user's own limits, except activity limits, which count real seconds and would trip in a fast replay
        risk.set_limits(acct["id"], {**limits, "max_orders_per_minute": 100_000, "max_trades_per_day": 1_000_000},
                        actor="simulation")
        bots = BotService(db, market, gateway, risk, bus)
        runner = BotRunner(bots, tick_s=0.0, clock=lambda: clock["now"])
        b = bots.create(f"sim-{kind}", bot["kind"], "SCENARIO:SIM", bot["timeframe"], account_id=acct["id"],
                        config=bot["config"])

        def bar_row(i: int, o: float, h: float, low: float, c: float) -> list[float]:
            vol = fx.get("volume", 1_000.0)
            return [START + i * tf.ms, o, h, low, c, vol]

        prev = float(closes[0])
        for i in range(WARMUP):
            c = float(closes[i])
            prov.rows.append(bar_row(i, prev, max(prev, c) * 1.002, min(prev, c) * 0.998, c))
            prev = c
        clock["now"] = START + WARMUP * tf.ms
        bots.validate(b["id"], actor="simulation")
        bots.start(b["id"], actor="simulation")
        equity_curve, rejected_from = [], fx.get("reject_from")
        orig_place = paper.place

        def rejecting_place(order, price, **kw):
            raise RuntimeError("scenario: broker rejected the order")

        for i in range(WARMUP, WARMUP + bars):
            c = float(closes[i])
            o = float(closes[i] if "gap_at" in fx and i - WARMUP == fx["gap_at"] else prev)
            hi, lo = max(o, c) * 1.003, min(o, c) * 0.997
            wide = fx.get("wide_spread")
            prov.spread_pct = wide[2] if wide and wide[0] <= i - WARMUP < wide[1] else 0.0
            outage = fx.get("outage")
            prov.down = bool(outage and outage[0] <= i - WARMUP < outage[1])
            paper.place = rejecting_place if rejected_from is not None and i - WARMUP >= rejected_from else orig_place
            if fx.get("restart_at") is not None and i - WARMUP == fx["restart_at"]:
                runner = BotRunner(bots, tick_s=0.0, clock=lambda: clock["now"])  # new process over the same data
                runner.recover_after_restart()
            path = [o, lo, hi, c] if c >= o else [o, hi, lo, c]
            prov.rows.append(bar_row(i, o, o, o, o))
            prov.closed = False
            for k, px in enumerate(path):
                row = prov.rows[-1]
                row[2], row[3], row[4] = max(row[2], px), min(row[3], px), px
                clock["now"] = START + i * tf.ms + int(tf.ms * (k + 1) / (len(path) + 1))
                if not prov.down:
                    gateway.on_price("SCENARIO:SIM", px, "SIMULATED")
                runner.tick()
            prov.closed = True
            clock["now"] = START + (i + 1) * tf.ms + 1
            runner.tick()
            prev = c
            try:
                equity_curve.append(portfolio.record_equity(acct["id"]))
            except Exception:  # noqa: BLE001 - no price during an outage
                equity_curve.append(equity_curve[-1] if equity_curve else equity)
        final = bots.get(b["id"])
        rt = final["runtime"]
        coids = [r["client_order_id"] for r in db.query("SELECT client_order_id FROM orders WHERE account_id = ?",
                                                         (acct["id"],))]
        fills = db.query("SELECT realized_pnl, order_id FROM fills WHERE account_id = ?", (acct["id"],))
        per_basket: dict[str, int] = {}
        for r in db.query("SELECT client_order_id FROM orders WHERE account_id = ? AND filled_qty > 0 "
                          "AND client_order_id LIKE '%:L%'", (acct["id"],)):
            key = r["client_order_id"].rsplit(":L", 1)[0]
            per_basket[key] = per_basket.get(key, 0) + 1
        baskets = _basket_losses(db, acct["id"])
        open_bk = (rt.get("basket") or {}).get("id")
        pos = db.query_one("SELECT qty, avg_price FROM positions WHERE account_id = ? AND instrument_id = ?",
                           (acct["id"], "SCENARIO:SIM"))
        open_pnl = float(pos["qty"]) * (float(closes[WARMUP + bars - 1]) - float(pos["avg_price"])) if pos and pos["qty"] else 0.0
        if open_pnl:  # a basket still open at the end (e.g. the bot was halted after a gap) counts at market value
            baskets = baskets + [baskets_by_id(db, acct["id"]).get(open_bk or "", 0.0) + open_pnl]
        eq = np.array(equity_curve) if equity_curve else np.array([equity])
        peak = np.maximum.accumulate(eq)
        return {
            "scenario": kind, "title": SCENARIOS[kind],
            "return_pct": round((eq[-1] / equity - 1) * 100, 3),
            "max_drawdown_pct": round(float(((eq - peak) / peak).min() * 100), 3),
            "baskets": rt.get("stats", {}).get("baskets", 0),
            "baskets_rejected": rt.get("stats", {}).get("rejected", 0),
            "take_profits": rt.get("stats", {}).get("take_profit", 0),
            "stops": rt.get("stats", {}).get("stop", 0) + rt.get("stats", {}).get("trailing_stop", 0),
            "expired": rt.get("stats", {}).get("expired", 0),
            "max_layers_filled": max(per_basket.values(), default=0),
            "orders": len(coids), "fills": len(fills),
            "duplicate_orders": len(coids) - len(set(coids)),
            "worst_basket_loss_pct": round(min(baskets, default=0.0) / equity * 100, 3),
            "open_position_pnl_pct": round(open_pnl / equity * 100, 3),
            "final_state": final["state"], "errors": final["error_count"],
            "log": [f"{lg['level']}: {lg['message']}" for lg in bots.logs(b["id"], limit=8)],
        }
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def _basket_losses(db, account_id: int) -> list[float]:
    return list(baskets_by_id(db, account_id).values())


def baskets_by_id(db, account_id: int) -> dict[str, float]:
    """Realized P&L per basket (fills grouped by the basket id in each order's client id)."""
    per: dict[str, float] = {}
    for r in db.query("SELECT o.client_order_id AS c, f.realized_pnl AS p FROM fills f JOIN orders o ON o.id = f.order_id "
                      "WHERE f.account_id = ?", (account_id,)):
        key = ":".join(str(r["c"]).split(":")[:2])
        per[key] = per.get(key, 0.0) + float(r["p"] or 0)
    return per


ScenarioFn = Callable[[int, int], tuple[np.ndarray, dict[str, Any]]]
