"""Bar-based backtesting engine.

Execution model (documented in docs/reference/BACKTESTING.md):

* Signals are evaluated on the CLOSE of bar ``i`` and filled on the OPEN of bar ``i+1``
  (``fill="next_open"``, the default). ``fill="close"`` fills on the signal bar's close and is
  flagged as optimistic in the results.
* Every fill pays slippage (bps, adverse), half the spread (adverse) and commission
  (percent of notional + fixed per order).
* Stops/targets are checked intrabar from the bar AFTER entry, using that bar's high/low.
  If the bar opens beyond the level, the fill is at the open (gap risk). If a single bar
  touches both stop and target, the stop is assumed first (``stop_first=True``) — the
  pessimistic choice, because OHLC bars do not reveal the order of prices within a bar.
* Trailing stops ratchet on bar closes only.
* No look-ahead: the decision at bar ``i`` uses only bars ``<= i`` (tested by truncation).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from omos.backtest.metrics import compute_metrics, json_safe, periods_per_year
from omos.errors import ValidationError
from omos.marketdata.models import Candles
from omos.script.lang import Program, ScriptResult, parse, run


@dataclass
class BacktestConfig:
    initial_cash: float = 10_000.0
    commission_pct: float = 0.1        # % of notional per fill
    commission_fixed: float = 0.0      # per fill, account currency
    slippage_bps: float = 5.0          # adverse, per fill
    spread_bps: float = 0.0            # full spread; half is paid on each fill
    fill: str = "next_open"            # next_open | close
    sizing: str = "pct_equity"         # pct_equity | fixed_qty | fixed_cash | risk_pct
    size_value: float = 100.0
    allow_short: bool = False
    pyramiding: int = 1                # max same-direction entries
    stop_first: bool = True
    max_leverage: float = 1.0
    partial_exit_pct: float = 0.0      # % of position closed at first take-profit (0 = close all)
    close_at_end: bool = True
    periods_per_year: float | None = None
    session: str = "24x7"

    def validate(self) -> None:
        if self.initial_cash <= 0:
            raise ValidationError("initial cash must be positive")
        for name in ("commission_pct", "commission_fixed", "slippage_bps", "spread_bps"):
            if getattr(self, name) < 0:
                raise ValidationError(f"{name} cannot be negative")
        if self.fill not in ("next_open", "close"):
            raise ValidationError("fill must be next_open or close")
        if self.sizing not in ("pct_equity", "fixed_qty", "fixed_cash", "risk_pct"):
            raise ValidationError("sizing must be pct_equity, fixed_qty, fixed_cash or risk_pct")
        if self.size_value <= 0:
            raise ValidationError("size value must be positive")
        if not 1 <= self.pyramiding <= 50:
            raise ValidationError("pyramiding must be between 1 and 50")
        if not 0 < self.max_leverage <= 100:
            raise ValidationError("max leverage must be in (0, 100]")
        if not 0 <= self.partial_exit_pct < 100:
            raise ValidationError("partial exit % must be in [0, 100)")


@dataclass
class Signals:
    long_entry: np.ndarray
    long_exit: np.ndarray
    short_entry: np.ndarray
    short_exit: np.ndarray
    stop_dist: np.ndarray       # price distance, NaN = none
    target_dist: np.ndarray
    trail_dist: np.ndarray
    size_pct: np.ndarray        # NaN = use config


def signals_from_script(res: ScriptResult, close: np.ndarray) -> Signals:
    n = res.n

    def b(name: str) -> np.ndarray:
        v = res.series(name, as_bool=True)
        return v if v is not None else np.zeros(n, dtype=bool)

    def dist(abs_name: str, pct_name: str) -> np.ndarray:
        a = res.series(abs_name)
        if a is not None:
            return np.abs(a)
        p = res.series(pct_name)
        if p is not None:
            return np.abs(p) / 100.0 * close
        return np.full(n, np.nan)

    size = res.series("position_size_pct")
    return Signals(b("long_entry"), b("long_exit"), b("short_entry"), b("short_exit"),
                   dist("stop_loss", "stop_loss_pct"), dist("take_profit", "take_profit_pct"),
                   dist("trailing_stop", "trailing_stop_pct"),
                   size if size is not None else np.full(n, np.nan))


@dataclass
class _Pos:
    side: int = 0              # +1 long, -1 short, 0 flat
    qty: float = 0.0           # absolute quantity
    avg: float = 0.0
    entry_bar: int = -1
    entry_ts: int = 0
    layers: int = 0
    stop: float = math.nan
    target: float = math.nan
    trail_dist: float = math.nan
    trail_level: float = math.nan
    extreme: float = math.nan  # best price since entry (for trailing)
    fees: float = 0.0
    partial_done: bool = False


@dataclass
class BacktestResult:
    config: dict[str, Any]
    metrics: dict[str, Any]
    trades: list[dict[str, Any]]
    equity: list[list[float]]          # [ts, equity]
    drawdown: list[list[float]]
    warnings: list[str] = field(default_factory=list)
    params: dict[str, float] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"config": self.config, "metrics": json_safe(self.metrics), "trades": self.trades,
                "equity": self.equity, "drawdown": self.drawdown, "warnings": self.warnings,
                "params": self.params, "data": self.data}


class Engine:
    def __init__(self, candles: Candles, signals: Signals, config: BacktestConfig):
        config.validate()
        self.c = candles
        self.s = signals
        self.cfg = config
        n = len(candles)
        for name in ("long_entry", "long_exit", "short_entry", "short_exit", "stop_dist", "target_dist",
                     "trail_dist", "size_pct"):
            if len(getattr(signals, name)) != n:
                raise ValidationError(f"signal '{name}' length does not match the data")

    # -- price helpers -------------------------------------------------------------
    def _adverse(self, price: float, side: int) -> float:
        """Execution price after slippage and half-spread for a fill in direction ``side`` (+1 buy)."""
        cost = (self.cfg.slippage_bps + self.cfg.spread_bps / 2.0) / 10_000.0
        return price * (1 + cost) if side > 0 else price * (1 - cost)

    def _fee(self, notional: float) -> float:
        return abs(notional) * self.cfg.commission_pct / 100.0 + self.cfg.commission_fixed

    def run(self) -> BacktestResult:
        c, s, cfg = self.c, self.s, self.cfg
        n = len(c)
        o, h, lo, cl, ts = c.open, c.high, c.low, c.close, c.ts
        cash = cfg.initial_cash
        pos = _Pos()
        trades: list[dict[str, Any]] = []
        equity = np.empty(n)
        fees_total = 0.0
        exposure = 0
        warnings: list[str] = []
        if cfg.fill == "close":
            warnings.append("fill=close assumes you can trade at the signal bar's close: optimistic.")

        pending: list[tuple[str, int, int]] = []  # (action, signal_bar, side)

        def close_position(i: int, price: float, reason: str, fraction: float = 1.0) -> None:
            nonlocal cash, pos, fees_total
            q = pos.qty * fraction
            exit_px = self._adverse(price, -pos.side)
            fee = self._fee(q * exit_px)
            gross = (exit_px - pos.avg) * q * pos.side
            entry_fee_share = pos.fees * fraction
            cash += pos.side * q * exit_px - fee
            fees_total += fee
            pnl = gross - fee - entry_fee_share
            trades.append({
                "side": "long" if pos.side > 0 else "short", "entry_ts": int(pos.entry_ts), "exit_ts": int(ts[i]),
                "entry_bar": pos.entry_bar, "exit_bar": i, "entry_price": pos.avg, "exit_price": exit_px, "qty": q,
                "pnl": pnl, "pnl_pct": 100.0 * pnl / (pos.avg * q) if pos.avg * q else 0.0,
                "fees": fee + entry_fee_share, "bars": i - pos.entry_bar, "reason": reason, "layers": pos.layers,
            })
            pos.fees -= entry_fee_share
            pos.qty -= q
            if pos.qty <= 1e-12 or fraction >= 1.0:
                pos = _Pos()

        def open_position(i: int, price: float, side: int, sig_bar: int) -> None:
            nonlocal cash, pos, fees_total
            px = self._adverse(price, side)
            eq_now = cash + (pos.side * pos.qty * px if pos.side else 0.0)
            size_pct = s.size_pct[sig_bar]
            stop_d = s.stop_dist[sig_bar]
            if cfg.sizing == "fixed_qty":
                q = cfg.size_value
            elif cfg.sizing == "fixed_cash":
                q = cfg.size_value / px
            elif cfg.sizing == "risk_pct":
                if not np.isfinite(stop_d) or stop_d <= 0:
                    warnings.append(f"bar {sig_bar}: risk_pct sizing needs stop_loss; entry skipped")
                    return
                q = (eq_now * cfg.size_value / 100.0) / stop_d
            else:
                pct = size_pct * 100 if np.isfinite(size_pct) else cfg.size_value
                q = eq_now * pct / 100.0 / px / max(1, cfg.pyramiding)
            # leverage cap on the combined position
            max_notional = eq_now * cfg.max_leverage
            room = max_notional - pos.qty * px
            q = min(q, max(room, 0.0) / px) if px > 0 else 0.0
            # fees must fit in cash for long cash accounts
            if side > 0 and cfg.max_leverage <= 1.0:
                q = min(q, max(cash, 0.0) / (px * (1 + cfg.commission_pct / 100.0)) - cfg.commission_fixed / px)
            if q <= 1e-12:
                return
            fee = self._fee(q * px)
            cash -= side * q * px + fee
            fees_total += fee
            if pos.side == 0:
                pos = _Pos(side=side, qty=q, avg=px, entry_bar=i, entry_ts=int(ts[i]), layers=1, fees=fee)
            else:
                pos.avg = (pos.avg * pos.qty + px * q) / (pos.qty + q)
                pos.qty += q
                pos.layers += 1
                pos.fees += fee
            if np.isfinite(stop_d) and stop_d > 0:
                pos.stop = pos.avg - side * stop_d
            t_d = s.target_dist[sig_bar]
            if np.isfinite(t_d) and t_d > 0:
                pos.target = pos.avg + side * t_d
            tr_d = s.trail_dist[sig_bar]
            if np.isfinite(tr_d) and tr_d > 0:
                pos.trail_dist = tr_d
                pos.extreme = px
                pos.trail_level = px - side * tr_d

        for i in range(n):
            # 1) fills of orders generated at the previous bar's close
            if pending:
                for action, sig_bar, side in pending:
                    if action == "exit" and pos.side == side:
                        close_position(i, o[i], "signal")
                    elif action == "entry":
                        if pos.side == -side:
                            close_position(i, o[i], "reverse")
                        if pos.side in (0, side) and pos.layers < cfg.pyramiding:
                            open_position(i, o[i], side, sig_bar)
                pending = []

            # 2) intrabar stop / target / trailing checks (never on the entry bar itself for next_open fills
            #    filled at this bar's open, the bar's whole range happens after entry, so checks apply)
            if pos.side != 0 and (pos.entry_bar < i or cfg.fill == "next_open"):
                self._check_exits(i, pos, close_position)

            # 3) signals at this bar's close
            if i < n - 1 or cfg.fill == "close":
                orders = self._orders_for_bar(i, pos)
                if cfg.fill == "close":
                    for action, side in orders:
                        if action == "exit" and pos.side == side:
                            close_position(i, cl[i], "signal")
                        elif action == "entry":
                            if pos.side == -side:
                                close_position(i, cl[i], "reverse")
                            if pos.side in (0, side) and pos.layers < cfg.pyramiding:
                                open_position(i, cl[i], side, i)
                else:
                    pending = [(a, i, sd) for a, sd in orders]

            # 4) trailing stop ratchets on the close
            if pos.side != 0 and np.isfinite(pos.trail_dist):
                if pos.side > 0:
                    pos.extreme = max(pos.extreme, cl[i])
                    pos.trail_level = max(pos.trail_level, pos.extreme - pos.trail_dist)
                else:
                    pos.extreme = min(pos.extreme, cl[i])
                    pos.trail_level = min(pos.trail_level, pos.extreme + pos.trail_dist)

            if pos.side != 0:
                exposure += 1
            equity[i] = cash + pos.side * pos.qty * cl[i]

        if pos.side != 0 and cfg.close_at_end and n:
            close_position(n - 1, cl[-1], "end_of_data")
            equity[-1] = cash

        ppy = cfg.periods_per_year or periods_per_year(c.timeframe or "1d", cfg.session)
        bh = float(cl[-1] / cl[0] - 1.0) if n > 1 and cl[0] > 0 else None
        metrics = compute_metrics(equity, trades, ppy=ppy, initial_cash=cfg.initial_cash, exposure_bars=exposure,
                                  n_bars=n, buy_hold_return=bh, fees_paid=fees_total)
        peaks = np.maximum.accumulate(equity) if n else equity
        dd = np.where(peaks > 0, equity / peaks - 1.0, 0.0) if n else equity
        step = max(1, n // 2000)  # keep payloads small; metrics use the full curve
        eq_out = [[int(ts[i]), float(equity[i])] for i in range(0, n, step)]
        dd_out = [[int(ts[i]), float(dd[i] * 100)] for i in range(0, n, step)]
        if n and (n - 1) % step:
            eq_out.append([int(ts[-1]), float(equity[-1])])
            dd_out.append([int(ts[-1]), float(dd[-1] * 100)])
        return BacktestResult(asdict(cfg), metrics, trades, eq_out, dd_out, sorted(set(warnings))[:20])

    def _orders_for_bar(self, i: int, pos: _Pos) -> list[tuple[str, int]]:
        s, cfg = self.s, self.cfg
        orders: list[tuple[str, int]] = []
        if pos.side > 0 and s.long_exit[i]:
            orders.append(("exit", 1))
        if pos.side < 0 and s.short_exit[i]:
            orders.append(("exit", -1))
        if s.long_entry[i] and not (pos.side > 0 and s.long_exit[i]):
            orders.append(("entry", 1))
        elif cfg.allow_short and s.short_entry[i] and not (pos.side < 0 and s.short_exit[i]):
            orders.append(("entry", -1))
        return orders

    def _check_exits(self, i: int, pos: _Pos, close_position) -> None:
        o, h, lo = self.c.open[i], self.c.high[i], self.c.low[i]
        side = pos.side
        stop = pos.stop
        if np.isfinite(pos.trail_level):
            stop = pos.trail_level if not np.isfinite(stop) else (max(stop, pos.trail_level) if side > 0 else min(stop, pos.trail_level))
        target = pos.target
        stop_hit = np.isfinite(stop) and (lo <= stop if side > 0 else h >= stop)
        target_hit = np.isfinite(target) and (h >= target if side > 0 else lo <= target)
        if stop_hit and (not target_hit or self.cfg.stop_first):
            gap = (o <= stop) if side > 0 else (o >= stop)
            reason = "trailing_stop" if np.isfinite(pos.trail_level) and stop == pos.trail_level else "stop_loss"
            close_position(i, o if gap else stop, reason)
            return
        if target_hit:
            gap = (o >= target) if side > 0 else (o <= target)
            px = o if gap else target
            if self.cfg.partial_exit_pct > 0 and not pos.partial_done:
                close_position(i, px, "take_profit_partial", fraction=self.cfg.partial_exit_pct / 100.0)
                if pos.side != 0:
                    pos.partial_done = True
                    pos.target = math.nan
                    pos.stop = pos.avg  # remaining size: stop moves to break-even
            else:
                close_position(i, px, "take_profit")


def backtest_script(source: str | Program, candles: Candles, config: BacktestConfig | None = None,
                    params: dict[str, Any] | None = None) -> BacktestResult:
    prog = parse(source) if isinstance(source, str) else source
    if not prog.is_strategy:
        raise ValidationError("a strategy script must assign long_entry (and/or short_entry)")
    if len(candles) < 2:
        raise ValidationError("not enough data to backtest (need at least 2 bars)")
    cfg = config or BacktestConfig()
    res = run(prog, candles, params)
    sig = signals_from_script(res, candles.close)
    result = Engine(candles, sig, cfg).run()
    result.params = res.params
    result.data = {"instrument_id": candles.instrument_id, "timeframe": candles.timeframe,
                   "status": candles.status.value, "source": candles.source, "bars": len(candles),
                   "from": int(candles.ts[0]), "to": int(candles.ts[-1])}
    if candles.status.value == "SIMULATED":
        result.warnings.append("Backtest used SIMULATED data — results say nothing about real markets.")
    if candles.quality.get("missing_bars"):
        result.warnings.append(f"Data has {candles.quality['missing_bars']} missing bar(s); see data quality report.")
    if result.metrics["trades"] < 30:
        result.warnings.append(f"Only {result.metrics['trades']} trades: too few to judge the strategy statistically.")
    return result
