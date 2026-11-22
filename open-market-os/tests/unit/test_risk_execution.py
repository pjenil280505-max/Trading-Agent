import pytest

from helpers import make_stack
from omos.errors import NotFound, ValidationError
from omos.execution.models import OrderRequest, OrderStatus, OrderType, Side, check_transition
from omos.risk.engine import AccountSnapshot, MarketContext, PositionView, RiskEngine, RiskLimits


@pytest.fixture
def st(db):
    s = make_stack(db)
    s.account = s.portfolio.create_account("Paper", 10_000)
    s.quotes["TEST:ABC"] = {"price": 100.0, "status": "REALTIME", "bid": 99.98, "ask": 100.02, "session_open": True}
    return s


def order(st, side="buy", qty=1.0, **kw):
    return OrderRequest(account_id=st.account["id"], instrument_id="TEST:ABC", side=Side(side), qty=qty, **kw)


# ------------------------------------------------------------------ order model
def test_state_machine():
    check_transition(OrderStatus.NEW, OrderStatus.FILLED)
    from omos.errors import InvalidState
    with pytest.raises(InvalidState):
        check_transition(OrderStatus.FILLED, OrderStatus.CANCELLED)
    with pytest.raises(InvalidState):
        check_transition(OrderStatus.REJECTED, OrderStatus.ACCEPTED)


@pytest.mark.parametrize("bad", [dict(qty=0), dict(qty=-1), dict(qty=float("nan")), dict(type=OrderType.LIMIT),
                                 dict(type=OrderType.STOP), dict(tif="forever"), dict(origin="hacker"),
                                 dict(stop_loss=-5)])
def test_order_validation(st, bad):
    with pytest.raises(ValidationError):
        order(st, **bad).validate()


# ------------------------------------------------------------------ paper fills, P&L, cash
def test_market_buy_then_sell_pnl_and_cash(st):
    r = st.gateway.submit(order(st, qty=10, stop_loss=95), actor="ui")
    o = r["order"]
    assert o["status"] == "filled" and r["decision"]["approved"]
    assert o["avg_fill_price"] == pytest.approx(100.02 * 1.0005)  # ask + 5 bps slippage
    acct = st.portfolio.account(st.account["id"])
    fee = 10 * o["avg_fill_price"] * 0.001
    assert acct["cash"] == pytest.approx(10_000 - 10 * o["avg_fill_price"] - fee)
    st.quotes["TEST:ABC"] = {"price": 110.0, "status": "REALTIME", "bid": 109.98, "ask": 110.02}
    s = st.gateway.submit(order(st, side="sell", qty=10), actor="ui")["order"]
    assert s["status"] == "filled"
    summ = st.portfolio.summary(st.account["id"])
    assert summ["open_positions"] == 0
    expected = (s["avg_fill_price"] - o["avg_fill_price"]) * 10 - fee - 10 * s["avg_fill_price"] * 0.001
    assert summ["realized_pnl"] == pytest.approx(expected)
    assert summ["label"] == "PAPER"


def test_limit_order_rests_then_fills_on_price(st):
    r = st.gateway.submit(order(st, qty=5, type=OrderType.LIMIT, limit_price=98.0, stop_loss=93), actor="ui")
    assert r["order"]["status"] == "accepted"
    changed = st.gateway.on_price("TEST:ABC", 98.5, "REALTIME")
    assert changed == []
    changed = st.gateway.on_price("TEST:ABC", 97.9, "REALTIME")
    assert changed[0]["status"] == "filled" and changed[0]["avg_fill_price"] == 98.0  # no price improvement assumed


def test_stop_order_triggers(st):
    st.gateway.submit(order(st, qty=5, stop_loss=90), actor="ui")
    r = st.gateway.submit(order(st, side="sell", qty=5, type=OrderType.STOP, stop_price=96.0), actor="ui")
    assert r["order"]["status"] == "accepted"
    out = st.gateway.on_price("TEST:ABC", 95.5, "REALTIME")
    assert out and out[0]["status"] == "filled" and out[0]["avg_fill_price"] < 95.5


def test_cancel_and_cancel_all(st):
    a = st.gateway.submit(order(st, qty=1, type=OrderType.LIMIT, limit_price=97.0), actor="ui")["order"]
    st.gateway.submit(order(st, qty=1, type=OrderType.LIMIT, limit_price=96.0), actor="ui")
    assert st.gateway.cancel(a["id"], actor="ui")["status"] == "cancelled"
    assert st.gateway.cancel_all(actor="ui") == 1
    with pytest.raises(NotFound):
        st.gateway.cancel(9999, actor="ui")


# ------------------------------------------------------------------ idempotency
def test_duplicate_client_order_id_never_double_fills(st):
    r1 = st.gateway.submit(order(st, qty=2, client_order_id="bot1:123:entry"), actor="bot:1")
    r2 = st.gateway.submit(order(st, qty=2, client_order_id="bot1:123:entry"), actor="bot:1")
    assert r2["duplicate"] is True and r2["order"]["id"] == r1["order"]["id"]
    assert st.db.scalar("SELECT COUNT(*) FROM fills") == 1


# ------------------------------------------------------------------ risk rules
def test_rejections_are_recorded(st):
    r = st.gateway.submit(order(st, qty=1000), actor="ui")  # 100k notional on a 10k account
    assert r["order"]["status"] == "rejected"
    reasons = r["decision"]["reasons"]
    assert any("order value" in x for x in reasons) and any("cash" in x for x in reasons)
    ev = st.risk.events()[0]
    assert ev["decision"] == "rejected" and ev["order_payload"]["qty"] == 1000


def test_risk_per_trade_uses_stop_distance(st):
    ok = st.gateway.submit(order(st, qty=20, stop_loss=99), actor="ui")      # risk 20*~1 = ~0.2%
    assert ok["order"]["status"] == "filled"
    bad = st.gateway.submit(order(st, qty=10, stop_loss=70), actor="ui")     # risk 10*30 = 3%
    assert bad["order"]["status"] == "rejected"
    assert any("risk" in x for x in bad["decision"]["reasons"])


def test_require_stop_and_wrong_side_stop(st):
    st.risk.set_limits(st.account["id"], {"require_stop_loss": True}, actor="ui")
    assert st.gateway.submit(order(st, qty=1), actor="ui")["order"]["status"] == "rejected"
    r = st.gateway.submit(order(st, qty=1, stop_loss=105), actor="ui")
    assert any("wrong side" in x for x in r["decision"]["reasons"])


def test_no_short_by_default(st):
    r = st.gateway.submit(order(st, side="sell", qty=1, stop_loss=105), actor="ui")
    assert r["order"]["status"] == "rejected" and any("short" in x for x in r["decision"]["reasons"])


def test_price_band_for_limits(st):
    r = st.gateway.submit(order(st, qty=1, type=OrderType.LIMIT, limit_price=50.0), actor="ui")
    assert any("from the last price" in x for x in r["decision"]["reasons"])


def test_layers_limited_and_volatility_cap(st):
    st.risk.set_limits(st.account["id"], {"max_layers_per_position": 2, "max_instrument_exposure_pct": 100,
                                          "max_order_value_pct": 100, "max_risk_per_trade_pct": 50,
                                          "max_position_risk_pct": 100}, actor="ui")
    for _ in range(2):
        assert st.gateway.submit(order(st, qty=1, stop_loss=95), actor="ui")["order"]["status"] == "filled"
    third = st.gateway.submit(order(st, qty=1, stop_loss=95), actor="ui")
    assert third["order"]["status"] == "rejected" and any("layer" in x for x in third["decision"]["reasons"])
    # volatility: ATR 8% of price lowers the ceiling to high_volatility_max_layers (here 1)
    st.risk.set_limits(st.account["id"], {"max_layers_per_position": 2, "high_volatility_max_layers": 1,
                                          "max_instrument_exposure_pct": 100, "max_order_value_pct": 100,
                                          "max_risk_per_trade_pct": 50, "max_position_risk_pct": 100}, actor="ui")
    eng = RiskEngine(st.db)
    snap = AccountSnapshot(st.account["id"], "paper", 10_000, 9_000, {"TEST:ABC": PositionView(1, 100, 1)}, {"TEST:ABC": 100})
    d = eng.check(order(st, qty=1, stop_loss=95), snap, MarketContext(100, "REALTIME", atr=8.0))
    assert not d.approved and any("limit 1" in x for x in d.reasons)


def test_order_rate_limit(st):
    st.risk.set_limits(st.account["id"], {"max_orders_per_minute": 3}, actor="ui")
    statuses = [st.gateway.submit(order(st, qty=0.1, stop_loss=99), actor="ui")["order"]["status"] for _ in range(5)]
    assert statuses[:3] == ["filled"] * 3 and statuses[3] == "rejected"


def test_live_account_blocked_without_adapter_and_setting(st):
    live = st.portfolio.create_account("Live", 1000, kind="live", broker="examplebroker")
    r = st.gateway.submit(OrderRequest(account_id=live["id"], instrument_id="TEST:ABC", side=Side.BUY, qty=1), actor="ui")
    assert r["order"]["status"] == "rejected"
    st.db.set_setting("trading.live_enabled", True)
    r2 = st.gateway.submit(OrderRequest(account_id=live["id"], instrument_id="TEST:ABC", side=Side.BUY, qty=1), actor="ui")
    assert r2["order"]["status"] == "rejected" and "adapter" in r2["order"]["reason"]


def test_no_price_means_no_order(st):
    def broken(iid):
        raise RuntimeError("feed down")
    st.gateway.quote_fn = broken
    r = st.gateway.submit(order(st, qty=1), actor="ui")
    assert r["order"]["status"] == "rejected" and "no market price" in r["order"]["reason"]


# ------------------------------------------------------------------ circuit breakers and kill switch
def test_daily_loss_circuit_breaker_halts_and_needs_a_person(st):
    st.risk.set_limits(st.account["id"], {"max_daily_loss_pct": 2.0, "max_order_value_pct": 100,
                                          "max_instrument_exposure_pct": 100, "max_risk_per_trade_pct": 50}, actor="ui")
    st.gateway.submit(order(st, qty=50, stop_loss=60), actor="ui")
    st.quotes["TEST:ABC"] = {"price": 80.0, "status": "REALTIME"}
    st.prices.update("TEST:ABC", 80.0, "REALTIME")
    state = st.risk.monitor(st.account["id"], st.portfolio.summary(st.account["id"])["equity"])
    assert state["halted"] and "daily loss" in state["breach"]
    # automation is blocked completely
    r = st.gateway.submit(order(st, side="sell", qty=10, origin="bot:1"), actor="bot:1")
    assert r["order"]["status"] == "rejected" and any("halted" in x for x in r["decision"]["reasons"])
    # a person may still reduce risk
    r2 = st.gateway.submit(order(st, side="sell", qty=10), actor="ui")
    assert r2["order"]["status"] == "filled"
    # but not add to it
    assert st.gateway.submit(order(st, qty=1, stop_loss=79), actor="ui")["order"]["status"] == "rejected"
    with pytest.raises(ValidationError):
        st.risk.resume(st.account["id"], actor="ai")
    st.risk.resume(st.account["id"], actor="ui")
    assert not st.risk.is_halted(st.account["id"])[0]


def test_global_emergency_stop(st):
    st.risk.set_global_halt(True, "test", actor="ui")
    assert st.gateway.submit(order(st, qty=1, stop_loss=99), actor="ui")["order"]["status"] == "rejected"
    with pytest.raises(ValidationError):
        st.risk.set_global_halt(False, "", actor="bot:1")
    st.risk.set_global_halt(False, "", actor="ui")
    assert st.gateway.submit(order(st, qty=1, stop_loss=99), actor="ui")["order"]["status"] == "filled"


def test_flatten_closes_everything(st):
    st.gateway.submit(order(st, qty=3, stop_loss=95), actor="ui")
    out = st.gateway.flatten(st.account["id"], actor="ui")
    assert all(r["order"]["status"] == "filled" for r in out)
    assert st.portfolio.positions(st.account["id"]) == []


def test_limits_validation():
    with pytest.raises(ValidationError):
        RiskLimits.from_dict({"max_daily_loss_pct": 0})
    with pytest.raises(ValidationError):
        RiskLimits.from_dict({"not_a_limit": 1})
    with pytest.raises(ValidationError):
        RiskLimits.from_dict({"max_layers_per_position": 0})


# ------------------------------------------------------------------ proposals (AI path)
def test_ai_proposals_need_human_approval(st):
    p = st.gateway.propose(order(st, qty=1, stop_loss=99, origin="ai"), rationale="test", origin="ai")
    assert p["status"] == "pending" and st.db.scalar("SELECT COUNT(*) FROM orders") == 0
    with pytest.raises(ValidationError):
        st.gateway.decide_proposal(p["id"], True, actor="ai")
    out = st.gateway.decide_proposal(p["id"], True, actor="ui")
    assert out["status"] == "approved" and out["order"]["origin"] == "ai"  # still checked as an AI-originated order
    with pytest.raises(ValidationError):
        st.gateway.decide_proposal(p["id"], True, actor="ui")


# ------------------------------------------------------------------ reconciliation and portfolio
def test_reconcile_cancels_unconfirmed_new_orders(st):
    st.db.execute("INSERT INTO orders(client_order_id, account_id, instrument_id, side, type, qty, status, origin, "
                  "created_at, updated_at) VALUES ('x', ?, 'TEST:ABC', 'buy', 'market', 1, 'new', 'bot:1', 1, 1)",
                  (st.account["id"],))
    out = st.gateway.reconcile()
    assert out["cancelled_unconfirmed"] == 1
    assert st.db.scalar("SELECT status FROM orders WHERE client_order_id = 'x'") == "cancelled"


def test_portfolio_summary_exposure_and_export(st):
    st.gateway.submit(order(st, qty=10, stop_loss=95), actor="ui")
    s = st.portfolio.summary(st.account["id"])
    assert s["exposure_pct"] > 0 and s["concentration_hhi"] == pytest.approx(1.0)
    csv_text = st.portfolio.export_fills_csv(st.account["id"])
    assert "TEST:ABC" in csv_text and "PAPER" in csv_text
    j = st.portfolio.add_journal("Entry idea", "trend", tags=["trend", " "])
    assert j["tags"] == '["trend"]'
    assert st.portfolio.journal(tag="trend")[0]["title"] == "Entry idea"


def test_reset_paper_account(st):
    st.gateway.submit(order(st, qty=10, stop_loss=95), actor="ui")
    st.portfolio.reset_account(st.account["id"], actor="ui")
    assert st.portfolio.account(st.account["id"])["cash"] == 10_000 and st.portfolio.positions(st.account["id"]) == []
