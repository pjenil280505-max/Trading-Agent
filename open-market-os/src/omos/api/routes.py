"""Business routes. Every route declares the scope it needs; services do the validation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from omos.api.deps import auth, require_live_scope
from omos.backtest.engine import BacktestConfig
from omos.backtest.optimize import monte_carlo, optimize, out_of_sample, walk_forward
from omos.backtest.strategies import TEMPLATES
from omos.errors import NotFound, PermissionDenied, ValidationError
from omos.execution.models import OrderRequest
from omos.indicators import structure as st
from omos.indicators.core import volume_profile
from omos.indicators.registry import catalog, compute, to_json_values
from omos.marketdata.service import candles_to_payload
from omos.marketdata.sessions import SESSIONS
from omos.marketdata.timeframes import STANDARD, heikin_ashi
from omos.mcp import permissions as mcp_perms
from omos.screener.service import FIELDS
from omos.script.lang import function_catalog, run
from omos.security.auth import PAIRING_PRESETS
from omos.storage.backup import backup_database, export_bundle, list_backups
from omos.util import dumps, loads, now_ms

router = APIRouter(prefix="/api")


def H(request: Request):
    return request.app.state.host


READ, PAPER, ADMIN, AI = auth("read"), auth("trade:paper"), auth("admin"), auth("ai")


def _bt_config(d: dict[str, Any] | None) -> dict[str, Any]:
    allowed = set(BacktestConfig.__dataclass_fields__) - {"session"}
    cfg = {k: v for k, v in (d or {}).items() if k in allowed}
    BacktestConfig(**cfg).validate()
    return cfg


# ====================================================================== system
@router.get("/system/status")
def system_status(request: Request, info=Depends(READ)):
    return H(request).status()


@router.get("/system/meta")
def system_meta(info=Depends(READ)):
    return {"timeframes": STANDARD, "sessions": [s.to_dict() for s in SESSIONS.values()], "screener_fields": FIELDS,
            "pairing_presets": PAIRING_PRESETS}


@router.get("/settings")
def get_settings(request: Request, info=Depends(READ)):
    db = H(request).db
    keys = ["ui", "data.poll_seconds", "notify.desktop", "automation.enabled", "ai.tool_levels", "ai.daily_budget_usd",
            "trading.live_enabled", "backup.auto", "backup.keep", "sec.contact", "data.providers"]
    return {k: db.get_setting(k) for k in keys}


SETTABLE = {"ui": dict, "data.poll_seconds": (int, float), "notify.desktop": bool, "automation.enabled": bool,
            "ai.tool_levels": list, "ai.daily_budget_usd": (int, float), "backup.auto": bool, "backup.keep": int,
            "sec.contact": str}


@router.put("/settings")
def put_settings(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    h = H(request)
    for k, v in payload.items():
        if k not in SETTABLE:
            raise ValidationError(f"setting '{k}' cannot be changed here")
        if not isinstance(v, SETTABLE[k]):
            raise ValidationError(f"setting '{k}' has the wrong type")
        if k == "ai.tool_levels" and not set(v) <= {"read", "analyze", "propose"}:
            raise ValidationError("AI tool levels are read, analyze and propose")
        if k == "data.poll_seconds" and not 2 <= float(v) <= 3600:
            raise ValidationError("poll interval must be 2–3600 seconds")
        h.db.set_setting(k, v)
    h.db.audit(info.actor, "settings.update", None, {"keys": list(payload)})
    return get_settings(request, info)


@router.post("/settings/live-trading")
def live_trading(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    """Live trading needs a typed confirmation. No live broker adapter ships verified in this release."""
    h = H(request)
    enable = bool(payload.get("enabled"))
    if enable and payload.get("confirm") != "I UNDERSTAND LIVE TRADING USES REAL MONEY":
        raise ValidationError("type exactly: I UNDERSTAND LIVE TRADING USES REAL MONEY")
    h.db.set_setting("trading.live_enabled", enable)
    h.db.audit(info.actor, "trading.live_enabled", None, {"enabled": enable})
    return {"live_enabled": enable, "verified_live_brokers": list(h.gateway.live_brokers)}


# ====================================================================== auth admin
@router.post("/auth/pairing")
def pairing(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    from urllib.parse import urlencode

    from omos.api.routes_system import lan_info

    out = H(request).auth.create_pairing_code(str(payload.get("preset", "monitor")), str(payload.get("label", ""))[:80],
                                              actor=info.actor)
    lan = lan_info(request)
    out["lan"] = lan
    if lan["active"] and lan["urls"]:
        # what the phone app scans: every address of this computer, the code, and the certificate to pin
        q = [("u", u) for u in lan["urls"]] + [("c", out["code"])] + ([("f", lan["fingerprint"])] if lan["fingerprint"] else [])
        out["link"] = "omos://pair?" + urlencode(q)
    return out


@router.get("/auth/tokens")
def tokens(request: Request, info=Depends(ADMIN)):
    return H(request).auth.list_tokens()


@router.delete("/auth/tokens/{token_id}")
def revoke(token_id: int, request: Request, info=Depends(ADMIN)):
    if token_id == info.id:
        raise ValidationError("you cannot revoke the token you are using; use another device")
    H(request).auth.revoke(token_id, actor=info.actor)
    return {"revoked": token_id}


# ====================================================================== market data
@router.get("/data/providers")
def providers(request: Request, info=Depends(READ)):
    return H(request).market.provider_infos()


@router.put("/data/providers")
def set_providers(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    h = H(request)
    cur = h.db.get_setting("data.providers", {}) or {}
    for name, conf in payload.items():
        if name not in ("OKX", "COINBASE", "KRAKEN", "BINANCE", "YAHOO", "FRED", "ECB", "CSV", "SIM"):
            raise ValidationError(f"unknown provider {name}")
        cur[name] = {"enabled": bool(conf.get("enabled"))}
    h.db.set_setting("data.providers", cur)
    h.market.reload_providers(cur)
    h.db.audit(info.actor, "data.providers", None, cur)
    return h.market.provider_infos()


@router.get("/data/cache")
def cache_stats(request: Request, info=Depends(READ)):
    return H(request).market.store.stats()


@router.post("/data/import_csv")
def import_csv(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    text = str(payload.get("csv", ""))
    if len(text) > 50_000_000:
        raise ValidationError("CSV is larger than 50 MB")
    return H(request).market.import_csv(str(payload.get("name", "")), text, str(payload.get("timeframe", "1d")))


@router.get("/instruments/search")
def search(request: Request, q: str = Query(..., max_length=40), providers: str = "", info=Depends(READ)):
    return H(request).market.search(q, [p for p in providers.split(",") if p] or None)


@router.get("/instruments/{instrument_id}")
def instrument(instrument_id: str, request: Request, info=Depends(READ)):
    return H(request).market.instrument(instrument_id).to_dict()


@router.get("/candles")
def candles(request: Request, instrument: str, tf: str = "1d", limit: int = Query(500, ge=1, le=50000),
            start: int | None = None, end: int | None = None, ha: bool = False, info=Depends(READ)):
    h = H(request)
    c = h.market.get_candles(instrument, tf, limit=limit, start_ms=start, end_ms=end)
    if ha:
        c = heikin_ashi(c)
    inst = h.market.instrument(instrument)
    return {**candles_to_payload(c), "instrument": inst.to_dict()}


@router.get("/quote")
def quote(request: Request, instrument: str, info=Depends(READ)):
    return H(request).market.get_quote(instrument).to_dict()


@router.get("/indicators")
def indicators(info=Depends(READ)):
    return catalog()


@router.post("/indicators/compute")
def indicator_compute(request: Request, payload: dict[str, Any] = Body(...), info=Depends(READ)):
    h = H(request)
    limit = int(payload.get("limit", 1000))
    c = h.market.get_candles(str(payload["instrument"]), str(payload.get("tf", "1d")), limit=min(limit, 50_000), max_age_s=30)
    ind_id = str(payload["id"])
    if ind_id.startswith("plugin:"):
        src = h.plugins.read_entry(ind_id.split(":", 1)[1])
        res = run(src, c, payload.get("params") or None)
        outputs = res.plots
        warmup = 0
    else:
        r = compute(ind_id, c, payload.get("params") or {})
        outputs, warmup = r["outputs"], r["warmup"]
    tail = int(payload.get("tail", 0) or 0)
    ts = c.ts.tolist()
    data = {k: to_json_values(v) for k, v in outputs.items()}
    if tail:
        ts = ts[-tail:]
        data = {k: v[-tail:] for k, v in data.items()}
    return {"id": ind_id, "ts": ts, "outputs": data, "warmup": warmup, "status": c.status.value, "source": c.source}


@router.post("/indicators/volume_profile")
def vprofile(request: Request, payload: dict[str, Any] = Body(...), info=Depends(READ)):
    c = H(request).market.get_candles(str(payload["instrument"]), str(payload.get("tf", "1d")),
                                     limit=min(int(payload.get("limit", 500)), 20_000), max_age_s=30)
    return volume_profile(c.high, c.low, c.close, c.volume, bins=max(5, min(int(payload.get("bins", 24)), 200)))


@router.get("/structure")
def structure(request: Request, instrument: str, tf: str = "1d", left: int = 3, right: int = 3, info=Depends(READ)):
    c = H(request).market.get_candles(instrument, tf, limit=1000, max_age_s=30)
    ms = st.market_structure(c.open, c.high, c.low, c.close, left, right)
    fvg = st.fair_value_gaps(c.high, c.low)
    ts = c.ts.tolist()

    def stamp(items):
        return [{**i, "ts": ts[i["bar"]]} for i in items]

    return {"events": stamp(ms["events"])[-50:], "order_blocks": stamp(ms["order_blocks"])[-20:],
            "fvg": stamp(fvg["zones"])[-50:], "support_resistance": st.support_resistance(c.high, c.low, c.close),
            "liquidity": st.liquidity_zones(c.high, c.low), "status": c.status.value,
            "methodology": st.__doc__.split("Definitions")[1].strip() if st.__doc__ else ""}


@router.get("/analysis")
def analysis(request: Request, instrument: str, tf: str = "1d", info=Depends(READ)):
    return H(request).evidence.build(instrument, tf, include_fundamentals=False)


# ====================================================================== watchlists, charts
@router.get("/watchlists")
def watchlists(request: Request, info=Depends(READ)):
    return H(request).watchlists.list()


@router.post("/watchlists")
def create_watchlist(request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).watchlists.create(str(payload.get("name", "")))


@router.post("/watchlists/{wl_id}/items")
def wl_add(wl_id: int, request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).watchlists.add(wl_id, str(payload.get("instrument_id", "")))


@router.delete("/watchlists/{wl_id}/items")
def wl_remove(wl_id: int, request: Request, instrument_id: str, info=Depends(PAPER)):
    return H(request).watchlists.remove(wl_id, instrument_id)


@router.put("/watchlists/{wl_id}")
def wl_update(wl_id: int, request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    w = H(request).watchlists
    if "name" in payload:
        w.rename(wl_id, str(payload["name"]))
    if "items" in payload:
        w.reorder(wl_id, [str(x) for x in payload["items"]])
    return w.get(wl_id)


@router.delete("/watchlists/{wl_id}")
def wl_delete(wl_id: int, request: Request, info=Depends(PAPER)):
    H(request).watchlists.delete(wl_id)
    return {"deleted": wl_id}


@router.get("/watchlists/{wl_id}/quotes")
def wl_quotes(wl_id: int, request: Request, tf: str = "1d", info=Depends(READ)):
    h = H(request)

    def one(iid: str) -> dict[str, Any]:
        try:
            c = h.market.get_candles(iid, tf, limit=30, max_age_s=30)
            last, prev = float(c.close[-1]), float(c.close[-2]) if len(c) > 1 else float(c.close[-1])
            return {"instrument_id": iid, "price": last, "change_pct": (last / prev - 1) * 100 if prev else None,
                    "spark": [float(x) for x in c.close[-30:]], "status": c.status.value,
                    "stale": bool(c.quality.get("stale"))}
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not break the list
            return {"instrument_id": iid, "error": getattr(exc, "message", str(exc))[:200]}

    items = h.watchlists.get(wl_id)["items"]
    # cold fetches go to the venue; run a few at once (provider rate limiters are thread-safe), keep list order
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(one, items))


@router.get("/workspaces")
def workspaces(request: Request, info=Depends(READ)):
    return H(request).charts.workspaces()


@router.get("/workspaces/{name}")
def workspace(name: str, request: Request, info=Depends(READ)):
    return H(request).charts.get_workspace(name)


@router.put("/workspaces/{name}")
def save_workspace(name: str, request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).charts.save_workspace(name, payload.get("layout") or {})


@router.delete("/workspaces/{name}")
def delete_workspace(name: str, request: Request, info=Depends(PAPER)):
    H(request).charts.delete_workspace(name)
    return {"deleted": name}


@router.get("/drawings")
def drawings(request: Request, instrument: str, info=Depends(READ)):
    return H(request).charts.drawings(instrument)


@router.put("/drawings")
def save_drawing(request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).charts.save_drawing(payload)


@router.delete("/drawings/{did}")
def delete_drawing(did: str, request: Request, info=Depends(PAPER)):
    H(request).charts.delete_drawing(did)
    return {"deleted": did}


@router.get("/chart_templates")
def templates(request: Request, info=Depends(READ)):
    return H(request).charts.templates()


@router.put("/chart_templates/{name}")
def save_template(name: str, request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).charts.save_template(name, payload.get("config") or {})


# ====================================================================== strategies, backtests
@router.get("/script/functions")
def script_functions(info=Depends(READ)):
    return function_catalog()


@router.get("/strategies/templates")
def strategy_templates(request: Request, info=Depends(READ)):
    plugins = [{"key": f"plugin:{p['id']}", "name": p["name"], "source": H(request).plugins.read_entry(p["id"])}
               for p in H(request).plugins.enabled("strategy")]
    return [{"key": k, **v} for k, v in TEMPLATES.items()] + plugins


@router.post("/strategies/validate")
def strategy_validate(request: Request, payload: dict[str, Any] = Body(...), info=Depends(READ)):
    return H(request).strategies.check(str(payload.get("source", "")))


@router.get("/strategies")
def strategies(request: Request, info=Depends(READ)):
    return H(request).strategies.list()


@router.post("/strategies")
def create_strategy(request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).strategies.create(str(payload.get("name", "")), str(payload.get("source", "")), str(payload.get("note", "")))


@router.get("/strategies/{sid}")
def get_strategy(sid: int, request: Request, version: int | None = None, info=Depends(READ)):
    s = H(request).strategies
    return {**s.get(sid, version), "versions": s.versions(sid)}


@router.put("/strategies/{sid}")
def update_strategy(sid: int, request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).strategies.update(sid, str(payload.get("source", "")), str(payload.get("note", "")))


@router.delete("/strategies/{sid}")
def delete_strategy(sid: int, request: Request, info=Depends(PAPER)):
    H(request).strategies.delete(sid)
    return {"deleted": sid}


@router.get("/strategies/{sid}/export")
def export_strategy(sid: int, request: Request, info=Depends(READ)):
    return H(request).strategies.export(sid)


@router.post("/strategies/import")
def import_strategy(request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).strategies.import_(payload)


def _source(h, payload: dict[str, Any]) -> str:
    if payload.get("source"):
        return str(payload["source"])
    if payload.get("strategy_id"):
        return h.strategies.get(int(payload["strategy_id"]), payload.get("version"))["source"]
    raise ValidationError("give either source or strategy_id")


@router.post("/backtest")
def backtest(request: Request, payload: dict[str, Any] = Body(...), info=Depends(READ)):
    h = H(request)
    src = _source(h, payload)
    res = h.run_backtest(src, str(payload["instrument"]), str(payload.get("tf", "1d")),
                         limit=min(int(payload.get("limit", 2000)), 50_000), config=_bt_config(payload.get("config")),
                         params=payload.get("params"))
    if payload.get("save", True):
        h.db.execute("INSERT INTO backtests(strategy_id, strategy_version, instrument_id, timeframe, config, metrics, trades, "
                     "equity, data_status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (payload.get("strategy_id"), payload.get("version"), payload["instrument"], payload.get("tf", "1d"),
                      dumps(res["config"]), dumps(res["metrics"]), dumps(res["trades"][-500:]), dumps(res["equity"]),
                      res["data"]["status"], now_ms()))
    return res


@router.get("/backtests")
def backtests(request: Request, limit: int = 50, info=Depends(READ)):
    rows = H(request).db.query("SELECT id, strategy_id, strategy_version, instrument_id, timeframe, metrics, data_status, "
                               "created_at FROM backtests ORDER BY id DESC LIMIT ?", (min(limit, 500),))
    for r in rows:
        r["metrics"] = loads(r["metrics"], {})
    return rows


def _opt_inputs(h, payload):
    src = _source(h, payload)
    c = h.market.get_candles(str(payload["instrument"]), str(payload.get("tf", "1d")),
                             limit=min(int(payload.get("limit", 2000)), 20_000), max_age_s=120)
    inst = h.market.instrument(str(payload["instrument"]))
    cfg = BacktestConfig(**{**_bt_config(payload.get("config")), "session": inst.session})
    return src, c, cfg


@router.post("/backtest/optimize")
def bt_optimize(request: Request, payload: dict[str, Any] = Body(...), info=Depends(READ)):
    src, c, cfg = _opt_inputs(H(request), payload)
    return optimize(src, c, cfg, ranges=payload.get("ranges"), objective=str(payload.get("objective", "sharpe")),
                    min_trades=int(payload.get("min_trades", 5)))


@router.post("/backtest/walk_forward")
def bt_wf(request: Request, payload: dict[str, Any] = Body(...), info=Depends(READ)):
    src, c, cfg = _opt_inputs(H(request), payload)
    return walk_forward(src, c, cfg, folds=int(payload.get("folds", 4)), ranges=payload.get("ranges"),
                        objective=str(payload.get("objective", "sharpe")))


@router.post("/backtest/out_of_sample")
def bt_oos(request: Request, payload: dict[str, Any] = Body(...), info=Depends(READ)):
    src, c, cfg = _opt_inputs(H(request), payload)
    return out_of_sample(src, c, cfg, train_pct=float(payload.get("train_pct", 70)), ranges=payload.get("ranges"),
                         objective=str(payload.get("objective", "sharpe")))


@router.post("/backtest/monte_carlo")
def bt_mc(request: Request, payload: dict[str, Any] = Body(...), info=Depends(READ)):
    h = H(request)
    res = h.run_backtest(_source(h, payload), str(payload["instrument"]), str(payload.get("tf", "1d")),
                         limit=min(int(payload.get("limit", 2000)), 50_000), config=_bt_config(payload.get("config")),
                         params=payload.get("params"))
    return monte_carlo(res["trades"], res["metrics"]["initial_cash"], runs=int(payload.get("runs", 1000)))


# ====================================================================== screener
@router.post("/screener/run")
def screener_run(request: Request, payload: dict[str, Any] = Body(...), info=Depends(READ)):
    return H(request).screener.run(payload.get("definition") or {})


@router.post("/screener/export")
def screener_export(request: Request, payload: dict[str, Any] = Body(...), info=Depends(READ)):
    h = H(request)
    return PlainTextResponse(h.screener.to_csv(h.screener.run(payload.get("definition") or {})), media_type="text/csv")


@router.get("/screeners")
def screeners(request: Request, info=Depends(READ)):
    return H(request).screener.list()


@router.post("/screeners")
def save_screener(request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).screener.save(str(payload.get("name", "")), payload.get("definition") or {}, payload.get("schedule_minutes"))


@router.post("/screeners/{sid}/run")
def run_screener(sid: int, request: Request, info=Depends(READ)):
    return H(request).screener.run_saved(sid)


@router.post("/screeners/{sid}/duplicate")
def dup_screener(sid: int, request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).screener.duplicate(sid, str(payload.get("name", "")))


@router.delete("/screeners/{sid}")
def del_screener(sid: int, request: Request, info=Depends(PAPER)):
    H(request).screener.delete(sid)
    return {"deleted": sid}


# ====================================================================== accounts & orders
@router.get("/accounts")
def accounts(request: Request, info=Depends(READ)):
    return H(request).portfolio.accounts()


@router.post("/accounts")
def create_account(request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).portfolio.create_account(str(payload.get("name", "")), float(payload.get("initial_cash", 10_000)))


@router.get("/accounts/{aid}/summary")
def account_summary(aid: int, request: Request, info=Depends(READ)):
    return H(request).portfolio.summary(aid)


@router.post("/accounts/{aid}/reset")
def account_reset(aid: int, request: Request, info=Depends(PAPER)):
    return H(request).portfolio.reset_account(aid, actor=info.actor)


@router.get("/accounts/{aid}/orders")
def account_orders(aid: int, request: Request, open_only: bool = False, info=Depends(READ)):
    return H(request).portfolio.orders(aid, open_only=open_only)


@router.get("/accounts/{aid}/fills")
def account_fills(aid: int, request: Request, info=Depends(READ)):
    return H(request).portfolio.fills(aid)


@router.get("/accounts/{aid}/equity")
def account_equity(aid: int, request: Request, info=Depends(READ)):
    return H(request).portfolio.equity_curve(aid)


@router.get("/accounts/{aid}/analytics")
def account_analytics(aid: int, request: Request, info=Depends(READ)):
    h = H(request)
    closes = {}
    for p in h.portfolio.positions(aid)[:10]:
        try:
            closes[p["instrument_id"]] = h.market.get_candles(p["instrument_id"], "1d", limit=120, max_age_s=3600).close
        except Exception:  # noqa: BLE001 - analytics are best-effort
            continue
    if closes:
        m = min(len(v) for v in closes.values())
        closes = {k: np.asarray(v[-m:]) for k, v in closes.items() if m > 10}
    return h.portfolio.analytics(aid, closes or None)


@router.get("/accounts/{aid}/export.csv")
def account_export(aid: int, request: Request, info=Depends(READ)):
    return PlainTextResponse(H(request).portfolio.export_fills_csv(aid), media_type="text/csv")


@router.post("/orders")
def submit_order(request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    h = H(request)
    req = OrderRequest.from_dict({**payload, "origin": "ui" if info.kind in ("browser", "device", "cli") else "api"})
    if h.portfolio.account(req.account_id)["kind"] == "live":
        require_live_scope(info)
    return h.gateway.submit(req, actor=info.actor)


@router.delete("/orders/{oid}")
def cancel_order(oid: int, request: Request, info=Depends(PAPER)):
    return H(request).gateway.cancel(oid, actor=info.actor)


@router.post("/orders/cancel_all")
def cancel_all(request: Request, payload: dict[str, Any] = Body(default={}), info=Depends(PAPER)):
    return {"cancelled": H(request).gateway.cancel_all(actor=info.actor, account_id=payload.get("account_id"))}


@router.post("/accounts/{aid}/flatten")
def flatten(aid: int, request: Request, info=Depends(PAPER)):
    return H(request).gateway.flatten(aid, actor=info.actor)


@router.get("/proposals")
def proposals(request: Request, status: str = "pending", info=Depends(READ)):
    rows = H(request).db.query("SELECT * FROM order_proposals WHERE status = ? ORDER BY id DESC LIMIT 200", (status,))
    for r in rows:
        r["payload"] = loads(r["payload"], {})
    return rows


@router.post("/proposals/{pid}/decide")
def decide(pid: int, request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    if info.kind == "mcp":
        raise PermissionDenied("proposals can only be decided by a person")
    return H(request).gateway.decide_proposal(pid, bool(payload.get("approve")), actor=info.actor)


@router.get("/journal")
def journal(request: Request, tag: str | None = None, info=Depends(READ)):
    return H(request).portfolio.journal(tag=tag)


@router.post("/journal")
def add_journal(request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).portfolio.add_journal(str(payload.get("title", "")), str(payload.get("body", "")),
                                            account_id=payload.get("account_id"), instrument_id=payload.get("instrument_id"),
                                            tags=payload.get("tags") or [])


@router.delete("/journal/{jid}")
def del_journal(jid: int, request: Request, info=Depends(PAPER)):
    H(request).portfolio.delete_journal(jid)
    return {"deleted": jid}


# ====================================================================== risk
@router.get("/risk/limits")
def risk_limits(request: Request, account_id: int, info=Depends(READ)):
    return H(request).risk.limits(account_id).to_dict()


@router.put("/risk/limits")
def set_risk_limits(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    return H(request).risk.set_limits(payload.get("account_id"), payload.get("limits") or {}, actor=info.actor).to_dict()


@router.get("/risk/state")
def risk_state(request: Request, account_id: int, info=Depends(READ)):
    h = H(request)
    return {"state": h.risk.state(account_id), "global": h.risk.global_halt(),
            "monitor": h.risk.monitor(account_id, h.portfolio.summary(account_id)["equity"])}


@router.get("/risk/events")
def risk_events(request: Request, account_id: int | None = None, info=Depends(READ)):
    return H(request).risk.events(account_id)


@router.post("/risk/resume")
def risk_resume(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    H(request).risk.resume(int(payload["account_id"]), actor=info.actor)
    return {"resumed": payload["account_id"]}


@router.post("/risk/emergency_stop")
def emergency(request: Request, payload: dict[str, Any] = Body(default={}), info=Depends(PAPER)):
    return H(request).bots.emergency_stop(actor=info.actor, flatten=bool(payload.get("flatten")))


@router.post("/risk/clear_emergency")
def clear_emergency(request: Request, info=Depends(ADMIN)):
    H(request).risk.set_global_halt(False, "cleared", actor=info.actor)
    return {"cleared": True}


# ====================================================================== bots
@router.get("/bots")
def bots(request: Request, archived: bool = False, info=Depends(READ)):
    return H(request).bots.list(include_archived=archived)


@router.post("/bots")
def create_bot(request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    h = H(request)
    cfg = dict(payload.get("config") or {})
    if payload.get("strategy_id") and not cfg.get("script"):
        cfg["script"] = h.strategies.get(int(payload["strategy_id"]))["source"]
    return h.bots.create(str(payload.get("name", "")), str(payload.get("kind", "rule")), str(payload.get("instrument_id", "")),
                         str(payload.get("timeframe", "1h")), account_id=int(payload["account_id"]), config=cfg,
                         strategy_id=payload.get("strategy_id"), actor=info.actor)


@router.get("/bots/{bid}")
def bot(bid: int, request: Request, info=Depends(READ)):
    b = H(request).bots
    return {**b.get(bid), "performance": b.performance(bid), "history": b.history(bid)}


@router.put("/bots/{bid}")
def configure_bot(bid: int, request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).bots.configure(bid, payload.get("config") or {}, actor=info.actor)


@router.post("/bots/{bid}/simulate")
def bot_simulate(bid: int, request: Request, payload: dict[str, Any] = Body(default={}), info=Depends(PAPER)):
    """Stress-test the bot on synthetic scenarios in an isolated simulation (a background job)."""
    from omos.bots.simulate import SCENARIOS, simulate

    h = H(request)
    b = h.bots.get(bid)
    scenarios = payload.get("scenarios") or list(SCENARIOS)
    bad = [s for s in scenarios if s not in SCENARIOS]
    if bad:
        raise ValidationError(f"unknown scenario(s): {', '.join(bad)}")
    bars = int(payload.get("bars", 160))
    if not 60 <= bars <= 600:
        raise ValidationError("bars must be between 60 and 600")
    equity = h.portfolio.snapshot(b["account_id"]).equity
    limits = h.risk.limits(b["account_id"]).to_dict()

    def run(job):
        results = []
        for i, sc in enumerate(scenarios):
            job.check_cancel()
            job.update(100 * i / len(scenarios), f"Scenario {i + 1} of {len(scenarios)}: {SCENARIOS[sc]}")
            results.append(simulate(b, equity=equity, limits=limits, scenarios=[sc], bars=bars)["results"][0])
        worst = min((r.get("worst_basket_loss_pct", 0) for r in results if "error" not in r), default=0)
        summary = {"scenarios": len(results), "errors": sum(1 for r in results if "error" in r),
                   "worst_basket_loss_pct": worst, "basket_risk_budget_pct": b["config"].get("basket_risk_pct"),
                   "duplicate_orders": sum(r.get("duplicate_orders", 0) for r in results if "error" not in r)}
        h.bots._set_flag(bid, "simulation", {"at": now_ms(), "summary": summary, "bars": bars})
        h.bots.log(bid, "info", f"simulated on {len(results)} scenario(s); worst basket {worst}% of equity",
                   {"summary": summary})
        return {"status": "SIMULATED", "equity": equity, "bars_per_scenario": bars, "results": results,
                "summary": summary,
                "note": "Simulated prices, paper fills. The risk budget limits the loss when the stop fills at its "
                        "price; gaps, fees and slippage can make a real loss larger (see the gap scenario)."}

    return h.jobs.start(f"simulate:{bid}", f"Simulate {b['name']}", run).to_dict()


@router.post("/bots/{bid}/{action}")
def bot_action(bid: int, action: str, request: Request, require_paper: int = 0,
               payload: dict[str, Any] = Body(default={}), info=Depends(PAPER)):
    b = H(request).bots
    if require_paper or info.kind == "mcp":
        if b.get(bid)["mode"] != "paper":
            raise PermissionDenied("only PAPER bots can be controlled here")
    actions = {
        "validate": lambda: b.validate(bid, actor=info.actor),
        "backtest": lambda: b.backtest(bid, actor=info.actor),
        "start": lambda: b.start(bid, actor=info.actor),
        "pause": lambda: b.pause(bid, actor=info.actor),
        "resume": lambda: b.resume(bid, actor=info.actor),
        "stop": lambda: b.stop(bid, actor=info.actor),
        "clone": lambda: b.clone(bid, str(payload.get("name", f"bot {bid} copy")), actor=info.actor),
        "mark_paper_tested": lambda: b.mark_paper_tested(bid, actor=info.actor),
        "approve": lambda: b.approve_live(bid, actor=info.actor, confirm=str(payload.get("confirm", ""))),
        "archive": lambda: b.delete(bid, actor=info.actor),
    }
    if action not in actions:
        raise NotFound(f"unknown bot action '{action}'")
    return actions[action]()


@router.get("/bots/{bid}/logs")
def bot_logs(bid: int, request: Request, limit: int = 200, info=Depends(READ)):
    return H(request).bots.logs(bid, min(limit, 1000))


# ====================================================================== alerts, notifications
@router.get("/alerts")
def alerts(request: Request, info=Depends(READ)):
    return H(request).alerts.list()


@router.post("/alerts")
def create_alert(request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).alerts.create(name=str(payload.get("name", "")), instrument_id=str(payload.get("instrument_id", "")),
                                    kind=str(payload.get("kind", "")), params=payload.get("params") or {},
                                    timeframe=str(payload.get("timeframe", "1m")), frequency=str(payload.get("frequency", "once")),
                                    expires_at=payload.get("expires_at"), notify=payload.get("notify"))


@router.patch("/alerts/{aid}")
def alert_state(aid: int, request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).alerts.update_state(aid, str(payload.get("state", "")), snooze_minutes=payload.get("snooze_minutes"))


@router.delete("/alerts/{aid}")
def del_alert(aid: int, request: Request, info=Depends(PAPER)):
    H(request).alerts.delete(aid)
    return {"deleted": aid}


@router.post("/alerts/{aid}/test")
def test_alert(aid: int, request: Request, info=Depends(PAPER)):
    return H(request).alerts.test_fire(aid)


@router.get("/alerts/history")
def alert_history(request: Request, alert_id: int | None = None, info=Depends(READ)):
    return H(request).alerts.history(alert_id)


@router.post("/alert_events/{eid}/ack")
def ack(eid: int, request: Request, info=Depends(PAPER)):
    H(request).alerts.acknowledge(eid)
    return {"acknowledged": eid}


@router.get("/notifications")
def notifications(request: Request, unread: bool = False, info=Depends(READ)):
    return H(request).notifier.list(unread_only=unread)


@router.post("/notifications/read")
def read_notifications(request: Request, payload: dict[str, Any] = Body(default={}), info=Depends(READ)):
    H(request).notifier.mark_read(payload.get("ids"))
    return {"ok": True}


# ====================================================================== research
@router.get("/news")
def news(request: Request, q: str = "", symbol: str | None = None, topic: str | None = None, info=Depends(READ)):
    return H(request).news.search(q=q, symbol=symbol, topic=topic)


@router.post("/news/refresh")
def news_refresh(request: Request, payload: dict[str, Any] = Body(default={}), info=Depends(READ)):
    return H(request).news.refresh([str(s) for s in (payload.get("symbols") or [])][:30])


@router.get("/news/feeds")
def news_feeds(request: Request, info=Depends(READ)):
    return H(request).news.feeds()


@router.put("/news/feeds")
def set_news_feeds(request: Request, payload: list[dict[str, Any]] = Body(...), info=Depends(ADMIN)):
    return H(request).news.set_feeds(payload)


@router.get("/fundamentals/{ticker}")
def fundamentals(ticker: str, request: Request, info=Depends(READ)):
    return H(request).fundamentals.company(ticker)


@router.get("/calendar")
def calendar(request: Request, info=Depends(READ)):
    return H(request).calendar.events()


# ====================================================================== AI
@router.get("/ai/presets")
def ai_presets(request: Request, info=Depends(READ)):
    return H(request).ai.presets()


@router.get("/ai/providers")
def ai_providers(request: Request, info=Depends(READ)):
    return H(request).ai.list()


@router.post("/ai/providers")
def add_ai_provider(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    return H(request).ai.add(str(payload.get("name", "")), str(payload.get("preset", "")), base_url=payload.get("base_url"),
                             model=payload.get("model"), api_key=payload.get("api_key"),
                             price_in=float(payload.get("price_in", 0)), price_out=float(payload.get("price_out", 0)))


@router.patch("/ai/providers/{pid}")
def ai_provider_enable(pid: int, request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    return H(request).ai.set_enabled(pid, bool(payload.get("enabled")), acknowledge_cost=bool(payload.get("acknowledge_cost")))


@router.delete("/ai/providers/{pid}")
def ai_provider_delete(pid: int, request: Request, info=Depends(ADMIN)):
    H(request).ai.delete(pid)
    return {"deleted": pid}


@router.post("/ai/analyze")
def ai_analyze(request: Request, payload: dict[str, Any] = Body(...), info=Depends(AI)):
    return H(request).agents.analyze(int(payload["provider_id"]), str(payload["instrument"]), timeframe=str(payload.get("tf", "1d")),
                                     role=str(payload.get("role", "market_analyst")), task=str(payload.get("task", "Analyse this instrument.")))


@router.post("/ai/debate")
def ai_debate(request: Request, payload: dict[str, Any] = Body(...), info=Depends(AI)):
    return H(request).agents.debate(int(payload["provider_id"]), str(payload["instrument"]), timeframe=str(payload.get("tf", "1d")))


@router.post("/ai/chat")
def ai_chat(request: Request, payload: dict[str, Any] = Body(...), info=Depends(AI)):
    return H(request).agents.chat_with_tools(int(payload["provider_id"]), str(payload.get("question", ""))[:4000])


@router.get("/ai/runs")
def ai_runs(request: Request, info=Depends(READ)):
    h = H(request)
    rows = h.db.query("SELECT id, ts, provider_id, workflow, role, tokens_in, tokens_out, cost, status FROM ai_runs "
                      "ORDER BY id DESC LIMIT 200")
    return {"runs": rows, "spend_today_usd": h.ai.spend_today(), "daily_budget_usd": h.db.get_setting("ai.daily_budget_usd", 1.0)}


# ====================================================================== MCP
@router.get("/mcp/self")
def mcp_self(request: Request, info=Depends(auth("mcp"))):
    return {"enabled": mcp_perms.permissions(H(request).db)}


@router.get("/mcp/permissions")
def mcp_permissions(request: Request, info=Depends(READ)):
    return mcp_perms.matrix_table(H(request).db)


@router.put("/mcp/permissions")
def set_mcp_permissions(request: Request, payload: dict[str, bool] = Body(...), info=Depends(ADMIN)):
    try:
        mcp_perms.set_permissions(H(request).db, payload, actor=info.actor)
    except KeyError as exc:
        raise ValidationError(f"unknown MCP tool {exc}") from exc
    return mcp_perms.matrix_table(H(request).db)


@router.post("/mcp/token")
def mcp_token(request: Request, info=Depends(ADMIN)):
    h = H(request)
    token, row = h.auth.create_token("MCP bridge", "mcp", ["mcp"], ttl_days=365, actor=info.actor)
    path: Path = h.cfg.data_path / "mcp_token"
    path.write_text(token)
    path.chmod(0o600)
    return {"token_info": row, "saved_to": str(path),
            "client_config": {"mcpServers": {"open-market-os": {"command": "omos", "args": ["mcp"]}}}}


@router.get("/mcp/servers")
def mcp_servers(request: Request, info=Depends(READ)):
    return H(request).mcp_client.list()


@router.post("/mcp/servers")
def add_mcp_server(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    return H(request).mcp_client.add(str(payload.get("name", "")), transport=str(payload.get("transport", "")),
                                     command=str(payload.get("command", "")), args=payload.get("args") or [],
                                     url=str(payload.get("url", "")), timeout_s=float(payload.get("timeout_s", 30)),
                                     actor=info.actor)


@router.post("/mcp/servers/{sid}/refresh")
def refresh_mcp(sid: int, request: Request, info=Depends(ADMIN)):
    return H(request).mcp_client.refresh(sid)


@router.put("/mcp/servers/{sid}/tools/{tool}")
def mcp_tool_perm(sid: int, tool: str, request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    return H(request).mcp_client.set_tool(sid, tool, bool(payload.get("enabled")), actor=info.actor)


@router.post("/mcp/servers/{sid}/call")
def call_mcp(sid: int, request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).mcp_client.call(sid, str(payload.get("tool", "")), payload.get("args") or {}, actor=info.actor)


@router.delete("/mcp/servers/{sid}")
def del_mcp(sid: int, request: Request, info=Depends(ADMIN)):
    H(request).mcp_client.delete(sid, actor=info.actor)
    return {"deleted": sid}


# ====================================================================== plugins
@router.get("/plugins")
def plugins(request: Request, info=Depends(READ)):
    return H(request).plugins.list()


@router.post("/plugins/install")
def install_plugin(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    return H(request).plugins.install(Path(str(payload.get("path", ""))), actor=info.actor)


@router.post("/plugins/{pid}/enable")
def enable_plugin(pid: str, request: Request, payload: dict[str, Any] = Body(default={}), info=Depends(ADMIN)):
    h = H(request)
    out = h.plugins.enable(pid, grant=[str(p) for p in payload.get("grant", [])], actor=info.actor)
    if out["kind"] == "data_provider_rest":
        out["loaded_providers"] = h.load_plugin_providers()
    return out


@router.post("/plugins/{pid}/disable")
def disable_plugin(pid: str, request: Request, info=Depends(ADMIN)):
    h = H(request)
    out = h.plugins.disable(pid, actor=info.actor)
    h.load_plugin_providers()
    return out


@router.delete("/plugins/{pid}")
def uninstall_plugin(pid: str, request: Request, info=Depends(ADMIN)):
    H(request).plugins.uninstall(pid, actor=info.actor)
    return {"uninstalled": pid}


# ====================================================================== workflows
@router.get("/workflows")
def workflows(request: Request, info=Depends(READ)):
    return H(request).workflows.list()


@router.post("/workflows")
def save_workflow(request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).workflows.save(str(payload.get("name", "")), payload.get("definition") or {}, actor=info.actor)


@router.post("/workflows/{wid}/run")
def run_workflow(wid: int, request: Request, payload: dict[str, Any] = Body(default={}), info=Depends(PAPER)):
    return H(request).workflows.run(wid, dry_run=bool(payload.get("dry_run", True)), actor=info.actor,
                                    trigger_data={"type": "manual", "by": info.actor})


@router.patch("/workflows/{wid}")
def toggle_workflow(wid: int, request: Request, payload: dict[str, Any] = Body(...), info=Depends(PAPER)):
    return H(request).workflows.set_enabled(wid, bool(payload.get("enabled")), actor=info.actor)


@router.get("/workflows/{wid}/runs")
def workflow_runs(wid: int, request: Request, info=Depends(READ)):
    return H(request).workflows.runs(wid)


@router.post("/workflows/disable_all")
def disable_workflows(request: Request, info=Depends(PAPER)):
    H(request).workflows.disable_all(actor=info.actor)
    return {"automation_enabled": False}


# ====================================================================== secrets, backups, audit
@router.get("/secrets")
def secrets(request: Request, info=Depends(ADMIN)):
    s = H(request).secrets
    return {"status": s.status(), "names": s.names()}


@router.put("/secrets/{name}")
def put_secret(name: str, request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    H(request).secrets.put(name, str(payload.get("value", "")))
    H(request).db.audit(info.actor, "secret.put", name)
    return {"stored": name}


@router.delete("/secrets/{name}")
def del_secret(name: str, request: Request, info=Depends(ADMIN)):
    H(request).secrets.delete(name)
    H(request).db.audit(info.actor, "secret.delete", name)
    return {"deleted": name}


@router.post("/secrets/unlock")
def unlock(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    H(request).secrets.unlock(str(payload.get("passphrase", "")))
    return H(request).secrets.status()


@router.get("/backups")
def backups(request: Request, info=Depends(ADMIN)):
    return list_backups(H(request).cfg.backups_path)


@router.post("/backups")
def make_backup(request: Request, info=Depends(ADMIN)):
    h = H(request)
    p = backup_database(h.cfg.db_path, h.cfg.backups_path, label="manual")
    h.db.audit(info.actor, "backup.create", p.name)
    return {"file": p.name, "path": str(p)}


@router.post("/export")
def export(request: Request, info=Depends(ADMIN)):
    h = H(request)
    p = export_bundle(h.cfg.db_path, h.cfg.exports_path, [h.cfg.data_path / "host.toml"])
    h.db.audit(info.actor, "data.export", p.name)
    return {"file": p.name, "path": str(p)}


@router.get("/audit")
def audit(request: Request, limit: int = 200, info=Depends(ADMIN)):
    rows = H(request).db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (min(limit, 2000),))
    for r in rows:
        r["detail"] = loads(r["detail"])
    return rows


@router.get("/device")
def device(request: Request, info=Depends(READ)):
    return H(request).device
