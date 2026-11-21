import numpy as np
import pytest

from helpers import make_stack
from omos.errors import InvalidState, ValidationError

CROSS = """
long_entry = close > sma(close, 5)
long_exit = close < sma(close, 5)
stop_loss_pct = 5
"""


@pytest.fixture
def st(db):
    closes = np.r_[np.full(80, 100.0), np.linspace(100, 90, 20)]  # flat then falling: below SMA, no entry yet
    s = make_stack(db, closes=closes)
    s.account = s.portfolio.create_account("Paper", 10_000)
    s.risk.set_limits(s.account["id"], {"max_order_value_pct": 100, "max_instrument_exposure_pct": 100,
                                        "max_risk_per_trade_pct": 10}, actor="ui")
    return s


def new_bot(st, kind="rule", config=None, name="b1"):
    cfg = config or {"script": CROSS, "sizing": "pct_equity", "size_value": 20, "lookback_bars": 60}
    return st.bots.create(name, kind, "TEST:ABC", "1m", account_id=st.account["id"], config=cfg)


def test_lifecycle_and_illegal_transitions(st):
    b = new_bot(st)
    assert b["state"] == "configured"
    with pytest.raises(InvalidState):
        st.bots.start(b["id"])                 # not validated yet
    b = st.bots.validate(b["id"])
    assert b["state"] == "validated"
    res = st.bots.backtest(b["id"])
    assert "metrics" in res and st.bots.get(b["id"])["state"] == "backtested"
    st.bots.start(b["id"])
    with pytest.raises(InvalidState):
        st.bots.configure(b["id"], {"script": CROSS})   # must pause first
    st.bots.pause(b["id"])
    st.bots.resume(b["id"])
    st.bots.stop(b["id"])
    with pytest.raises(ValidationError):
        st.bots.mark_paper_tested(b["id"])     # needs >= 24h of paper running
    states = [h["to_state"] for h in st.bots.history(b["id"])]
    assert states == ["created", "configured", "validated", "backtested", "running", "paused", "running", "stopped"]


def test_live_approval_requires_paper_test_and_exact_confirmation(st):
    b = st.bots.validate(new_bot(st)["id"])
    with pytest.raises(ValidationError):
        st.bots.approve_live(b["id"], actor="ai", confirm="APPROVE b1")
    with pytest.raises(InvalidState):
        st.bots.approve_live(b["id"], actor="ui", confirm="APPROVE b1")


def test_rule_bot_trades_on_new_closed_bars_only(st):
    b = st.bots.validate(new_bot(st)["id"])
    st.bots.start(b["id"])
    st.runner.tick()
    assert st.db.scalar("SELECT COUNT(*) FROM orders") == 0  # price below SMA: no entry
    for p in (95, 99, 104, 106):
        st.provider.append("ABC", p)
    st.runner.tick()
    orders = st.db.query("SELECT * FROM orders")
    assert len(orders) == 1 and orders[0]["side"] == "buy" and orders[0]["status"] == "filled"
    assert orders[0]["origin"] == f"bot:{b['id']}"
    # same bar again -> no new order
    st.runner.tick()
    assert st.db.scalar("SELECT COUNT(*) FROM orders") == 1
    # price falls below SMA -> exit on the next closed bar
    for p in (100, 92):
        st.provider.append("ABC", p, low=91.9)
    st.runner.tick()
    sides = [o["side"] for o in st.db.query("SELECT side FROM orders ORDER BY id")]
    assert sides[-1] == "sell"
    assert st.portfolio.positions(st.account["id"]) == []


def test_restart_recovery_does_not_duplicate_orders(st):
    b = st.bots.validate(new_bot(st)["id"])
    st.bots.start(b["id"])
    for p in (95, 99, 104, 106):
        st.provider.append("ABC", p)
    st.runner.tick()
    n_orders = st.db.scalar("SELECT COUNT(*) FROM orders")
    # simulate a crash right after submitting: last_bar_ts was never saved
    st.db.execute("UPDATE bots SET last_bar_ts = NULL WHERE id = ?", (b["id"],))
    from helpers import make_stack as _ms  # noqa: F401  (same DB, fresh services = new process)
    from omos.bots.service import BotRunner
    runner2 = BotRunner(st.bots, tick_s=0.05)
    resumed = runner2.recover_after_restart()
    assert resumed == [b["id"]]
    runner2.tick()
    assert st.db.scalar("SELECT COUNT(*) FROM orders") == n_orders  # idempotent client order id
    logs = [l["message"] for l in st.bots.logs(b["id"])]
    assert any("host restarted" in m for m in logs)


def test_stop_loss_exit_triggers_intrabar(st):
    b = st.bots.validate(new_bot(st)["id"])
    st.bots.start(b["id"])
    for p in (95, 99, 104, 106):
        st.provider.append("ABC", p)
    st.runner.tick()
    entry = st.db.query_one("SELECT avg_fill_price FROM orders WHERE side='buy'")["avg_fill_price"]
    st.provider.set_closes("ABC", [*[r[4] for r in st.provider.series["ABC"]], entry * 0.9], open_last=True)
    st.runner.tick()
    last = st.db.query_one("SELECT * FROM orders ORDER BY id DESC LIMIT 1")
    assert last["side"] == "sell" and last["reason"] == "stop_loss"


def test_bot_errors_escalate_to_error_state(st):
    b = st.bots.validate(new_bot(st)["id"])
    st.bots.start(b["id"])
    st.provider.fail = True
    st.db.execute("DELETE FROM candles")  # no cache either
    for _ in range(5):
        st.runner._backoff.clear()
        st.runner.tick()
    assert st.bots.get(b["id"])["state"] == "error"


def test_bot_paused_by_risk_halt_and_emergency_stop(st):
    b = st.bots.validate(new_bot(st)["id"])
    st.bots.start(b["id"])
    out = st.bots.emergency_stop(actor="ui")
    assert out["paused_bots"] == 1 and st.bots.get(b["id"])["state"] == "paused"
    with pytest.raises(InvalidState):
        st.bots.start(b["id"])   # emergency stop must be cleared by a person first
    st.risk.set_global_halt(False, "", actor="ui")
    assert st.bots.start(b["id"])["state"] == "running"


def test_dca_bot_buys_on_schedule_and_respects_cap(st):
    cfg = {"sizing": "fixed_cash", "size_value": 100, "every_bars": 2, "max_invested": 250, "lookback_bars": 60}
    b = st.bots.validate(new_bot(st, "dca", cfg, name="dca")["id"])
    st.bots.start(b["id"])
    for p in (91, 92, 93, 94, 95, 96, 97, 98):
        st.provider.append("ABC", p)
        st.runner.tick()
    buys = st.db.query("SELECT * FROM orders WHERE status = 'filled'")
    invested = sum(o["filled_qty"] * o["avg_fill_price"] for o in buys)
    assert 2 <= len(buys) <= 3 and invested <= 250 + 1e-6


def test_grid_bot_places_limits_below_price(st):
    cfg = {"lower": 80, "upper": 100, "levels": 5, "qty_per_level": 1, "sizing": "fixed_qty", "size_value": 1,
           "lookback_bars": 60}
    b = st.bots.validate(new_bot(st, "grid", cfg, name="grid")["id"])
    st.bots.start(b["id"])
    st.provider.append("ABC", 90.5)
    st.runner.tick()
    resting = st.db.query("SELECT * FROM orders WHERE status = 'accepted'")
    assert resting and all(o["type"] == "limit" and o["side"] == "buy" and o["limit_price"] < 90.5 for o in resting)


def test_bot_config_validation(st):
    with pytest.raises(ValidationError):
        new_bot(st, config={"script": "x = 1\n"})
    with pytest.raises(ValidationError):
        new_bot(st, "grid", {"lower": 10, "upper": 5, "levels": 3, "qty_per_level": 1}, name="g")


# ------------------------------------------------------------------ alerts
def test_price_alert_once_and_notification(st):
    a = st.alerts.create(name="above 95", instrument_id="TEST:ABC", kind="price_above", params={"level": 95})
    assert st.alerts.evaluate_all() == []       # last close 90
    st.provider.append("ABC", 96)
    fired = st.alerts.evaluate_all()
    assert len(fired) == 1 and st.alerts.get(a["id"])["state"] == "triggered"
    assert st.alerts.evaluate_all() == []       # once
    assert st.notifier.list()[0]["category"] == "alert"


def test_cross_alert_edge_triggered(st):
    st.alerts.create(name="x", instrument_id="TEST:ABC", kind="price_cross", params={"level": 92}, frequency="every")
    st.alerts.evaluate_all()                     # sets last value (90)
    st.provider.append("ABC", 93)
    assert len(st.alerts.evaluate_all()) == 1
    st.provider.append("ABC", 94)
    assert st.alerts.evaluate_all() == []        # no new cross


def test_expression_alert_and_snooze_and_test_fire(st):
    a = st.alerts.create(name="rsi", instrument_id="TEST:ABC", kind="expression",
                         params={"expression": "close > sma(close, 3)"}, frequency="once_per_bar")
    st.provider.append("ABC", 99)
    assert len(st.alerts.evaluate_all()) == 1
    st.alerts.update_state(a["id"], "snoozed", snooze_minutes=5)
    st.provider.append("ABC", 80)
    st.provider.append("ABC", 99)
    assert st.alerts.evaluate_all() == []
    ev = st.alerts.test_fire(a["id"])
    assert ev["test"] is True


def test_alert_validation(st):
    with pytest.raises(ValidationError):
        st.alerts.create(name="", instrument_id="TEST:ABC", kind="price_above", params={})
    with pytest.raises(ValidationError):
        st.alerts.create(name="", instrument_id="TEST:ABC", kind="expression", params={"expression": "import os"})


def test_alert_expiry(st):
    a = st.alerts.create(name="e", instrument_id="TEST:ABC", kind="price_above", params={"level": 1}, expires_at=1)
    st.alerts.evaluate_all()
    assert st.alerts.get(a["id"])["state"] == "expired"


# ------------------------------------------------------------------ screener / watchlists
def test_screener_with_rules_expr_and_coverage(st):
    st.provider.set_closes("UP", np.linspace(50, 100, 260))
    st.provider.set_closes("DOWN", np.linspace(100, 50, 260))
    wl = st.watchlists.create("Test")
    for s in ("TEST:UP", "TEST:DOWN", "TEST:MISSING"):
        st.watchlists.add(wl["id"], s)
    res = st.screener.run({"universe": {"watchlist_id": wl["id"]}, "timeframe": "1m",
                           "filter": {"op": "and", "rules": [{"field": "change_pct_20", "cmp": ">", "value": 0},
                                                             {"expr": "close > sma(close, 50)"}]},
                           "sort": {"field": "change_pct", "desc": True}})
    assert [r["instrument_id"] for r in res["results"]] == ["TEST:UP"]
    assert "TEST:MISSING" in res["errors"] and res["scanned"] == 2
    assert "Nothing outside this universe" in res["coverage_note"]
    csv_text = st.screener.to_csv(res)
    assert csv_text.splitlines()[1].startswith("TEST:UP")


def test_screener_or_groups_and_saved(st):
    st.provider.set_closes("UP", np.linspace(50, 100, 260))
    d = {"universe": {"instruments": ["TEST:UP", "TEST:ABC"]}, "timeframe": "1m",
         "filter": {"op": "or", "rules": [{"field": "rsi14", "cmp": ">", "value": 70},
                                          {"op": "and", "rules": [{"field": "price", "cmp": "<", "value": 0}]}]}}
    saved = st.screener.save("hot", d, schedule_minutes=15)
    assert st.screener.due() == [saved["id"]]
    out = st.screener.run_saved(saved["id"])
    assert out["matched"] >= 1 and st.screener.due() == []
    dup = st.screener.duplicate(saved["id"], "hot copy")
    assert dup["definition"] == d


def test_screener_validation(st):
    with pytest.raises(ValidationError):
        st.screener.run({"universe": {}})
    with pytest.raises(ValidationError):
        st.screener.run({"universe": {"instruments": ["TEST:ABC"]}, "filter": {"rules": [{"field": "nope", "cmp": "<", "value": 1}]}})


def test_watchlist_crud(st):
    wl = st.watchlists.create("Mine")
    st.watchlists.add(wl["id"], "TEST:ABC")
    st.watchlists.add(wl["id"], "TEST:UP")
    st.watchlists.reorder(wl["id"], ["TEST:UP", "TEST:ABC"])
    assert st.watchlists.get(wl["id"])["items"] == ["TEST:UP", "TEST:ABC"]
    st.watchlists.remove(wl["id"], "TEST:UP")
    assert st.watchlists.get(wl["id"])["items"] == ["TEST:ABC"]
    with pytest.raises(ValidationError):
        st.watchlists.add(wl["id"], "nocolon")
