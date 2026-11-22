"""Adaptive layered entries: planning, all-or-nothing basket risk, the layered bot, and scenario simulations."""

from __future__ import annotations

import numpy as np
import pytest

from helpers import make_stack
from omos.bots import layered
from omos.bots.simulate import SCENARIOS, simulate
from omos.execution.models import OrderRequest, OrderType, Side
from omos.risk.engine import MarketContext, RiskLimits

BASE = {"spacing": 1.0, "max_depth": 4.0, "stop": 1.0, "take_profit": 1.5, "basket_risk_pct": 1.0}


def cfg(**kw):
    return layered.normalise({**BASE, **kw})


# ------------------------------------------------------------------------------------------- planning
def test_plan_spends_the_risk_budget_and_follows_volatility():
    calm = layered.plan(cfg(), price=100, atr=1.0, equity=10_000, ceiling=12, max_notional=1e9)
    wild = layered.plan(cfg(), price=100, atr=4.0, equity=10_000, ceiling=12, max_notional=1e9)
    for p in (calm, wild):
        assert len(p.layers) == 5 and p.risk == pytest.approx(100, rel=1e-3)  # 1% of 10,000
        assert p.layers[0]["type"] == "market" and all(lyr["type"] == "limit" for lyr in p.layers[1:])
        prices = [lyr["price"] for lyr in p.layers]
        assert prices == sorted(prices, reverse=True) and p.stop < prices[-1]
    # same money at risk, but a wilder market gets a wider ladder and smaller positions
    assert wild.layers[-1]["price"] < calm.layers[-1]["price"]
    assert sum(lyr["qty"] for lyr in wild.layers) < sum(lyr["qty"] for lyr in calm.layers)


def test_layer_count_is_adaptive_not_fixed():
    deep = layered.plan(cfg(max_depth=10), price=100, atr=1.0, equity=10_000, ceiling=50, max_notional=1e9)
    assert len(deep.layers) == 11                         # depth / spacing decides, not a constant
    capped = layered.plan(cfg(max_depth=10), price=100, atr=1.0, equity=10_000, ceiling=4, max_notional=1e9)
    assert len(capped.layers) == 4 and "ceiling" in capped.why
    own = layered.plan(cfg(max_depth=10, max_layers=2), price=100, atr=1.0, equity=10_000, ceiling=50, max_notional=1e9)
    assert len(own.layers) == 2
    tiny = layered.plan(cfg(max_depth=10, min_layer_value=400), price=100, atr=1.0, equity=10_000, ceiling=50,
                        max_notional=1e9)
    assert 1 <= len(tiny.layers) < 11 and min(lyr["qty"] * lyr["price"] for lyr in tiny.layers) >= 400
    none = layered.plan(cfg(min_layer_value=1e6), price=100, atr=1.0, equity=10_000, ceiling=50, max_notional=1e9)
    assert not none.layers and "too small" in none.why


def test_plan_respects_exposure_caps_growth_and_shorts():
    capped = layered.plan(cfg(stop=0.1), price=100, atr=0.2, equity=10_000, ceiling=12, max_notional=2_000)
    assert sum(lyr["qty"] * lyr["price"] for lyr in capped.layers) <= 2_000 * 1.0001 and capped.risk < 100
    grow = layered.plan(cfg(size_growth=1.5), price=100, atr=1.0, equity=10_000, ceiling=12, max_notional=1e9)
    q = [lyr["qty"] for lyr in grow.layers]
    assert all(b > a for a, b in zip(q, q[1:]))
    short = layered.plan(cfg(direction="short"), price=100, atr=1.0, equity=10_000, ceiling=12, max_notional=1e9)
    assert short.stop > max(lyr["price"] for lyr in short.layers)
    assert not layered.plan(cfg(), price=100, atr=None, equity=10_000, ceiling=12, max_notional=1e9).layers


def test_config_validation():
    with pytest.raises(Exception):
        layered.normalise({"direction": "sideways"})
    with pytest.raises(Exception):
        layered.normalise({"basket_risk_pct": 50})
    with pytest.raises(Exception):
        layered.normalise({"size_growth": 3})


# ------------------------------------------------------------------------------------------- basket risk
def _basket(acct_id, n=4, qty=10.0, price=100.0, step=1.0, stop=94.0):
    return [OrderRequest(account_id=acct_id, instrument_id="TEST:ABC", side=Side.BUY, qty=qty,
                         type=OrderType.MARKET if i == 0 else OrderType.LIMIT,
                         limit_price=None if i == 0 else price - i * step, stop_loss=stop, origin="bot:1")
            for i in range(n)]


def test_whole_basket_is_judged_together(db):
    s = make_stack(db)
    acct = s.portfolio.create_account("L", 10_000.0)
    s.risk.set_limits(acct["id"], {"max_risk_per_trade_pct": 2.0, "max_position_risk_pct": 3.0}, actor="t")
    snap = s.portfolio.snapshot(acct["id"])
    mkt = MarketContext(price=100.0, price_status="REALTIME", atr=1.0)
    ok = s.risk.check_basket(_basket(acct["id"], n=4, qty=5), snap, mkt)
    assert ok.approved, ok.reasons
    # each layer risks at most 0.6% (fine alone), but all 8 together risk 3.4% > 3%: the whole basket is refused
    big = s.risk.check_basket(_basket(acct["id"], n=8, qty=5, stop=88), snap, mkt)
    assert not big.approved and any("if every layer fills and the stop is hit" in r for r in big.reasons)
    assert all(c["passed"] for c in big.checks if c["rule"] == "risk_per_trade")
    over = s.risk.check_basket(_basket(acct["id"], n=13, qty=0.5), snap, mkt)
    assert not over.approved and any("safety ceiling of 12" in r for r in over.reasons)
    hot = MarketContext(price=100.0, price_status="REALTIME", atr=8.0)  # ATR 8% > 5%: ceiling drops to 4
    assert not s.risk.check_basket(_basket(acct["id"], n=5, qty=1), snap, hot).approved
    poor = s.risk.check_basket(_basket(acct["id"], n=4, qty=30, stop=99.5), snap, mkt)
    assert not poor.approved and any("cash" in r or "exposure" in r for r in poor.reasons)
    far = _basket(acct["id"], n=3, qty=1, step=20, stop=50)
    assert any("layer 3" in r and "from the last price" in r for r in s.risk.check_basket(far, snap, mkt).reasons)


def test_rejected_basket_places_nothing_and_replays_are_idempotent(db):
    s = make_stack(db)
    acct = s.portfolio.create_account("L", 10_000.0)
    s.risk.set_limits(acct["id"], {"max_position_risk_pct": 1.0}, actor="t")
    s.gateway.on_price("TEST:ABC", 110.0, "REALTIME")
    bad = s.gateway.submit_basket(_basket(acct["id"], n=6, qty=5, price=110, stop=100), actor="t", basket_id="b1")
    assert not bad["approved"] and len(bad["orders"]) == 6
    assert {o["status"] for o in bad["orders"]} == {"rejected"}
    assert len({o["risk_decision_id"] for o in bad["orders"]}) == 1  # one decision for the whole basket
    assert not s.portfolio.positions(acct["id"])
    good = s.gateway.submit_basket(_basket(acct["id"], n=3, qty=2, price=110, stop=106), actor="t", basket_id="b2")
    assert good["approved"] and [o["status"] for o in good["orders"]][0] == "filled"
    assert [o["status"] for o in good["orders"]][1:] == ["accepted", "accepted"]
    again = s.gateway.submit_basket(_basket(acct["id"], n=3, qty=2, price=110, stop=106), actor="t", basket_id="b2")
    assert again["duplicate"] and len(again["orders"]) == 3
    assert db.scalar("SELECT COUNT(*) FROM orders WHERE client_order_id LIKE 'b2:%'") == 3


def test_broker_failure_mid_basket_takes_back_placed_layers(db):
    s = make_stack(db)
    acct = s.portfolio.create_account("L", 10_000.0)
    s.gateway.on_price("TEST:ABC", 110.0, "REALTIME")
    real = s.gateway.paper.place
    calls = {"n": 0}

    def flaky(order, price, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("connection reset")
        return real(order, price, **kw)

    s.gateway.paper.place = flaky
    r = s.gateway.submit_basket([OrderRequest(account_id=acct["id"], instrument_id="TEST:ABC", side=Side.BUY, qty=1,
                                              type=OrderType.LIMIT, limit_price=109 - i, stop_loss=100, origin="bot:1")
                                 for i in range(4)], actor="t", basket_id="b3")
    assert r["aborted"] == "connection reset"
    statuses = sorted(o["status"] for o in r["orders"])
    assert statuses == ["cancelled", "cancelled", "rejected", "rejected"]  # nothing left resting
    # a single order the broker cannot take is rejected, not left looking 'new'
    s.gateway.paper.place = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline"))
    one = s.gateway.submit(OrderRequest(account_id=acct["id"], instrument_id="TEST:ABC", side=Side.BUY, qty=1),
                           actor="t")
    assert one["order"]["status"] == "rejected" and "broker error: offline" in one["order"]["reason"]


# ------------------------------------------------------------------------------------------- the bot
def test_layered_bot_places_manages_and_closes_a_basket(db):
    closes = np.r_[np.full(200, 100.0) + np.sin(np.arange(200) / 3.0), np.linspace(100, 101, 5)]
    s = make_stack(db, closes=closes)
    acct = s.portfolio.create_account("L", 10_000.0)
    b = s.bots.create("ladder", "layered", "TEST:ABC", "1m", account_id=acct["id"], config={**BASE, "cooldown_bars": 0})
    s.runner.clock = lambda: int(s.provider.series["ABC"][-1][0]) + 2 * 60_000  # the test data's own time
    s.bots.validate(b["id"])
    s.bots.start(b["id"])
    s.runner.tick()
    rt = s.bots.get(b["id"])["runtime"]
    assert rt["basket"]["planned_layers"] >= 2 and rt["stats"]["baskets"] == 1
    assert s.portfolio.positions(acct["id"])[0]["qty"] > 0
    # price rises through the take-profit: everything is closed and unfilled layers cancelled
    for px in np.linspace(101, 115, 15):
        s.provider.append("ABC", float(px))
        s.gateway.on_price("TEST:ABC", float(px), "REALTIME")
        s.runner.tick()
        if not s.bots.get(b["id"])["runtime"].get("basket"):
            break
    rt = s.bots.get(b["id"])["runtime"]
    assert "basket" not in rt and rt["stats"].get("take_profit") == 1
    assert not [o for o in s.portfolio.orders(acct["id"], open_only=True)]
    assert not s.portfolio.positions(acct["id"]) or abs(s.portfolio.positions(acct["id"])[0]["qty"]) < 1e-9


# ------------------------------------------------------------------------------------------- scenarios
@pytest.fixture(scope="module")
def sims():
    bot = {"kind": "layered", "timeframe": "1h",
           "config": layered.normalise({**BASE, "trail_activate": 1.0, "trail_distance": 1.0})}
    res = simulate(bot, equity=10_000, limits=RiskLimits().to_dict(), bars=120)
    return {r["scenario"]: r for r in res["results"]}, res


def test_every_scenario_runs_without_duplicates(sims):
    by, res = sims
    assert set(by) == set(SCENARIOS) and res["summary"]["errors"] == 0
    assert all(r["duplicate_orders"] == 0 for r in by.values())
    assert res["status"] == "SIMULATED" and "gap" in res["note"].lower()


def test_scenario_outcomes_match_the_safety_design(sims):
    by, _ = sims
    assert by["crash"]["stops"] >= 1 and by["crash"]["take_profits"] == 0
    # losing baskets stay near the 1% budget (fees and slippage come on top) when stops fill at their price
    for name in ("crash", "high_volatility", "sideways", "dip_and_recover"):
        assert by[name]["worst_basket_loss_pct"] >= -1.5, name
    assert by["gap_down"]["worst_basket_loss_pct"] < -1.5        # a gap jumps over the stop: reported, not hidden
    assert by["thin_market"]["fills"] > by["thin_market"]["orders"] * 0.9  # partial fills happened
    assert by["data_outage"]["final_state"] == "running"          # resumed by itself after the outage
    restart, base = by["restart"], by["dip_and_recover"]
    assert (restart["orders"], restart["fills"], restart["return_pct"]) == (base["orders"], base["fills"], base["return_pct"])
    assert by["rejected_orders"]["final_state"] == "error"        # repeated broker failures stop the bot
    assert by["spread_blowout"]["baskets"] < base["baskets"]      # no new baskets while the spread is wide


def test_basket_over_the_account_limit_is_always_rejected_whole():
    bot = {"kind": "layered", "timeframe": "1h", "config": layered.normalise(BASE)}
    # every layer is worth more than 1% of equity, so each planned basket breaks a limit and is refused whole
    r = simulate(bot, equity=10_000, limits={**RiskLimits().to_dict(), "max_order_value_pct": 1.0},
                 scenarios=["dip_and_recover"], bars=60)["results"][0]
    assert "error" not in r, r
    assert r["baskets"] == 0 and r["baskets_rejected"] >= 1 and r["fills"] == 0


def test_validation_refuses_a_budget_the_account_cannot_take(db):
    s = make_stack(db)
    acct = s.portfolio.create_account("L", 10_000.0)
    b = s.bots.create("too big", "layered", "TEST:ABC", "1m", account_id=acct["id"], config={**BASE, "basket_risk_pct": 6})
    with pytest.raises(Exception, match="every basket would be rejected"):
        s.bots.validate(b["id"])


def test_circuit_breaker_keeps_protective_exits_working(db):
    """After a halt, a bot can no longer add risk, but its stop must still close the position."""
    from omos.execution.models import OrderRequest, Side
    s = make_stack(db, closes=np.full(250, 100.0) + np.sin(np.arange(250) / 3.0))
    acct = s.portfolio.create_account("L", 10_000.0)
    s.gateway.on_price("TEST:ABC", 100.0, "REALTIME")
    s.gateway.submit(OrderRequest(account_id=acct["id"], instrument_id="TEST:ABC", side=Side.BUY, qty=10), actor="ui")
    s.risk.halt(acct["id"], "daily loss limit", actor="risk_engine")
    add = s.gateway.submit(OrderRequest(account_id=acct["id"], instrument_id="TEST:ABC", side=Side.BUY, qty=1,
                                        origin="bot:1"), actor="bot:1")
    assert add["order"]["status"] == "rejected"
    close = s.gateway.submit(OrderRequest(account_id=acct["id"], instrument_id="TEST:ABC", side=Side.SELL, qty=10,
                                          origin="bot:1", reduce_only=True), actor="bot:1")
    assert close["order"]["status"] == "filled"
