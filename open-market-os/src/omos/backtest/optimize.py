"""Parameter optimisation, walk-forward analysis, out-of-sample split and Monte Carlo.

Overfitting is the main danger here, so every result reports out-of-sample numbers
next to in-sample ones, and the grid size is capped.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np

from omos.backtest.engine import BacktestConfig, backtest_script
from omos.errors import ValidationError
from omos.marketdata.models import Candles
from omos.script.lang import Program, parse

MAX_COMBINATIONS = 400
OBJECTIVES = {"sharpe", "sortino", "calmar", "total_return_pct", "profit_factor", "cagr_pct"}


def _grid(prog: Program, ranges: dict[str, list[float]] | None) -> list[dict[str, float]]:
    space: dict[str, list[float]] = {}
    if ranges:
        for k, vals in ranges.items():
            if k not in prog.params:
                raise ValidationError(f"unknown parameter '{k}'")
            if not vals or len(vals) > 50:
                raise ValidationError(f"parameter '{k}' needs 1–50 values")
            space[k] = [float(v) for v in vals]
    else:
        bounded = {name: p.bounds for name, p in prog.params.items() if p.bounds}
        # Spread the combination budget across the parameters so a script's own declared ranges
        # always produce a runnable default grid (e.g. 2 params -> at most 20 values each).
        per_param = max(2, min(25, int(MAX_COMBINATIONS ** (1 / len(bounded)) + 1e-9))) if bounded else 0
        for name, (lo, hi, step) in bounded.items():
            count = int(math.floor((hi - lo) / step + 1e-9)) + 1
            if count > per_param:  # coarsen automatically, keep endpoints
                vals = [float(v) for v in np.linspace(lo, hi, per_param)]
                if all(float(x).is_integer() for x in (lo, hi, step)):
                    vals = sorted({float(round(v)) for v in vals})  # lengths/periods stay whole numbers
            else:
                vals = [lo + k * step for k in range(count)]
            space[name] = [round(v, 10) for v in vals]
    if not space:
        raise ValidationError("no parameters to optimise: declare ranges like `param fast = 9 (5, 30, 1)`")
    combos = 1
    for v in space.values():
        combos *= len(v)
    if combos > MAX_COMBINATIONS:
        raise ValidationError(f"{combos} combinations exceed the limit of {MAX_COMBINATIONS}; narrow the ranges")
    keys = list(space)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(space[k] for k in keys))]


def _score(metrics: dict[str, Any], objective: str, min_trades: int) -> float:
    if metrics["trades"] < min_trades:
        return -math.inf
    v = metrics.get(objective)
    if v is None or not isinstance(v, int | float) or math.isnan(v):
        return -math.inf
    return float(min(v, 1e6))


def optimize(source: str, candles: Candles, config: BacktestConfig | None = None, *,
             ranges: dict[str, list[float]] | None = None, objective: str = "sharpe", min_trades: int = 5,
             top: int = 20) -> dict[str, Any]:
    if objective not in OBJECTIVES:
        raise ValidationError(f"objective must be one of {', '.join(sorted(OBJECTIVES))}")
    prog = parse(source)
    grid = _grid(prog, ranges)
    rows = []
    for params in grid:
        res = backtest_script(prog, candles, config, params)
        m = res.metrics
        rows.append({"params": params, "score": _score(m, objective, min_trades),
                     "metrics": {k: m[k] for k in ("total_return_pct", "sharpe", "max_drawdown_pct", "trades",
                                                   "win_rate_pct", "profit_factor", "cagr_pct")}})
    rows.sort(key=lambda r: r["score"], reverse=True)
    valid = [r for r in rows if r["score"] != -math.inf]
    return {"objective": objective, "combinations": len(grid), "valid": len(valid),
            "results": [{**r, "score": None if r["score"] == -math.inf else r["score"]} for r in rows[:top]],
            "warning": ("Best-of-grid results are biased upward (they were picked BECAUSE they did well on this "
                        "data). Check walk-forward / out-of-sample results before trusting them.")}


def out_of_sample(source: str, candles: Candles, config: BacktestConfig | None = None, *, train_pct: float = 70.0,
                  ranges: dict[str, list[float]] | None = None, objective: str = "sharpe") -> dict[str, Any]:
    if not 30 <= train_pct <= 90:
        raise ValidationError("train % must be between 30 and 90")
    cut = int(len(candles) * train_pct / 100)
    train, test = candles.slice(0, cut), candles.slice(cut)
    if len(test) < 50 or len(train) < 100:
        raise ValidationError("not enough data for an out-of-sample split")
    opt = optimize(source, train, config, ranges=ranges, objective=objective)
    if not opt["results"] or opt["results"][0]["score"] is None:
        return {"train": opt, "test": None, "message": "no parameter set produced enough trades in-sample"}
    best = opt["results"][0]["params"]
    test_res = backtest_script(source, test, config, best)
    return {"best_params": best, "in_sample": opt["results"][0]["metrics"],
            "out_of_sample": {k: test_res.metrics[k] for k in ("total_return_pct", "sharpe", "max_drawdown_pct",
                                                              "trades", "win_rate_pct", "profit_factor")},
            "split_ts": int(candles.ts[cut]), "train_bars": len(train), "test_bars": len(test)}


def walk_forward(source: str, candles: Candles, config: BacktestConfig | None = None, *, folds: int = 4,
                 train_pct: float = 70.0, ranges: dict[str, list[float]] | None = None,
                 objective: str = "sharpe") -> dict[str, Any]:
    """Rolling walk-forward: each fold optimises on a window and tests on the window after it."""
    if not 2 <= folds <= 12:
        raise ValidationError("folds must be between 2 and 12")
    n = len(candles)
    window = n // folds
    if window < 150:
        raise ValidationError("not enough data for this many folds (need ~150 bars per fold)")
    out = []
    oos_returns = []
    for k in range(folds):
        seg = candles.slice(k * window, min(n, (k + 1) * window))
        cut = int(len(seg) * train_pct / 100)
        train, test = seg.slice(0, cut), seg.slice(cut)
        opt = optimize(source, train, config, ranges=ranges, objective=objective, min_trades=2)
        best = opt["results"][0] if opt["results"] and opt["results"][0]["score"] is not None else None
        if not best:
            out.append({"fold": k + 1, "message": "no valid in-sample parameters"})
            continue
        t = backtest_script(source, test, config, best["params"])
        oos_returns.append(t.metrics["total_return_pct"])
        out.append({"fold": k + 1, "params": best["params"], "in_sample_return_pct": best["metrics"]["total_return_pct"],
                    "out_of_sample_return_pct": t.metrics["total_return_pct"],
                    "out_of_sample_sharpe": t.metrics["sharpe"], "out_of_sample_trades": t.metrics["trades"],
                    "test_from": int(test.ts[0]), "test_to": int(test.ts[-1])})
    is_ret = [f["in_sample_return_pct"] for f in out if "in_sample_return_pct" in f]
    eff = (np.mean(oos_returns) / np.mean(is_ret)) if is_ret and np.mean(is_ret) != 0 else None
    return {"folds": out, "mean_oos_return_pct": float(np.mean(oos_returns)) if oos_returns else None,
            "profitable_oos_folds": int(sum(r > 0 for r in oos_returns)), "walk_forward_efficiency": eff}


def monte_carlo(trades: list[dict[str, Any]], initial_cash: float, *, runs: int = 1000, seed: int = 7) -> dict[str, Any]:
    """Resample trade returns with replacement to see how much the outcome depends on trade order/luck."""
    if not trades:
        raise ValidationError("no trades to resample")
    if not 100 <= runs <= 20_000:
        raise ValidationError("runs must be between 100 and 20000")
    r = np.array([t["pnl_pct"] / 100.0 for t in trades])
    rng = np.random.default_rng(seed)
    finals, dds = np.empty(runs), np.empty(runs)
    for k in range(runs):
        sample = rng.choice(r, size=len(r), replace=True)
        eq = initial_cash * np.cumprod(1 + sample)
        peaks = np.maximum.accumulate(np.r_[initial_cash, eq])
        dds[k] = float(-(np.r_[initial_cash, eq] / peaks - 1).min()) * 100
        finals[k] = eq[-1]
    pct = lambda a, q: float(np.percentile(a, q))  # noqa: E731
    return {"runs": runs, "trades": len(r),
            "final_equity": {"p5": pct(finals, 5), "p50": pct(finals, 50), "p95": pct(finals, 95)},
            "max_drawdown_pct": {"p50": pct(dds, 50), "p95": pct(dds, 95), "worst": float(dds.max())},
            "prob_loss_pct": float((finals < initial_cash).mean() * 100),
            "note": "Assumes trades are independent and position size is a fixed fraction of equity."}
