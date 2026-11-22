"""Central risk engine. Independent of strategies, bots and AI; nothing places an order without it.

``RiskEngine.check()`` is a pure decision over (order, account snapshot, market context, limits,
recent activity). The gateway records every decision in ``risk_events``. ``RiskEngine.monitor()``
runs after fills and on a timer, and raises the account's circuit breaker (``trading_halted``)
when a loss or drawdown limit is breached. Only a person can clear a halt.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from omos.errors import ValidationError
from omos.execution.models import OrderRequest, OrderType, Side
from omos.storage.db import Database
from omos.util import dumps, loads, new_id, now_ms, utc_day, utc_week


@dataclass
class RiskLimits:
    max_risk_per_trade_pct: float = 2.0        # equity at risk between entry and stop
    max_position_risk_pct: float = 4.0         # aggregate risk of all layers of one position
    require_stop_loss: bool = False
    assumed_stop_pct: float = 10.0             # risk assumed when an order carries no stop
    max_daily_loss_pct: float = 5.0
    max_weekly_loss_pct: float = 10.0
    max_drawdown_pct: float = 20.0
    max_total_exposure_pct: float = 100.0      # gross notional / equity
    max_instrument_exposure_pct: float = 50.0
    max_leverage: float = 1.0
    max_open_positions: int = 10
    max_order_qty: float = 0.0                 # 0 = no quantity cap
    max_order_value_pct: float = 50.0          # single order notional / equity
    max_orders_per_minute: int = 20
    max_trades_per_day: int = 100
    max_simultaneous_bots: int = 5
    # Layering has no fixed strategy limit: layered bots size their ladders from a risk budget and the
    # current volatility. These two are safety ceilings only, never targets.
    max_layers_per_position: int = 12          # safety ceiling on layers in one position
    high_volatility_atr_pct: float = 5.0       # above this ATR% of price ...
    high_volatility_max_layers: int = 4        # ... the ceiling drops to this
    max_layer_distance_pct: float = 30.0       # deepest layer of a basket, % from the last price
    max_price_deviation_pct: float = 5.0       # limit/stop price sanity band around the last price
    allow_short: bool = False
    trading_hours_only: bool = False
    allowed_instruments: list[str] = field(default_factory=list)
    blocked_instruments: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> RiskLimits:
        lim = cls()
        for k, v in (d or {}).items():
            if k not in cls.__dataclass_fields__:
                raise ValidationError(f"unknown risk limit '{k}'")
            setattr(lim, k, v)
        lim.validate()
        return lim

    def validate(self) -> None:
        pct = ["max_risk_per_trade_pct", "max_position_risk_pct", "assumed_stop_pct", "max_daily_loss_pct",
               "max_weekly_loss_pct", "max_drawdown_pct", "max_order_value_pct", "max_price_deviation_pct",
               "high_volatility_atr_pct", "max_layer_distance_pct"]
        for k in pct:
            v = float(getattr(self, k))
            if not 0 < v <= 100:
                raise ValidationError(f"{k} must be in (0, 100]")
        for k in ("max_total_exposure_pct", "max_instrument_exposure_pct"):
            if not 0 < float(getattr(self, k)) <= 10_000:
                raise ValidationError(f"{k} must be in (0, 10000]")
        if not 0 < float(self.max_leverage) <= 100:
            raise ValidationError("max_leverage must be in (0, 100]")
        for k in ("max_open_positions", "max_orders_per_minute", "max_trades_per_day", "max_simultaneous_bots",
                  "max_layers_per_position", "high_volatility_max_layers"):
            if int(getattr(self, k)) < 1:
                raise ValidationError(f"{k} must be at least 1")
        if float(self.max_order_qty) < 0:
            raise ValidationError("max_order_qty cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PositionView:
    qty: float                 # signed
    avg_price: float
    layers: int = 0


@dataclass
class AccountSnapshot:
    account_id: int
    kind: str                  # paper | live
    equity: float
    cash: float
    positions: dict[str, PositionView]
    prices: dict[str, float]

    def exposure(self, instrument_id: str | None = None) -> float:
        total = 0.0
        for iid, p in self.positions.items():
            if instrument_id and iid != instrument_id:
                continue
            total += abs(p.qty) * self.prices.get(iid, p.avg_price)
        return total


@dataclass
class MarketContext:
    price: float               # last trade / quote price used to validate the order
    price_status: str          # data status label of that price
    session_open: bool = True
    atr: float | None = None   # recent volatility for layering checks


@dataclass
class RiskDecision:
    approved: bool
    decision_id: str
    reasons: list[str]
    checks: list[dict[str, Any]]
    notional: float
    risk_amount: float
    adjusted_qty: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RiskEngine:
    def __init__(self, db: Database):
        self.db = db

    # -- configuration ---------------------------------------------------------------
    def limits(self, account_id: int) -> RiskLimits:
        return RiskLimits.from_dict(self.db.get_setting(f"risk.limits.{account_id}") or self.db.get_setting("risk.limits.default"))

    def set_limits(self, account_id: int | None, limits: dict[str, Any], actor: str) -> RiskLimits:
        lim = RiskLimits.from_dict(limits)
        key = f"risk.limits.{account_id}" if account_id else "risk.limits.default"
        self.db.set_setting(key, lim.to_dict())
        self.db.audit(actor, "risk.limits.update", key, lim.to_dict())
        return lim

    def global_halt(self) -> dict[str, Any]:
        return self.db.get_setting("risk.global_halt", {"halted": False})

    def state(self, account_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM risk_state WHERE account_id = ?", (account_id,))
        return row or {"account_id": account_id, "trading_halted": 0, "halt_reason": None}

    def is_halted(self, account_id: int) -> tuple[bool, str]:
        g = self.global_halt()
        if g.get("halted"):
            return True, f"emergency stop is active ({g.get('reason', 'no reason given')})"
        st = self.state(account_id)
        if st.get("trading_halted"):
            return True, f"circuit breaker tripped: {st.get('halt_reason')}"
        return False, ""

    # -- the decision -------------------------------------------------------------------
    def check(self, req: OrderRequest, acct: AccountSnapshot, mkt: MarketContext, *,
              live_enabled: bool = False, active_bots: int = 0, starting_bot: bool = False) -> RiskDecision:
        lim = self.limits(acct.account_id)
        checks: list[dict[str, Any]] = []
        reasons: list[str] = []

        def rule(name: str, ok: bool, msg: str, **detail: Any) -> None:
            checks.append({"rule": name, "passed": ok, "message": msg, **detail})
            if not ok:
                reasons.append(msg)

        pos = acct.positions.get(req.instrument_id, PositionView(0.0, 0.0, 0))
        signed = req.qty * req.side.sign
        reduces = pos.qty != 0 and (pos.qty > 0) != (signed > 0) and abs(signed) <= abs(pos.qty) + 1e-12
        is_human = req.origin.split(":")[0] in ("ui", "emergency")

        halted, why = self.is_halted(acct.account_id)
        if halted:
            # While halted nobody may ADD risk. A person may reduce or close; automation only through reduce-only
            # orders (a bot's protective stop must still work after a circuit breaker trips).
            may = reduces and (is_human or req.reduce_only)
            rule("halt", may, f"trading halted: {why}" + ("" if may else
                 " (only orders that reduce or close a position are allowed)"))
        if acct.kind == "live":
            rule("live_enabled", live_enabled, "live trading is disabled (Settings > Trading > Live trading)")
        if req.reduce_only and not reduces:
            rule("reduce_only", False, "reduce-only order would open or increase a position")
        if lim.allowed_instruments and req.instrument_id not in lim.allowed_instruments:
            rule("allowed_instruments", False, f"{req.instrument_id} is not on the allowed-instruments list")
        if req.instrument_id in lim.blocked_instruments:
            rule("blocked_instruments", False, f"{req.instrument_id} is blocked")

        price = mkt.price
        if not (isinstance(price, int | float) and math.isfinite(price) and price > 0):
            rule("price_available", False, "no valid market price to check this order against")
            return self._decide(checks, reasons, 0.0, 0.0)
        if mkt.price_status in ("SIMULATED",) and acct.kind == "live":
            rule("price_real", False, "a live order cannot be validated against simulated prices")
        if lim.trading_hours_only and not mkt.session_open:
            rule("session_open", False, "market session is closed")

        for name, p in (("limit_price", req.limit_price), ("stop_price", req.stop_price)):
            if p is not None:
                dev = abs(p / price - 1) * 100
                rule(f"{name}_band", dev <= lim.max_price_deviation_pct,
                     f"{name} {p:g} is {dev:.1f}% from the last price {price:g} (limit {lim.max_price_deviation_pct}%)",
                     deviation_pct=dev)

        exec_price = req.limit_price if req.type in (OrderType.LIMIT, OrderType.STOP_LIMIT) else (
            req.stop_price if req.type == OrderType.STOP else price)
        notional = req.qty * float(exec_price)
        equity = acct.equity
        if equity <= 0:
            rule("equity_positive", reduces, "account equity is zero or negative")
            return self._decide(checks, reasons, notional, 0.0)

        if lim.max_order_qty and not reduces:
            rule("max_order_qty", req.qty <= lim.max_order_qty, f"quantity {req.qty:g} exceeds the cap {lim.max_order_qty:g}")
        if not reduces:
            pct = notional / equity * 100
            rule("max_order_value", pct <= lim.max_order_value_pct,
                 f"order value is {pct:.1f}% of equity (limit {lim.max_order_value_pct}%)", value_pct=pct)

        # shorting
        new_qty = pos.qty + signed
        if new_qty < -1e-12 and not lim.allow_short:
            rule("no_short", False, "this order would open a short position, and shorting is disabled")

        if not reduces:
            # exposure after the order
            gross_after = acct.exposure() - abs(pos.qty) * price + abs(new_qty) * price
            inst_after = abs(new_qty) * price
            rule("total_exposure", gross_after / equity * 100 <= lim.max_total_exposure_pct,
                 f"total exposure would be {gross_after / equity * 100:.1f}% of equity (limit {lim.max_total_exposure_pct}%)")
            rule("instrument_exposure", inst_after / equity * 100 <= lim.max_instrument_exposure_pct,
                 f"{req.instrument_id} exposure would be {inst_after / equity * 100:.1f}% of equity "
                 f"(limit {lim.max_instrument_exposure_pct}%)")
            rule("leverage", gross_after / equity <= lim.max_leverage + 1e-9,
                 f"leverage would be {gross_after / equity:.2f}x (limit {lim.max_leverage}x)")
            if lim.max_leverage <= 1.0 and req.side == Side.BUY:
                need = notional * 1.005  # headroom for fees and slippage
                rule("cash", need <= acct.cash + 1e-6, f"order needs about {need:,.2f} but only {acct.cash:,.2f} cash is free")
            opens_new = pos.qty == 0
            open_count = sum(1 for p in acct.positions.values() if abs(p.qty) > 1e-12)
            if opens_new:
                rule("max_open_positions", open_count < lim.max_open_positions,
                     f"{open_count} positions open (limit {lim.max_open_positions})")
            else:
                allowed_layers = self.layer_ceiling(lim, mkt)
                if allowed_layers < lim.max_layers_per_position:
                    checks.append({"rule": "volatility_layer_cap", "passed": True,
                                   "message": f"high volatility: layer ceiling {allowed_layers} (ATR {mkt.atr / price * 100:.1f}%)"})
                rule("max_layers", pos.layers < allowed_layers,
                     f"position already has {pos.layers} layer(s) (limit {allowed_layers})")

        # risk per trade and per position
        risk_amount = 0.0
        if not reduces:
            if req.stop_loss is None:
                if lim.require_stop_loss:
                    rule("stop_required", False, "a stop-loss is required for every new position")
                stop_dist = float(exec_price) * lim.assumed_stop_pct / 100
            else:
                wrong_side = (req.side == Side.BUY and req.stop_loss >= exec_price) or (
                    req.side == Side.SELL and req.stop_loss <= exec_price)
                if wrong_side:
                    rule("stop_side", False, "stop-loss is on the wrong side of the entry price")
                stop_dist = abs(float(exec_price) - req.stop_loss)
            risk_amount = req.qty * stop_dist
            risk_pct = risk_amount / equity * 100
            rule("risk_per_trade", risk_pct <= lim.max_risk_per_trade_pct + 1e-9,
                 f"risk {risk_pct:.2f}% of equity (limit {lim.max_risk_per_trade_pct}%)", risk_pct=risk_pct)
            if pos.qty != 0 and (pos.qty > 0) == (signed > 0):
                existing_risk = abs(pos.qty) * float(exec_price) * lim.assumed_stop_pct / 100 if req.stop_loss is None \
                    else abs(pos.qty) * abs(pos.avg_price - req.stop_loss)
                agg = (existing_risk + risk_amount) / equity * 100
                rule("position_risk", agg <= lim.max_position_risk_pct + 1e-9,
                     f"position risk would be {agg:.2f}% of equity (limit {lim.max_position_risk_pct}%)")

        # activity limits
        since = now_ms() - 60_000
        recent = self.db.scalar("SELECT COUNT(*) FROM orders WHERE account_id = ? AND created_at >= ?",
                                (acct.account_id, since)) or 0
        rule("orders_per_minute", recent < lim.max_orders_per_minute,
             f"{recent} orders in the last minute (limit {lim.max_orders_per_minute})")
        day_start = now_ms() - (now_ms() % 86_400_000)
        trades_today = self.db.scalar("SELECT COUNT(*) FROM fills WHERE account_id = ? AND ts >= ?",
                                      (acct.account_id, day_start)) or 0
        rule("trades_per_day", trades_today < lim.max_trades_per_day,
             f"{trades_today} fills today (limit {lim.max_trades_per_day})")
        if starting_bot:
            rule("max_bots", active_bots < lim.max_simultaneous_bots,
                 f"{active_bots} bots running (limit {lim.max_simultaneous_bots})")
        return self._decide(checks, reasons, notional, risk_amount)

    @staticmethod
    def layer_ceiling(lim: RiskLimits, mkt: MarketContext) -> int:
        """Safety ceiling on layers in one position right now (lower when volatility is high)."""
        if mkt.atr is not None and mkt.price > 0 and mkt.atr / mkt.price * 100 > lim.high_volatility_atr_pct:
            return min(lim.max_layers_per_position, lim.high_volatility_max_layers)
        return lim.max_layers_per_position

    def check_basket(self, reqs: list[OrderRequest], acct: AccountSnapshot, mkt: MarketContext, *,
                     live_enabled: bool = False) -> RiskDecision:
        """All layers of a layered entry, judged together as if every one of them fills.

        The basket is approved only if the WHOLE of it fits every limit; otherwise nothing is placed.
        Single orders are judged by ``check``; a basket must never get through as separate orders that
        each look small.
        """
        lim = self.limits(acct.account_id)
        checks: list[dict[str, Any]] = []
        reasons: list[str] = []

        def rule(name: str, ok: bool, msg: str, **detail: Any) -> None:
            checks.append({"rule": name, "passed": ok, "message": msg, **detail})
            if not ok:
                reasons.append(msg)

        if not reqs:
            rule("basket_empty", False, "the basket has no orders")
            return self._decide(checks, reasons, 0.0, 0.0)
        r0 = reqs[0]
        if len({r.instrument_id for r in reqs}) != 1 or len({r.side for r in reqs}) != 1 or \
                len({r.account_id for r in reqs}) != 1:
            rule("basket_shape", False, "a basket adds to one position, in one direction, in one account")
            return self._decide(checks, reasons, 0.0, 0.0)
        if any(r.reduce_only for r in reqs):
            rule("basket_entries_only", False, "a basket may only contain entries, not reduce-only orders")
        side = r0.side
        halted, why = self.is_halted(acct.account_id)
        if halted:
            rule("halt", False, f"trading halted: {why}")
        if acct.kind == "live":
            rule("live_enabled", live_enabled, "live trading is disabled (Settings > Trading > Live trading)")
        if lim.allowed_instruments and r0.instrument_id not in lim.allowed_instruments:
            rule("allowed_instruments", False, f"{r0.instrument_id} is not on the allowed-instruments list")
        if r0.instrument_id in lim.blocked_instruments:
            rule("blocked_instruments", False, f"{r0.instrument_id} is blocked")
        price = mkt.price
        if not (isinstance(price, int | float) and math.isfinite(price) and price > 0):
            rule("price_available", False, "no valid market price to check this basket against")
            return self._decide(checks, reasons, 0.0, 0.0)
        if mkt.price_status in ("SIMULATED",) and acct.kind == "live":
            rule("price_real", False, "a live basket cannot be validated against simulated prices")
        if lim.trading_hours_only and not mkt.session_open:
            rule("session_open", False, "market session is closed")
        equity = acct.equity
        if equity <= 0:
            rule("equity_positive", False, "account equity is zero or negative")
            return self._decide(checks, reasons, 0.0, 0.0)

        pos = acct.positions.get(r0.instrument_id, PositionView(0.0, 0.0, 0))
        if pos.qty != 0 and (pos.qty > 0) != (side == Side.BUY):
            rule("basket_direction", False, "a basket cannot be placed against the open position")
        notional = 0.0
        basket_risk = 0.0
        for i, r in enumerate(reqs):
            px = float(r.limit_price if r.type in (OrderType.LIMIT, OrderType.STOP_LIMIT) else
                       r.stop_price if r.type == OrderType.STOP else price)
            dist = abs(px / price - 1) * 100
            if dist > lim.max_layer_distance_pct:
                rule("layer_distance", False, f"layer {i + 1} at {px:g} is {dist:.1f}% from the last price "
                     f"(limit {lim.max_layer_distance_pct}%)")
            value = r.qty * px
            notional += value
            if lim.max_order_qty and r.qty > lim.max_order_qty:
                rule("max_order_qty", False, f"layer {i + 1}: quantity {r.qty:g} exceeds the cap {lim.max_order_qty:g}")
            if value / equity * 100 > lim.max_order_value_pct:
                rule("max_order_value", False, f"layer {i + 1} is {value / equity * 100:.1f}% of equity "
                     f"(limit {lim.max_order_value_pct}%)")
            if r.stop_loss is None:
                if lim.require_stop_loss:
                    rule("stop_required", False, f"layer {i + 1} has no stop-loss")
                stop_dist = px * lim.assumed_stop_pct / 100
            else:
                if (side == Side.BUY and r.stop_loss >= px) or (side == Side.SELL and r.stop_loss <= px):
                    rule("stop_side", False, f"layer {i + 1}: stop-loss is on the wrong side of its entry price")
                stop_dist = abs(px - r.stop_loss)
            layer_risk = r.qty * stop_dist
            basket_risk += layer_risk
            if layer_risk / equity * 100 > lim.max_risk_per_trade_pct + 1e-9:
                rule("risk_per_trade", False, f"layer {i + 1} risks {layer_risk / equity * 100:.2f}% of equity "
                     f"(limit {lim.max_risk_per_trade_pct}%)")

        signed_total = sum(r.qty for r in reqs) * side.sign
        new_qty = pos.qty + signed_total
        if new_qty < -1e-12 and not lim.allow_short:
            rule("no_short", False, "this basket would open a short position, and shorting is disabled")
        gross_after = acct.exposure() - abs(pos.qty) * price + abs(new_qty) * price
        inst_after = abs(new_qty) * price
        rule("total_exposure", gross_after / equity * 100 <= lim.max_total_exposure_pct,
             f"if every layer fills, total exposure is {gross_after / equity * 100:.1f}% of equity "
             f"(limit {lim.max_total_exposure_pct}%)")
        rule("instrument_exposure", inst_after / equity * 100 <= lim.max_instrument_exposure_pct,
             f"if every layer fills, {r0.instrument_id} exposure is {inst_after / equity * 100:.1f}% of equity "
             f"(limit {lim.max_instrument_exposure_pct}%)")
        rule("leverage", gross_after / equity <= lim.max_leverage + 1e-9,
             f"if every layer fills, leverage is {gross_after / equity:.2f}x (limit {lim.max_leverage}x)")
        if lim.max_leverage <= 1.0 and side == Side.BUY:
            need = notional * 1.005
            rule("cash", need <= acct.cash + 1e-6, f"the whole basket needs about {need:,.2f} but {acct.cash:,.2f} cash is free")
        if pos.qty == 0:
            open_count = sum(1 for p in acct.positions.values() if abs(p.qty) > 1e-12)
            rule("max_open_positions", open_count < lim.max_open_positions,
                 f"{open_count} positions open (limit {lim.max_open_positions})")
        ceiling = self.layer_ceiling(lim, mkt)
        total_layers = pos.layers + len(reqs)
        rule("layer_ceiling", total_layers <= ceiling,
             f"{total_layers} layers in one position exceeds the safety ceiling of {ceiling}"
             + (" (lowered for high volatility)" if ceiling < lim.max_layers_per_position else ""))
        existing_risk = 0.0
        if pos.qty != 0:
            stops = [r.stop_loss for r in reqs if r.stop_loss is not None]
            existing_risk = abs(pos.qty) * (abs(pos.avg_price - min(stops) if side == Side.BUY else pos.avg_price - max(stops))
                                            if stops else pos.avg_price * lim.assumed_stop_pct / 100)
        agg = (existing_risk + basket_risk) / equity * 100
        rule("basket_risk", agg <= lim.max_position_risk_pct + 1e-9,
             f"if every layer fills and the stop is hit, the loss is {agg:.2f}% of equity "
             f"(limit {lim.max_position_risk_pct}%)", risk_pct=agg)
        since = now_ms() - 60_000
        recent = self.db.scalar("SELECT COUNT(*) FROM orders WHERE account_id = ? AND created_at >= ?",
                                (acct.account_id, since)) or 0
        rule("orders_per_minute", recent + len(reqs) <= lim.max_orders_per_minute,
             f"{recent} orders in the last minute plus {len(reqs)} in this basket (limit {lim.max_orders_per_minute})")
        day_start = now_ms() - (now_ms() % 86_400_000)
        trades_today = self.db.scalar("SELECT COUNT(*) FROM fills WHERE account_id = ? AND ts >= ?",
                                      (acct.account_id, day_start)) or 0
        rule("trades_per_day", trades_today + len(reqs) <= lim.max_trades_per_day,
             f"{trades_today} fills today plus up to {len(reqs)} from this basket (limit {lim.max_trades_per_day})")
        return self._decide(checks, reasons, notional, basket_risk)

    @staticmethod
    def _decide(checks, reasons, notional, risk_amount) -> RiskDecision:
        return RiskDecision(not reasons, new_id("rd_"), reasons, checks, notional, risk_amount)

    def check_bot_start(self, account_id: int, active_bots: int) -> tuple[bool, str]:
        lim = self.limits(account_id)
        halted, why = self.is_halted(account_id)
        if halted:
            return False, f"cannot start bots: {why}"
        if active_bots >= lim.max_simultaneous_bots:
            return False, f"{active_bots} bots already running (limit {lim.max_simultaneous_bots})"
        return True, ""

    # -- post-trade monitoring / circuit breakers ---------------------------------------------
    def monitor(self, account_id: int, equity: float) -> dict[str, Any]:
        lim = self.limits(account_id)
        ts = now_ms()
        day, week = utc_day(ts), utc_week(ts)
        st = self.db.query_one("SELECT * FROM risk_state WHERE account_id = ?", (account_id,))
        if st is None:
            self.db.execute(
                "INSERT INTO risk_state(account_id, trading_halted, day, day_start_equity, week, week_start_equity, "
                "peak_equity, updated_at) VALUES (?, 0, ?, ?, ?, ?, ?, ?)",
                (account_id, day, equity, week, equity, equity, ts))
            st = self.db.query_one("SELECT * FROM risk_state WHERE account_id = ?", (account_id,))
        assert st is not None
        updates: dict[str, Any] = {}
        if st["day"] != day:
            updates.update(day=day, day_start_equity=equity)
        if st["week"] != week:
            updates.update(week=week, week_start_equity=equity)
        peak = max(float(st["peak_equity"] or equity), equity)
        updates["peak_equity"] = peak
        day_start = float(updates.get("day_start_equity", st["day_start_equity"]) or equity)
        week_start = float(updates.get("week_start_equity", st["week_start_equity"]) or equity)
        day_pnl = (equity / day_start - 1) * 100 if day_start else 0.0
        week_pnl = (equity / week_start - 1) * 100 if week_start else 0.0
        dd = (1 - equity / peak) * 100 if peak else 0.0
        breach = None
        if day_pnl <= -lim.max_daily_loss_pct:
            breach = f"daily loss {day_pnl:.2f}% hit the {lim.max_daily_loss_pct}% limit"
        elif week_pnl <= -lim.max_weekly_loss_pct:
            breach = f"weekly loss {week_pnl:.2f}% hit the {lim.max_weekly_loss_pct}% limit"
        elif dd >= lim.max_drawdown_pct:
            breach = f"drawdown {dd:.2f}% hit the {lim.max_drawdown_pct}% limit"
        sets = ", ".join(f"{k} = ?" for k in updates)
        self.db.execute(f"UPDATE risk_state SET {sets}, updated_at = ? WHERE account_id = ?",  # noqa: S608 - fixed keys
                        (*updates.values(), ts, account_id))
        if breach and not st["trading_halted"]:
            self.halt(account_id, breach, actor="risk_engine")
        return {"day_pnl_pct": day_pnl, "week_pnl_pct": week_pnl, "drawdown_pct": dd, "peak_equity": peak,
                "breach": breach, "halted": bool(breach) or bool(st["trading_halted"])}

    def halt(self, account_id: int, reason: str, actor: str) -> None:
        self.db.execute(
            "INSERT INTO risk_state(account_id, trading_halted, halt_reason, halted_at, updated_at) VALUES (?, 1, ?, ?, ?) "
            "ON CONFLICT(account_id) DO UPDATE SET trading_halted = 1, halt_reason = excluded.halt_reason, "
            "halted_at = excluded.halted_at, updated_at = excluded.updated_at",
            (account_id, reason, now_ms(), now_ms()))
        self.log_event(account_id, "halt", actor, "circuit_breaker", reason)

    def resume(self, account_id: int, actor: str) -> None:
        if actor.split(":")[0] not in ("ui", "cli"):
            raise ValidationError("only a person (UI or CLI) can clear a trading halt")
        self.db.execute("UPDATE risk_state SET trading_halted = 0, halt_reason = NULL, updated_at = ? WHERE account_id = ?",
                        (now_ms(), account_id))
        self.log_event(account_id, "resume", actor, "circuit_breaker", "trading resumed by a person")

    def set_global_halt(self, halted: bool, reason: str, actor: str) -> None:
        if not halted and actor.split(":")[0] not in ("ui", "cli"):
            raise ValidationError("only a person (UI or CLI) can clear the emergency stop")
        self.db.set_setting("risk.global_halt", {"halted": halted, "reason": reason, "by": actor, "at": now_ms()})
        self.log_event(None, "kill" if halted else "resume", actor, "emergency_stop", reason)

    def log_event(self, account_id: int | None, decision: str, origin: str, rule: str, message: str,
                  payload: dict[str, Any] | None = None, decision_id: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO risk_events(ts, account_id, decision, origin, rule, message, order_payload, decision_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now_ms(), account_id, decision, origin, rule, message[:1000], dumps(payload) if payload else None, decision_id))

    def events(self, account_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if account_id:
            rows = self.db.query("SELECT * FROM risk_events WHERE account_id = ? OR account_id IS NULL ORDER BY id DESC LIMIT ?",
                                 (account_id, limit))
        else:
            rows = self.db.query("SELECT * FROM risk_events ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["order_payload"] = loads(r["order_payload"])
        return rows
