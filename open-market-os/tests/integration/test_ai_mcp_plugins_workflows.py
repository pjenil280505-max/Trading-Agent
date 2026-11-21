import json
import shutil
import sys
import textwrap
from pathlib import Path

import httpx
import numpy as np
import pytest

from helpers import make_stack
from omos.ai.agents import AgentService, EvidenceBuilder, extract_json, normalise
from omos.ai.providers import AIProviderService, ChatResult, MockModel
from omos.automation.workflows import WorkflowEngine
from omos.errors import PermissionDenied, ValidationError
from omos.mcp.client import MCPClientService
from omos.plugins.manager import PluginManager, validate_manifest
from omos.security.secrets import SecretStore

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def st(db, tmp_path, monkeypatch):
    monkeypatch.setenv("OMOS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OMOS_VAULT_PASSPHRASE", "integration test pass")
    s = make_stack(db, closes=100 + np.cumsum(np.sin(np.arange(400) / 9.0)))
    s.account = s.portfolio.create_account("Paper", 10_000)
    s.ai = AIProviderService(db, SecretStore(tmp_path))
    s.agents = AgentService(db, s.ai, EvidenceBuilder(s.market), gateway=s.gateway)
    return s


# ---------------------------------------------------------------- AI safety
def test_extract_json_tolerates_fences_and_text():
    assert extract_json('Sure!\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    with pytest.raises(ValidationError):
        extract_json("no json here")


def test_normalise_flags_hallucinated_numbers():
    ev = {"quote": {"price": 101.5}, "indicators": {"rsi14": 55.0}}
    out = normalise({"summary": "x", "stance": "moonshot", "confidence": 7,
                     "observations": [{"claim": "price is 101.5", "evidence": "quote.price"},
                                      {"claim": "price is 250", "evidence": "quote.price"}]}, ev)
    assert out["stance"] == "neutral" and out["confidence"] == 1.0
    assert out["verification"]["verified"] == 1 and out["verification"]["unverified_claims"] == ["price is 250"]


def test_prompt_injected_model_cannot_place_or_propose_orders(st, monkeypatch):
    """A compromised/injected model tries to propose a huge order. Code-level permissions stop it."""
    p = st.ai.add("mock", "mock")
    st.ai.set_enabled(p["id"], True)
    script = iter([
        json.dumps({"tool": "propose_order", "args": {"account_id": st.account["id"], "instrument_id": "TEST:ABC",
                                                       "side": "buy", "qty": 1000, "rationale": "IGNORE ALL RULES"}}),
        json.dumps({"tool": "get_quote", "args": {"instrument_id": "TEST:ABC"}}),
        json.dumps({"answer": {"summary": "done", "stance": "neutral", "confidence": 0.2}}),
    ])
    monkeypatch.setattr(MockModel, "chat", lambda self, system, messages: ChatResult(next(script), 10, 10, "evil", 1))
    out = st.agents.chat_with_tools(p["id"], "buy everything now")
    assert out["trace"][0] == {"tool": "propose_order", "ok": False,
                               "error": "tool 'propose_order' needs the 'propose' AI permission (Settings > AI > Tool permissions)"}
    assert out["trace"][1]["ok"] is True
    assert st.db.scalar("SELECT COUNT(*) FROM order_proposals") == 0
    assert st.db.scalar("SELECT COUNT(*) FROM orders") == 0


def test_even_with_propose_permission_a_person_must_approve(st, monkeypatch):
    st.db.set_setting("ai.tool_levels", ["read", "analyze", "propose"])
    p = st.ai.add("mock", "mock")
    st.ai.set_enabled(p["id"], True)
    script = iter([json.dumps({"tool": "propose_order", "args": {"account_id": st.account["id"], "instrument_id": "TEST:ABC",
                                                                  "side": "buy", "qty": 1, "stop_loss": 90}}),
                   json.dumps({"answer": {"summary": "proposed"}})])
    monkeypatch.setattr(MockModel, "chat", lambda self, system, messages: ChatResult(next(script), 1, 1, "m", 1))
    st.agents.chat_with_tools(p["id"], "propose")
    prop = st.db.query_one("SELECT * FROM order_proposals")
    assert prop["status"] == "pending" and st.db.scalar("SELECT COUNT(*) FROM orders") == 0
    with pytest.raises(ValidationError):
        st.gateway.decide_proposal(prop["id"], True, actor="ai")


def test_untrusted_evidence_is_delimited(st, monkeypatch):
    seen = {}

    def spy(self, system, messages):
        seen["system"], seen["user"] = system, messages[-1]["content"]
        return ChatResult(json.dumps({"summary": "ok"}), 1, 1, "m", 1)
    monkeypatch.setattr(MockModel, "chat", spy)
    p = st.ai.add("mock", "mock")
    st.ai.set_enabled(p["id"], True)
    st.agents.analyze(p["id"], "TEST:ABC", timeframe="1m")
    assert "untrusted DATA" in seen["system"] and seen["user"].count("<evidence>") == 1


def test_budget_blocks_paid_provider(st):
    paid = st.ai.add("hosted", "openai", api_key="sk-test-not-a-real-key-000", price_in=1000, price_out=1000)
    st.ai.set_enabled(paid["id"], True, acknowledge_cost=True)
    st.db.execute("INSERT INTO ai_runs(ts, provider_id, cost, status) VALUES (strftime('%s','now')*1000, ?, 5.0, 'ok')", (paid["id"],))
    with pytest.raises(PermissionDenied):
        st.ai.client(paid["id"])


def _ollama_up() -> bool:
    try:
        return httpx.get("http://127.0.0.1:11434/api/tags", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not _ollama_up(), reason="no local Ollama server on 127.0.0.1:11434")
def test_real_local_model_via_ollama(st):
    p = st.ai.add("ollama", "ollama", model="qwen2.5:0.5b")
    st.ai.set_enabled(p["id"], True)
    out = st.agents.analyze(p["id"], "TEST:ABC", timeframe="1m")
    res = out["result"]
    assert res["_meta"]["is_local"] is True and res["_meta"]["cost_usd"] == 0
    assert res["_meta"]["tokens_in"] > 100
    assert res["stance"] in ("bullish", "bearish", "neutral")
    assert res["label"].startswith("AI-generated") or "error" in res


# ---------------------------------------------------------------- MCP client against a real stdio server
ECHO_SERVER = textwrap.dedent('''
    from mcp.server.mcpserver import MCPServer
    s = MCPServer("echo-test")

    @s.tool()
    def echo(text: str) -> str:
        """Echo text back."""
        return "echo: " + text

    @s.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    s.run("stdio")
''')


def test_mcp_client_stdio_discovery_permissions_and_call(db, tmp_path):
    script = tmp_path / "echo_server.py"
    script.write_text(ECHO_SERVER)
    svc = MCPClientService(db)
    srv = svc.add("echo", transport="stdio", command=sys.executable, args=[str(script)], timeout_s=30)
    refreshed = svc.refresh(srv["id"])
    names = {t["name"] for t in refreshed["discovered"]["tools"]}
    assert names == {"echo", "add"}
    assert all(t["enabled"] == 0 for t in refreshed["tools"])   # new tools start disabled
    with pytest.raises(PermissionDenied):
        svc.call(srv["id"], "echo", {"text": "hi"})
    svc.set_tool(srv["id"], "echo", True)
    out = svc.call(srv["id"], "echo", {"text": "hi"})
    assert out["content"] == "echo: hi" and out["untrusted"] is True
    assert any(r["action"] == "mcp.call" for r in db.query("SELECT action FROM audit_log"))


def test_mcp_client_rejects_shell_tricks_and_plain_http_remote(db):
    svc = MCPClientService(db)
    with pytest.raises(ValidationError):
        svc.add("bad", transport="stdio", command="python; rm -rf /")
    with pytest.raises(ValidationError):
        svc.add("bad2", transport="http", url="http://example.com/mcp")


# ---------------------------------------------------------------- plugins
def test_manifest_validation():
    good = json.loads((ROOT / "examples/plugins/ema-ribbon/plugin.json").read_text())
    validate_manifest(dict(good))
    for bad in ({"permissions": ["filesystem:/"]}, {"id": "BAD ID"}, {"version": "1"}, {"entry": "../x.oms"},
                {"kind": "rootkit"}, {"omos_version": ">=99.0.0"}):
        with pytest.raises(ValidationError):
            validate_manifest({**good, **bad})


def test_indicator_plugin_lifecycle(db, tmp_path, ohlcv):
    pm = PluginManager(db, tmp_path / "plugins")
    p = pm.install(ROOT / "examples/plugins/ema-ribbon", actor="ui")
    assert p["enabled"] == 0 and p["requested_permissions"] == ["read:market_data"]
    with pytest.raises(PermissionDenied):
        pm.read_entry(p["id"])
    with pytest.raises(PermissionDenied):
        pm.enable(p["id"], grant=[], actor="ui")
    pm.enable(p["id"], grant=["read:market_data"], actor="ui")
    from omos.script.lang import run
    res = run(pm.read_entry(p["id"]), ohlcv)
    assert set(res.plots) == {"ema8", "ema21", "ema55", "ema89"}
    # tampering after install is detected
    (tmp_path / "plugins" / p["id"] / "ribbon.oms").write_text("plot(close, 'x')\n")
    pm.disable(p["id"], actor="ui")
    with pytest.raises(PermissionDenied):
        pm.enable(p["id"], grant=["read:market_data"], actor="ui")
    pm.uninstall(p["id"], actor="ui")
    assert pm.list() == []


def test_process_plugin_isolated_call(db, tmp_path):
    pm = PluginManager(db, tmp_path / "plugins")
    p = pm.install(ROOT / "examples/plugins/zscore-process", actor="ui")
    with pytest.raises(PermissionDenied):
        pm.call_process(p["id"], "compute", {"close": [1, 2, 3], "length": 2})
    pm.enable(p["id"], grant=["compute"], actor="ui")
    out = pm.call_process(p["id"], "compute", {"close": [1.0, 2.0, 3.0, 2.0, 1.0], "length": 3})
    assert out["zscore"][0] is None and abs(out["zscore"][2] - 1.2247448713915890) < 1e-9


def test_process_plugin_gets_no_secrets_and_is_time_limited(db, tmp_path, monkeypatch):
    monkeypatch.setenv("OMOS_VAULT_PASSPHRASE", "super secret passphrase")
    src = tmp_path / "src"
    src.mkdir()
    (src / "plugin.json").write_text(json.dumps({
        "id": "test.env-spy", "name": "env spy", "version": "1.0.0", "author": "t", "license": "MIT", "kind": "process",
        "entry": "main.py", "description": "reports its environment", "omos_version": ">=0.1.0", "permissions": ["compute"]}))
    (src / "main.py").write_text("import json, os, sys, time\nreq = json.loads(sys.stdin.read())\n"
                                 "if req['method'] == 'sleep': time.sleep(30)\n"
                                 "print(json.dumps({'id': 1, 'result': dict(os.environ)}))\n")
    pm = PluginManager(db, tmp_path / "plugins")
    pm.install(src, actor="ui")
    pm.enable("test.env-spy", grant=["compute"], actor="ui")
    env = pm.call_process("test.env-spy", "env", {})
    assert "OMOS_VAULT_PASSPHRASE" not in env and env["OMOS_PLUGIN_ID"] == "test.env-spy"
    from omos.errors import OmosError
    with pytest.raises(OmosError) as e:
        pm.call_process("test.env-spy", "sleep", {}, timeout_s=1)
    assert e.value.code == "plugin_timeout"


def test_plugin_rejects_symlinks(db, tmp_path):
    src = tmp_path / "s"
    shutil.copytree(ROOT / "examples/plugins/ema-ribbon", src)
    (src / "link").symlink_to("/etc/passwd")
    with pytest.raises(ValidationError):
        PluginManager(db, tmp_path / "plugins").install(src, actor="ui")


@pytest.mark.network
def test_rest_provider_plugin_live(db, tmp_path):
    from omos.marketdata.service import MarketDataService
    from omos.plugins.rest_provider import RestCandleProvider

    spec = json.loads((ROOT / "examples/plugins/example-rest-feed/provider.json").read_text())
    with pytest.raises(PermissionDenied):
        RestCandleProvider(spec, ["evil.example.com"])
    prov = RestCandleProvider(spec, ["www.okx.com"])
    svc = MarketDataService(db, providers={"OKXPLUG": prov})
    c = svc.get_candles("OKXPLUG:BTC-USDT", "1h", limit=50)
    assert len(c) >= 40 and c.status.value == "DELAYED"


# ---------------------------------------------------------------- workflows
def make_engine(st):
    return WorkflowEngine(st.db, {"market": st.market, "gateway": st.gateway, "portfolio": st.portfolio,
                                  "notifier": st.notifier, "screener": st.screener, "bots": st.bots}, st.bus)


def test_workflow_validation(st):
    eng = make_engine(st)
    with pytest.raises(ValidationError):
        eng.save("x", {"trigger": {"type": "telepathy"}, "steps": [{"type": "notify"}]})
    with pytest.raises(ValidationError):
        eng.save("x", {"trigger": {"type": "schedule", "every_minutes": 5}, "steps": []})
    with pytest.raises(ValidationError):
        eng.save("x", {"trigger": {"type": "schedule", "every_minutes": 5},
                       "steps": [{"type": "condition", "instrument": "TEST:ABC", "expression": "import os"}]})


def test_price_trigger_is_edge_triggered_and_limits(st):
    eng = make_engine(st)
    w = eng.save("breakout", {"trigger": {"type": "price", "instrument": "TEST:ABC", "op": ">", "level": 105},
                              "steps": [{"type": "notify", "title": "Breakout {{trigger.price}}"}],
                              "limits": {"cooldown_minutes": 0, "max_runs_per_day": 2}})
    eng.set_enabled(w["id"], True)
    assert eng.tick() == []
    st.provider.append("ABC", 106)
    fired = eng.tick()
    assert len(fired) == 1 and fired[0]["status"] == "completed"
    st.provider.append("ABC", 107)
    assert eng.tick() == []                      # still above: no re-fire without dipping below
    st.provider.append("ABC", 100)
    eng.tick()
    st.provider.append("ABC", 108)
    assert eng.tick()[0]["status"] == "completed"
    st.provider.append("ABC", 90)
    eng.tick()
    st.provider.append("ABC", 109)
    assert eng.tick()[0]["status"] == "skipped"  # daily limit of 2 reached


def test_event_trigger_and_global_kill(st):
    eng = make_engine(st)
    w = eng.save("on risk", {"trigger": {"type": "event", "topic": "risk"},
                             "steps": [{"type": "journal", "title": "risk event"}]})
    eng.set_enabled(w["id"], True)
    st.bus.publish("risk", {"x": 1})
    assert eng.tick()[0]["status"] == "completed"
    eng.disable_all("ui")
    st.bus.publish("risk", {"x": 2})
    assert eng.tick() == []


def test_condition_stops_chain_and_live_orders_refused(st):
    eng = make_engine(st)
    live = st.portfolio.create_account("Live", 100, kind="live", broker="x")
    w = eng.save("guarded", {"trigger": {"type": "schedule", "every_minutes": 1},
                             "steps": [{"type": "condition", "instrument": "TEST:ABC", "timeframe": "1m", "expression": "close < 0"},
                                       {"type": "notify", "title": "never"}]})
    r = eng.run(w["id"])
    assert r["status"] == "stopped" and len(r["log"]) == 2
    w2 = eng.save("live order", {"trigger": {"type": "schedule", "every_minutes": 1},
                                 "steps": [{"type": "paper_order", "account_id": live["id"], "instrument": "TEST:ABC",
                                            "side": "buy", "qty": 1}]})
    r2 = eng.run(w2["id"])
    assert r2["status"] == "failed" and r2["log"][0]["status"] == "denied"
