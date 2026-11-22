"""End-to-end tests of the HTTP API against a real Host (background loops off, controlled data provider)."""

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from helpers import ControlledProvider
from omos.api.app import create_app
from omos.config import HostConfig
from omos.host import Host


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OMOS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OMOS_VAULT_PASSPHRASE", "test vault passphrase")
    prov = ControlledProvider()
    prov.set_closes("ABC", 100 + np.cumsum(np.sin(np.arange(400) / 7.0)))
    prov.set_closes("XYZ", np.linspace(50, 80, 400))
    host = Host(HostConfig(data_dir=str(tmp_path / "home")), start_loops=False, providers={"TEST": prov})
    app = create_app(host, testing=True)
    client = TestClient(app)
    code = host.auth.create_bootstrap_code()
    r = client.post("/api/auth/exchange", json={"code": code, "device_name": "test browser"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    yield {"host": host, "client": client, "prov": prov, "token": token}
    host.db.close()


def test_public_health_and_auth_required(env):
    c = TestClient(create_app(env["host"], testing=True))
    assert c.get("/api/health").json()["status"] == "ok"
    r = c.get("/api/system/status")
    assert r.status_code == 401 and r.json()["error"] == "auth_required"
    r = c.get("/api/system/status", headers={"Authorization": "Bearer omos_wrongwrongwrongwrongwrong"})
    assert r.status_code == 401


def test_host_header_allowlist_and_security_headers(env):
    c = env["client"]
    r = c.get("/api/health", headers={"Host": "evil.example.com"})
    assert r.status_code == 421
    r = c.get("/api/health")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    connect = next(d for d in csp.split(";") if d.strip().startswith("connect-src")).split()[1:]
    assert all(src == "'self'" or src.endswith("//testserver") for src in connect), connect  # no wildcard ws:/wss:
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


def test_csp_websocket_source_is_this_host_only():
    from omos.api.app import csp_for

    assert "connect-src 'self' ws://127.0.0.1:8765 wss://127.0.0.1:8765;" in csp_for("127.0.0.1:8765")
    assert "ws://[::1]:8765" in csp_for("[::1]:8765")
    for bad in ("evil.com; script-src *", "a b", "x'y", ""):
        assert "connect-src 'self';" in csp_for(bad)  # anything odd: same-origin only, never injected


def test_scopes_enforced(env):
    host, c = env["host"], env["client"]
    tok, _ = host.auth.create_token("phone", "device", ["read"])
    mon = TestClient(create_app(host, testing=True), headers={"Authorization": f"Bearer {tok}"})
    assert mon.get("/api/bots").status_code == 200
    acct = c.get("/api/accounts").json()[0]["id"]
    r = mon.post("/api/orders", json={"account_id": acct, "instrument_id": "TEST:ABC", "side": "buy", "qty": 1})
    assert r.status_code == 403 and "trade:paper" in r.json()["message"]
    assert mon.put("/api/risk/limits", json={"account_id": acct, "limits": {}}).status_code == 403
    assert mon.get("/api/secrets").status_code == 403


def test_pairing_flow_and_revoke(env):
    c, host = env["client"], env["host"]
    code = c.post("/api/auth/pairing", json={"preset": "monitor", "label": "phone"}).json()["code"]
    fresh = TestClient(create_app(host, testing=True))
    r = fresh.post("/api/auth/exchange", json={"code": code, "device_name": "my phone"})
    assert r.status_code == 200 and r.json()["token_info"]["scopes"] == ["read"]
    tid = r.json()["token_info"]["id"]
    assert c.delete(f"/api/auth/tokens/{tid}").status_code == 200
    host.auth._cache.clear()
    r2 = fresh.get("/api/bots", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert r2.status_code == 401


def test_market_data_indicator_structure_endpoints(env):
    c = env["client"]
    r = c.get("/api/candles", params={"instrument": "TEST:ABC", "tf": "1m", "limit": 100})
    assert r.status_code == 200
    body = r.json()
    assert len(body["bars"]) == 100 and body["status"] == "REALTIME" and body["instrument"]["id"] == "TEST:ABC"
    ha = c.get("/api/candles", params={"instrument": "TEST:ABC", "tf": "1m", "limit": 50, "ha": True}).json()
    assert "Heikin-Ashi" in ha["source"]
    agg = c.get("/api/candles", params={"instrument": "TEST:ABC", "tf": "15m", "limit": 10}).json()
    assert agg["quality"]["aggregated_from"] == "1m"
    ind = c.post("/api/indicators/compute", json={"instrument": "TEST:ABC", "tf": "1m", "id": "rsi", "params": {"length": 14},
                                                  "limit": 200}).json()
    assert len(ind["ts"]) == 200 and ind["outputs"]["value"][0] is None and ind["outputs"]["value"][-1] is not None
    assert len(c.get("/api/indicators").json()) >= 30
    s = c.get("/api/structure", params={"instrument": "TEST:ABC", "tf": "1m"}).json()
    assert "events" in s and "methodology" in s
    a = c.get("/api/analysis", params={"instrument": "TEST:ABC", "tf": "1m"}).json()
    assert a["data"]["status"] == "REALTIME" and "rsi14" in a["indicators"]
    bad = c.get("/api/candles", params={"instrument": "NOPE:X", "tf": "1m"})
    assert bad.status_code == 503 and bad.json()["error"] == "data_unavailable"


def test_drawings_and_workspace_persist(env):
    c = env["client"]
    d = c.put("/api/drawings", json={"instrument_id": "TEST:ABC", "kind": "segment",
                                    "points": [{"t": 1700000000000, "v": 100.5}, {"t": 1700000600000, "v": 102}],
                                    "styles": {"line": {"color": "#ff0000"}}}).json()
    assert d["id"].startswith("dr_")
    assert c.get("/api/drawings", params={"instrument": "TEST:ABC"}).json()[0]["points"][1]["v"] == 102
    assert c.put("/api/drawings", json={"instrument_id": "TEST:ABC", "kind": "evil<script>", "points": [{"t": 1, "v": 1}]}).status_code == 422
    c.put("/api/workspaces/main", json={"layout": {"grid": "2x2", "cells": [{"instrument": "TEST:ABC", "tf": "1m"}]}})
    assert c.get("/api/workspaces/main").json()["layout"]["grid"] == "2x2"
    c.delete(f"/api/drawings/{d['id']}")
    assert c.get("/api/drawings", params={"instrument": "TEST:ABC"}).json() == []


def test_strategy_backtest_optimize_flow(env):
    c = env["client"]
    src = "param fast = 5 (3, 9, 2)\nlong_entry = crossover(sma(close, fast), sma(close, 20))\nlong_exit = crossunder(sma(close, fast), sma(close, 20))\n"
    v = c.post("/api/strategies/validate", json={"source": src}).json()
    assert v["is_strategy"] and v["params"][0]["name"] == "fast"
    s = c.post("/api/strategies", json={"name": "cross", "source": src}).json()
    s2 = c.put(f"/api/strategies/{s['id']}", json={"source": src.replace("20", "25"), "note": "slower"}).json()
    assert s2["version"] == 2 and len(c.get(f"/api/strategies/{s['id']}").json()["versions"]) == 2
    bt = c.post("/api/backtest", json={"strategy_id": s["id"], "instrument": "TEST:ABC", "tf": "1m", "limit": 400}).json()
    assert "sharpe" in bt["metrics"] and bt["data"]["status"] == "REALTIME"
    assert c.get("/api/backtests").json()[0]["instrument_id"] == "TEST:ABC"
    opt = c.post("/api/backtest/optimize", json={"source": src, "instrument": "TEST:ABC", "tf": "1m", "limit": 400,
                                                 "objective": "total_return_pct", "min_trades": 1}).json()
    assert opt["combinations"] == 4
    bad = c.post("/api/backtest", json={"source": "x = eval(1)\n", "instrument": "TEST:ABC", "tf": "1m"})
    assert bad.status_code == 422 and bad.json()["error"] == "script_error"
    assert len(c.get("/api/strategies/templates").json()) >= 5


def test_paper_trading_risk_and_emergency_stop(env):
    c = env["client"]
    acct = c.get("/api/accounts").json()[0]
    assert acct["label"] == "PAPER"
    r = c.post("/api/orders", json={"account_id": acct["id"], "instrument_id": "TEST:ABC", "side": "buy", "qty": 1,
                                    "stop_loss": 90}).json()
    assert r["order"]["status"] == "filled" and r["decision"]["approved"]
    big = c.post("/api/orders", json={"account_id": acct["id"], "instrument_id": "TEST:ABC", "side": "buy", "qty": 10_000}).json()
    assert big["order"]["status"] == "rejected"
    summ = c.get(f"/api/accounts/{acct['id']}/summary").json()
    assert summ["open_positions"] == 1
    assert c.get(f"/api/accounts/{acct['id']}/export.csv").text.startswith("time_utc_ms")
    assert c.get("/api/risk/events").json()[0]["decision"] in ("approved", "rejected")
    es = c.post("/api/risk/emergency_stop", json={}).json()
    assert "paused_bots" in es
    after = c.post("/api/orders", json={"account_id": acct["id"], "instrument_id": "TEST:ABC", "side": "buy", "qty": 1,
                                        "stop_loss": 90}).json()
    assert after["order"]["status"] == "rejected"
    assert c.post("/api/risk/clear_emergency").json()["cleared"]
    ok = c.post("/api/orders", json={"account_id": acct["id"], "instrument_id": "TEST:ABC", "side": "sell", "qty": 1}).json()
    assert ok["order"]["status"] == "filled"


def test_live_trading_needs_typed_confirmation(env):
    c = env["client"]
    assert c.post("/api/settings/live-trading", json={"enabled": True, "confirm": "yes"}).status_code == 422
    r = c.post("/api/settings/live-trading", json={"enabled": True, "confirm": "I UNDERSTAND LIVE TRADING USES REAL MONEY"}).json()
    assert r["live_enabled"] is True and r["verified_live_brokers"] == []
    c.post("/api/settings/live-trading", json={"enabled": False})


def test_bot_lifecycle_via_api(env):
    c = env["client"]
    acct = c.get("/api/accounts").json()[0]["id"]
    b = c.post("/api/bots", json={"name": "api bot", "kind": "rule", "instrument_id": "TEST:ABC", "timeframe": "1m",
                                  "account_id": acct, "config": {"script": "long_entry = close > sma(close, 10)\nlong_exit = close < sma(close, 10)\n",
                                                                 "sizing": "pct_equity", "size_value": 5, "lookback_bars": 100}}).json()
    assert b["state"] == "configured"
    assert c.post(f"/api/bots/{b['id']}/start").status_code == 409  # must validate first
    assert c.post(f"/api/bots/{b['id']}/validate").json()["state"] == "validated"
    assert c.post(f"/api/bots/{b['id']}/start").json()["state"] == "running"
    env["host"].runner.tick()
    assert c.post(f"/api/bots/{b['id']}/pause").json()["state"] == "paused"
    assert c.post(f"/api/bots/{b['id']}/resume").json()["state"] == "running"
    assert c.post(f"/api/bots/{b['id']}/stop").json()["state"] == "stopped"
    detail = c.get(f"/api/bots/{b['id']}").json()
    assert detail["performance"]["label"] == "PAPER" and len(detail["history"]) >= 5
    assert c.get(f"/api/bots/{b['id']}/logs").json()


def test_alerts_notifications_screener_watchlists(env):
    c = env["client"]
    a = c.post("/api/alerts", json={"name": "high", "instrument_id": "TEST:XYZ", "kind": "price_above", "params": {"level": 60}}).json()
    assert c.post(f"/api/alerts/{a['id']}/test").json()["test"] is True
    env["host"].alerts.evaluate_all()
    assert c.get("/api/alerts").json()[0]["state"] == "triggered"
    assert any(n["category"] == "alert" for n in c.get("/api/notifications").json())
    wl = c.post("/api/watchlists", json={"name": "t"}).json()
    c.post(f"/api/watchlists/{wl['id']}/items", json={"instrument_id": "TEST:XYZ"})
    c.post(f"/api/watchlists/{wl['id']}/items", json={"instrument_id": "TEST:ABC"})
    q = c.get(f"/api/watchlists/{wl['id']}/quotes", params={"tf": "1m"}).json()
    assert {x["instrument_id"] for x in q} == {"TEST:XYZ", "TEST:ABC"} and q[0]["spark"]
    res = c.post("/api/screener/run", json={"definition": {"universe": {"watchlist_id": wl["id"]}, "timeframe": "1m",
                                                          "filter": {"op": "and", "rules": [{"field": "change_pct_20", "cmp": ">", "value": 0}]}}}).json()
    assert [r["instrument_id"] for r in res["results"]] == ["TEST:XYZ"]


def test_mcp_route_enforcement(env):
    c, host = env["client"], env["host"]
    tok = c.post("/api/mcp/token").json()
    mcp_tok = (host.cfg.data_path / "mcp_token").read_text()
    assert tok["token_info"]["scopes"] == ["mcp"]
    m = TestClient(create_app(host, testing=True), headers={"Authorization": f"Bearer {mcp_tok}"})
    ok = m.get("/api/quote", params={"instrument": "TEST:ABC"}, headers={"X-OMOS-MCP-Tool": "get_quote"})
    assert ok.status_code == 200
    # wrong tool for the route, no tool, disabled tool, admin route: all refused by the HOST
    assert m.get("/api/quote", params={"instrument": "TEST:ABC"}, headers={"X-OMOS-MCP-Tool": "list_alerts"}).status_code == 403
    assert m.get("/api/quote", params={"instrument": "TEST:ABC"}).status_code == 403
    assert m.get("/api/accounts", headers={"X-OMOS-MCP-Tool": "get_portfolio"}).status_code == 403   # private: off by default
    assert m.get("/api/secrets", headers={"X-OMOS-MCP-Tool": "get_quote"}).status_code == 403
    assert m.post("/api/orders", json={}, headers={"X-OMOS-MCP-Tool": "get_quote"}).status_code == 403
    assert m.get("/api/mcp/self").json()["enabled"]["get_quote"] is True
    c.put("/api/mcp/permissions", json={"get_portfolio": True})
    assert m.get("/api/accounts", headers={"X-OMOS-MCP-Tool": "get_portfolio"}).status_code == 200
    denied = [r for r in c.get("/api/audit").json() if r["action"] == "mcp.denied"]
    assert len(denied) >= 4


def test_mcp_server_in_process_end_to_end(env):
    """Real MCP protocol: an MCP client talks to our MCP server, which calls the host API."""
    import anyio
    from mcp import Client

    from omos.mcp.server import HostAPI, build_server

    c, host = env["client"], env["host"]
    c.post("/api/mcp/token")
    mcp_tok = (host.cfg.data_path / "mcp_token").read_text()
    api = HostAPI("http://testserver", mcp_tok, client=TestClient(create_app(host, testing=True)))
    server = build_server(api, {"get_quote": True, "get_candles": True, "run_backtest": True, "create_alert": False})

    async def main():
        async with Client(server) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            res = await client.call_tool("get_quote", {"instrument_id": "TEST:ABC"})
            text = res.content[0].text
            return tools, json.loads(text)

    tools, quote = anyio.run(main)
    assert tools == {"get_quote", "get_candles", "run_backtest"}  # disabled tools are not even listed
    assert quote["instrument_id"] == "TEST:ABC" and quote["status"] == "REALTIME"


def test_ai_mock_provider_and_cost_gate(env):
    c = env["client"]
    p = c.post("/api/ai/providers", json={"name": "mock", "preset": "mock"}).json()
    assert p["enabled"] == 0
    assert c.post("/api/ai/analyze", json={"provider_id": p["id"], "instrument": "TEST:ABC", "tf": "1m"}).status_code == 403
    c.patch(f"/api/ai/providers/{p['id']}", json={"enabled": True})
    out = c.post("/api/ai/analyze", json={"provider_id": p["id"], "instrument": "TEST:ABC", "tf": "1m"}).json()
    res = out["result"]
    assert res["label"].startswith("AI-generated") and "MOCK" in res["summary"]
    assert res["verification"]["numeric_observations"] >= 1 and res["verification"]["verified"] == res["verification"]["numeric_observations"]
    debate = c.post("/api/ai/debate", json={"provider_id": p["id"], "instrument": "TEST:ABC", "tf": "1m"}).json()
    assert set(debate["views"]) == {"bull", "bear", "risk"} and "consensus" in debate
    paid = c.post("/api/ai/providers", json={"name": "oa", "preset": "openai", "api_key": "sk-test-not-real-key-123"}).json()
    r = c.patch(f"/api/ai/providers/{paid['id']}", json={"enabled": True})
    assert r.status_code == 403 and "may charge" in r.json()["message"]
    assert "sk-test" not in json.dumps(c.get("/api/ai/providers").json())   # keys never returned


def test_workflow_dry_run_via_api(env):
    c = env["client"]
    acct = c.get("/api/accounts").json()[0]["id"]
    wf = c.post("/api/workflows", json={"name": "dip buyer", "definition": {
        "trigger": {"type": "schedule", "every_minutes": 60},
        "steps": [{"type": "condition", "instrument": "TEST:XYZ", "timeframe": "1m", "expression": "close > 0"},
                  {"type": "risk_check", "account_id": acct},
                  {"type": "notify", "title": "Price {{step1.value}}"},
                  {"type": "paper_order", "account_id": acct, "instrument": "TEST:XYZ", "side": "buy", "qty": 0.1,
                   "stop_loss_pct": 5}]}}).json()
    assert wf["enabled"] == 0
    run = c.post(f"/api/workflows/{wf['id']}/run", json={"dry_run": True}).json()
    assert run["status"] == "completed" and run["log"][3]["result"]["dry_run"] is True
    assert c.get(f"/api/accounts/{acct}/orders").json() == []  # dry run placed nothing
    real = c.post(f"/api/workflows/{wf['id']}/run", json={"dry_run": False}).json()
    assert real["status"] == "completed" and real["log"][3]["result"]["status"] == "filled"
    assert c.get(f"/api/accounts/{acct}/orders").json()[0]["origin"] == "workflow"


def test_settings_secrets_backup_export(env):
    c = env["client"]
    assert c.put("/api/settings", json={"ai.daily_budget_usd": 2.5}).json()["ai.daily_budget_usd"] == 2.5
    assert c.put("/api/settings", json={"trading.live_enabled": True}).status_code == 422  # not settable here
    c.put("/api/secrets/fred.api_key", json={"value": "abc123secret"})
    s = c.get("/api/secrets").json()
    assert s["names"] == ["fred.api_key"] and "abc123secret" not in json.dumps(s)
    assert c.post("/api/backups").json()["file"].startswith("omos-")
    assert c.post("/api/export").json()["file"].endswith(".zip")


def test_websocket_auth_and_candle_push(env):
    host, c, token = env["host"], env["client"], env["token"]
    with c.websocket_connect("/api/ws") as ws:
        ws.send_text(json.dumps({"token": "omos_bad"}))
        msg = json.loads(ws.receive_text())
        assert msg["topic"] == "error"
    with c.websocket_connect("/api/ws") as ws:
        ws.send_text(json.dumps({"token": token}))
        assert json.loads(ws.receive_text())["topic"] == "hello"
        ws.send_text(json.dumps({"type": "subscribe", "instrument": "TEST:ABC", "tf": "1m"}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text())["topic"] == "pong"
        host._poll_market()
        for _ in range(10):
            evt = json.loads(ws.receive_text())
            if evt["topic"] == "candle":
                assert evt["data"]["instrument"] == "TEST:ABC" and evt["data"]["status"] == "REALTIME"
                break
        else:
            pytest.fail("no candle event received")


def test_unknown_api_route_and_errors_hide_internals(env):
    c = env["client"]
    r = c.get("/api/does-not-exist")
    assert r.status_code == 404
    r = c.post("/api/orders", json={"account_id": "x"})
    assert r.status_code == 422 and "Traceback" not in r.text


def test_shutdown_hooks_run_on_lifespan_exit(env):
    """uvicorn re-raises SIGTERM after graceful shutdown, so host cleanup must run in the lifespan."""
    app = create_app(env["host"], testing=True)
    ran = []
    app.state.shutdown_hooks = [lambda: ran.append("a"), lambda: 1 / 0, lambda: ran.append("b")]
    with TestClient(app):
        assert ran == []
    assert ran == ["a", "b"]  # a failing hook does not skip the others


def test_scheduled_screener_runs_and_notifies_only_new_matches(env):
    host, c = env["host"], env["client"]
    wl = c.post("/api/watchlists", json={"name": "sched"}).json()
    c.post(f"/api/watchlists/{wl['id']}/items", json={"instrument_id": "TEST:ABC"})
    definition = {"universe": {"watchlist_id": wl["id"]}, "timeframe": "1m",
                  "filter": {"op": "and", "rules": [{"field": "rsi14", "cmp": ">=", "value": 0}]}}
    r = c.post("/api/screeners", json={"name": "always", "definition": definition, "schedule_minutes": 5})
    assert r.status_code == 200, r.text

    def screener_notes():
        return [n for n in c.get("/api/notifications").json() if n["category"] == "screener"]

    host._automation()                       # due: never run before
    notes = screener_notes()
    assert len(notes) == 1 and "1 new match" in notes[0]["title"] and "TEST:ABC" in notes[0]["body"]
    host._automation()                       # not due again for 5 minutes
    sid = c.get("/api/screeners").json()[0]["id"]
    host.db.execute("UPDATE screeners SET last_run_at = 0 WHERE id = ?", (sid,))
    host._automation()                       # due again, same match: no repeat notification
    assert len(screener_notes()) == 1
    assert len(host.db.query("SELECT id FROM screener_runs WHERE screener_id = ?", (sid,))) == 2


def test_remote_access_accepts_this_machines_own_addresses_only(tmp_path, monkeypatch):
    """Regression: with --allow-remote, phones addressing the PC by its LAN IP got HTTP 421."""
    from omos import config

    monkeypatch.setenv("OMOS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OMOS_VAULT_PASSPHRASE", "test vault passphrase")
    monkeypatch.setattr(config, "local_host_names", lambda: {"192.168.1.50", "[fe80::1]", "mypc", "mypc.local"})
    host = Host(HostConfig(data_dir=str(tmp_path / "r"), host="0.0.0.0", allow_remote=True), start_loops=False,
                providers={"TEST": ControlledProvider()})
    c = TestClient(create_app(host))
    for ok in ("192.168.1.50:8765", "mypc.local:8765", "[fe80::1]:8765", "127.0.0.1:8765"):
        assert c.get("/api/health", headers={"Host": ok}).status_code == 200, ok
    for bad in ("evil.example.com", "192.168.1.50.evil.com", "mypc.evil.com"):
        assert c.get("/api/health", headers={"Host": bad}).status_code == 421, bad
    local = TestClient(create_app(Host(HostConfig(data_dir=str(tmp_path / "l")), start_loops=False,
                                       providers={"TEST": ControlledProvider()})))
    assert local.get("/api/health", headers={"Host": "192.168.1.50:8765"}).status_code == 421  # remote off
    host.db.close()
