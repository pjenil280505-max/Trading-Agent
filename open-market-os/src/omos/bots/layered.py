"""Adaptive layered entries ("baskets").

A layered bot does not have a fixed number of layers. Each time it enters, it plans a ladder from:

* the **risk budget**: money lost if every layer fills and the basket stop is hit
  (``basket_risk_pct`` of equity);
* the **current volatility**: spacing, depth, stop and target are multiples of ATR (or percentages),
  so a calm market gets tight, numerous layers and a wild one gets wide, fewer, smaller ones;
* **safety ceilings**: the risk engine's layer ceiling (lower in high volatility), exposure, cash
  and the optional per-bot ``max_layers``.

The whole ladder is then sent as one basket: the risk engine checks every layer together, as if all
of them fill, and places nothing unless the whole basket passes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from omos.errors import ValidationError

DEFAULTS: dict[str, Any] = {
    "direction": "long",          # long | short (short also needs shorting enabled in risk limits)
    "entry_condition": "",        # optional OMS expression, e.g. "rsi(close, 14) < 35"; empty = enter when flat
    "spacing_mode": "atr",        # atr | pct: unit for spacing, max_depth, stop, take_profit, trailing
    "atr_period": 14,
    "spacing": 1.0,               # distance between layers
    "max_depth": 4.0,             # deepest layer at most this far from the first
    "stop": 1.0,                  # basket stop this far beyond the deepest layer
    "take_profit": 1.5,           # exit everything this far beyond the average entry price
    "trail_activate": 0.0,        # 0 = no trailing; else start trailing after this much favourable move
    "trail_distance": 1.0,        # trailing stop distance once active
    "basket_risk_pct": 1.0,       # % of equity lost if every layer fills and the stop is hit
    "size_growth": 1.0,           # each deeper layer is this many times the previous (0.5-2)
    "first_entry": "market",      # market | limit (first layer one spacing away)
    "max_layers": 0,              # 0 = adaptive (only the risk engine's ceiling applies)
    "min_layer_value": 5.0,       # smallest layer worth placing, in account currency
    "expire_bars": 30,            # cancel an unfilled ladder after this many bars
    "cooldown_bars": 3,           # wait after a basket closes
    "max_spread_pct": 0.5,        # do not open a basket while the quoted spread is wider
    "min_volume": 0.0,            # do not open a basket after a closed bar with less volume
    "stale_after_bars": 3,        # no new basket when the newest closed bar is older than this
}


def normalise(cfg: dict[str, Any]) -> dict[str, Any]:
    out = {**DEFAULTS, **{k: v for k, v in cfg.items() if k in DEFAULTS or k in (
        "sizing", "size_value", "lookback_bars")}}
    if out["direction"] not in ("long", "short"):
        raise ValidationError("direction must be long or short")
    if out["spacing_mode"] not in ("atr", "pct"):
        raise ValidationError("spacing_mode must be atr or pct")
    if out["first_entry"] not in ("market", "limit"):
        raise ValidationError("first_entry must be market or limit")
    num = {"spacing": (0.05, 50), "max_depth": (0, 200), "stop": (0.05, 100), "take_profit": (0.05, 200),
           "trail_activate": (0, 200), "trail_distance": (0.05, 100), "basket_risk_pct": (0.01, 10),
           "size_growth": (0.5, 2.0), "min_layer_value": (0, 1e9), "max_spread_pct": (0.001, 100),
           "min_volume": (0, 1e18)}
    for k, (lo, hi) in num.items():
        v = float(out[k])
        if not (math.isfinite(v) and lo <= v <= hi):
            raise ValidationError(f"{k} must be between {lo} and {hi}")
        out[k] = v
    for k, (lo, hi) in {"atr_period": (2, 200), "max_layers": (0, 100), "expire_bars": (1, 10_000),
                        "cooldown_bars": (0, 10_000), "stale_after_bars": (1, 1_000)}.items():
        v = int(out[k])
        if not lo <= v <= hi:
            raise ValidationError(f"{k} must be between {lo} and {hi}")
        out[k] = v
    out["entry_condition"] = str(out["entry_condition"] or "").strip()
    return out


@dataclass
class LayerPlan:
    layers: list[dict[str, Any]] = field(default_factory=list)   # {type, limit_price?, qty, price}
    stop: float = 0.0
    unit: float = 0.0
    risk: float = 0.0
    notional: float = 0.0
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"layers": self.layers, "stop": self.stop, "unit": self.unit, "risk": self.risk,
                "notional": self.notional, "why": self.why}


def plan(cfg: dict[str, Any], *, price: float, atr: float | None, equity: float, ceiling: int,
         max_notional: float) -> LayerPlan:
    """Ladder for one basket. Pure function: the same inputs always give the same plan."""
    if not (price > 0 and equity > 0):
        return LayerPlan(why="no valid price or equity")
    if cfg["spacing_mode"] == "atr":
        if not atr or not math.isfinite(atr) or atr <= 0:
            return LayerPlan(why="volatility (ATR) not available yet")
        unit = atr
    else:
        unit = price / 100
    sign = 1 if cfg["direction"] == "long" else -1
    spacing, depth = cfg["spacing"] * unit, cfg["max_depth"] * unit
    stop_dist = cfg["stop"] * unit
    first = price if cfg["first_entry"] == "market" else price - sign * spacing
    by_depth = int(depth // spacing) + 1 if spacing > 0 else 1
    limit_n = min(by_depth, max(1, ceiling), cfg["max_layers"] or by_depth)
    budget = equity * cfg["basket_risk_pct"] / 100
    why_n = ("depth" if limit_n == by_depth else "safety ceiling" if limit_n == ceiling else "max_layers")
    for n in range(limit_n, 0, -1):
        prices = [first - sign * i * spacing for i in range(n)]
        stop = prices[-1] - sign * stop_dist
        if min(prices) <= 0 or stop <= 0:
            continue  # ladder would reach zero: fewer layers
        weights = [cfg["size_growth"] ** i for i in range(n)]
        denom = sum(w * abs(p - stop) for w, p in zip(weights, prices))
        k = budget / denom
        notional = sum(k * w * p for w, p in zip(weights, prices))
        if notional > max_notional > 0:
            k *= max_notional / notional  # exposure or cash caps the size; risk ends up below budget
        qtys = [k * w for w in weights]
        if min(q * p for q, p in zip(qtys, prices)) < cfg["min_layer_value"]:
            why_n = "smallest layer below the minimum value"
            continue
        layers = []
        for i, (q, p) in enumerate(zip(qtys, prices)):
            market = i == 0 and cfg["first_entry"] == "market"
            layers.append({"type": "market" if market else "limit", "limit_price": None if market else _round(p),
                           "qty": _round(q), "price": _round(p), "stop_loss": _round(stop)})
        risk = sum(lyr["qty"] * abs(lyr["price"] - stop) for lyr in layers)
        return LayerPlan(layers, _round(stop), unit, risk, sum(lyr["qty"] * lyr["price"] for lyr in layers),
                         f"{n} layer(s): limited by {why_n}")
    return LayerPlan(unit=unit, why="the risk budget is too small for even one layer of the minimum value")


def _round(x: float) -> float:
    if x == 0 or not math.isfinite(x):
        return x
    return round(x, max(0, 8 - int(math.floor(math.log10(abs(x))))))
