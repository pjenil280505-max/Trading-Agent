"""Performance metrics computed from an equity curve and a trade list."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from omos.marketdata.timeframes import parse


def periods_per_year(timeframe: str, session: str = "24x7") -> float:
    tf = parse(timeframe)
    if session == "24x7":
        return 365.25 * 86_400_000 / tf.ms
    if session == "fx24x5":
        return 260 * 86_400_000 / min(tf.ms, 86_400_000) if tf.ms < 86_400_000 else 260 / (tf.ms / 86_400_000)
    # exchange sessions: ~252 trading days of ~6.5 hours
    if tf.ms >= 86_400_000:
        return 252.0 / (tf.ms / 86_400_000) if tf.unit != "w" else 52.0 / tf.n
    return 252.0 * (6.5 * 3_600_000) / tf.ms


def max_drawdown(equity: np.ndarray) -> tuple[float, int, int, int]:
    """Returns (max drawdown fraction, peak index, trough index, longest underwater bars)."""
    if len(equity) == 0:
        return 0.0, 0, 0, 0
    peaks = np.maximum.accumulate(equity)
    dd = np.where(peaks > 0, equity / peaks - 1.0, 0.0)
    trough = int(np.argmin(dd))
    peak = int(np.argmax(equity[: trough + 1])) if trough > 0 else 0
    underwater = dd < 0
    longest = cur = 0
    for u in underwater:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    return float(-dd.min()), peak, trough, longest


def compute_metrics(equity: np.ndarray, trades: list[dict[str, Any]], *, ppy: float, initial_cash: float,
                    exposure_bars: int, n_bars: int, buy_hold_return: float | None = None,
                    fees_paid: float = 0.0) -> dict[str, Any]:
    eq = np.asarray(equity, dtype=float)
    final = float(eq[-1]) if len(eq) else initial_cash
    total_ret = final / initial_cash - 1.0
    rets = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([])
    rets = rets[np.isfinite(rets)]
    years = n_bars / ppy if ppy > 0 else 0.0
    cagr = (final / initial_cash) ** (1 / years) - 1 if years > 0 and final > 0 else (-1.0 if final <= 0 else 0.0)
    vol = float(np.std(rets, ddof=1) * math.sqrt(ppy)) if len(rets) > 2 else 0.0
    mean = float(np.mean(rets)) if len(rets) else 0.0
    sharpe = mean / np.std(rets, ddof=1) * math.sqrt(ppy) if len(rets) > 2 and np.std(rets, ddof=1) > 0 else 0.0
    downside = rets[rets < 0]
    dd_dev = float(np.sqrt(np.mean(downside ** 2))) if len(downside) else 0.0
    sortino = mean / dd_dev * math.sqrt(ppy) if dd_dev > 0 else 0.0
    mdd, peak, trough, underwater = max_drawdown(eq)
    calmar = cagr / mdd if mdd > 0 else 0.0

    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    gross_win, gross_loss = float(wins.sum()), float(-losses.sum())
    win_rate = len(wins) / len(pnls) if len(pnls) else 0.0
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    pct = np.array([t["pnl_pct"] for t in trades], dtype=float)
    return {
        "initial_cash": initial_cash,
        "final_equity": final,
        "total_return_pct": total_ret * 100,
        "cagr_pct": cagr * 100,
        "volatility_ann_pct": vol * 100,
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "calmar": float(calmar),
        "max_drawdown_pct": mdd * 100,
        "max_drawdown_peak_bar": peak,
        "max_drawdown_trough_bar": trough,
        "longest_underwater_bars": underwater,
        "trades": len(pnls),
        "win_rate_pct": win_rate * 100,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0),
        "expectancy": float(pnls.mean()) if len(pnls) else 0.0,
        "expectancy_pct": float(pct.mean()) if len(pct) else 0.0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": (avg_win / -avg_loss) if avg_loss < 0 else 0.0,
        "best_trade_pct": float(pct.max()) if len(pct) else 0.0,
        "worst_trade_pct": float(pct.min()) if len(pct) else 0.0,
        "avg_bars_held": float(np.mean([t["bars"] for t in trades])) if trades else 0.0,
        "exposure_pct": 100.0 * exposure_bars / n_bars if n_bars else 0.0,
        "fees_paid": fees_paid,
        "buy_hold_return_pct": None if buy_hold_return is None else buy_hold_return * 100,
        "years": years,
    }


def json_safe(d: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
            out[k] = None if math.isnan(v) else ("inf" if v > 0 else "-inf")
        else:
            out[k] = v
    return out
