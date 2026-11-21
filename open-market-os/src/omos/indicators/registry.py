"""Indicator registry: metadata for the UI/screener/scripts plus a single compute entry point."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from omos.errors import ValidationError
from omos.indicators import core as ta
from omos.indicators import structure as st
from omos.marketdata.models import Candles

SOURCES = ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4")


def source_series(c: Candles, name: str) -> np.ndarray:
    if name == "close":
        return c.close
    if name == "open":
        return c.open
    if name == "high":
        return c.high
    if name == "low":
        return c.low
    if name == "hl2":
        return (c.high + c.low) / 2
    if name == "hlc3":
        return (c.high + c.low + c.close) / 3
    if name == "ohlc4":
        return (c.open + c.high + c.low + c.close) / 4
    raise ValidationError(f"unknown source '{name}'")


@dataclass
class Param:
    name: str
    default: Any
    kind: str = "int"         # int | float | choice
    min: float | None = 1
    max: float | None = 1000
    choices: tuple[str, ...] = ()

    def coerce(self, value: Any) -> Any:
        if value is None:
            return self.default
        if self.kind == "choice":
            if value not in self.choices:
                raise ValidationError(f"{self.name} must be one of {', '.join(self.choices)}")
            return value
        try:
            v = int(value) if self.kind == "int" else float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"{self.name} must be a number") from exc
        if self.min is not None and v < self.min or self.max is not None and v > self.max:
            raise ValidationError(f"{self.name} must be between {self.min} and {self.max}")
        return v

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "default": self.default, "kind": self.kind, "min": self.min, "max": self.max,
                "choices": list(self.choices)}


@dataclass
class Output:
    name: str
    kind: str = "line"        # line | histogram | dots | markers
    color: str = "#2962ff"


@dataclass
class Spec:
    id: str
    name: str
    category: str
    overlay: bool
    params: list[Param]
    outputs: list[Output]
    fn: Callable[[Candles, dict[str, Any]], dict[str, np.ndarray]]
    levels: list[float] = field(default_factory=list)
    status: str = "tested"     # tested | implemented
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "category": self.category, "overlay": self.overlay,
                "params": [p.to_dict() for p in self.params],
                "outputs": [{"name": o.name, "kind": o.kind, "color": o.color} for o in self.outputs],
                "levels": self.levels, "status": self.status, "note": self.note}


def _src(default: str = "close") -> Param:
    return Param("source", default, "choice", None, None, SOURCES)


def _len(default: int, name: str = "length", lo: int = 1, hi: int = 2000) -> Param:
    return Param(name, default, "int", lo, hi)


def _flt(name: str, default: float, lo: float = 0.0, hi: float = 100.0) -> Param:
    return Param(name, default, "float", lo, hi)


def _one(fn):
    return lambda c, p: {"value": fn(c, p)}


_BLUE, _ORANGE, _GREEN, _RED, _PURPLE, _GREY = "#2962ff", "#ff9800", "#26a69a", "#ef5350", "#ab47bc", "#9e9e9e"

SPECS: dict[str, Spec] = {s.id: s for s in [
    # --- trend
    Spec("sma", "Simple Moving Average", "trend", True, [_len(20), _src()], [Output("value")],
         _one(lambda c, p: ta.sma(source_series(c, p["source"]), p["length"]))),
    Spec("ema", "Exponential Moving Average", "trend", True, [_len(20), _src()], [Output("value", color=_ORANGE)],
         _one(lambda c, p: ta.ema(source_series(c, p["source"]), p["length"]))),
    Spec("wma", "Weighted Moving Average", "trend", True, [_len(20), _src()], [Output("value", color=_PURPLE)],
         _one(lambda c, p: ta.wma(source_series(c, p["source"]), p["length"]))),
    Spec("vwma", "Volume-Weighted Moving Average", "trend", True, [_len(20)], [Output("value", color=_GREEN)],
         _one(lambda c, p: ta.vwma(c.close, c.volume, p["length"]))),
    Spec("hma", "Hull Moving Average", "trend", True, [_len(21, lo=2), _src()], [Output("value", color=_RED)],
         _one(lambda c, p: ta.hma(source_series(c, p["source"]), p["length"]))),
    Spec("dema", "Double EMA", "trend", True, [_len(20), _src()], [Output("value", color=_GREEN)],
         _one(lambda c, p: ta.dema(source_series(c, p["source"]), p["length"]))),
    Spec("tema", "Triple EMA", "trend", True, [_len(20), _src()], [Output("value", color=_PURPLE)],
         _one(lambda c, p: ta.tema(source_series(c, p["source"]), p["length"]))),
    Spec("supertrend", "Supertrend", "trend", True, [_len(10), _flt("multiplier", 3.0, 0.1, 20)],
         [Output("line", color=_GREEN), Output("direction", "markers", _GREY)],
         lambda c, p: ta.supertrend(c.high, c.low, c.close, p["length"], p["multiplier"])),
    Spec("ichimoku", "Ichimoku (no chikou)", "trend", True,
         [_len(9, "tenkan"), _len(26, "kijun"), _len(52, "senkou_b"), _len(26, "displacement")],
         [Output("tenkan", color=_BLUE), Output("kijun", color=_RED), Output("span_a", color=_GREEN),
          Output("span_b", color=_ORANGE)],
         lambda c, p: ta.ichimoku(c.high, c.low, p["tenkan"], p["kijun"], p["senkou_b"], p["displacement"]),
         note="Spans are aligned to the bar they apply to; chikou omitted to avoid look-ahead."),
    Spec("adx", "ADX / DMI", "trend", False, [_len(14)],
         [Output("adx", color=_PURPLE), Output("plus_di", color=_GREEN), Output("minus_di", color=_RED)],
         lambda c, p: ta.adx(c.high, c.low, c.close, p["length"]), levels=[20, 25]),
    Spec("psar", "Parabolic SAR", "trend", True, [_flt("step", 0.02, 0.001, 1), _flt("max_step", 0.2, 0.01, 1)],
         [Output("value", "dots", _ORANGE)], _one(lambda c, p: ta.psar(c.high, c.low, p["step"], p["max_step"]))),
    # --- momentum
    Spec("rsi", "Relative Strength Index", "momentum", False, [_len(14), _src()], [Output("value", color=_PURPLE)],
         _one(lambda c, p: ta.rsi(source_series(c, p["source"]), p["length"])), levels=[30, 70]),
    Spec("stoch", "Stochastic", "momentum", False, [_len(14, "k"), _len(3, "d"), _len(3, "smooth")],
         [Output("k", color=_BLUE), Output("d", color=_ORANGE)],
         lambda c, p: ta.stoch(c.high, c.low, c.close, p["k"], p["d"], p["smooth"]), levels=[20, 80]),
    Spec("stochrsi", "Stochastic RSI", "momentum", False,
         [_len(14, "rsi_length"), _len(14, "stoch_length"), _len(3, "k"), _len(3, "d")],
         [Output("k", color=_BLUE), Output("d", color=_ORANGE)],
         lambda c, p: ta.stochrsi(c.close, p["rsi_length"], p["stoch_length"], p["k"], p["d"]), levels=[20, 80]),
    Spec("macd", "MACD", "momentum", False, [_len(12, "fast"), _len(26, "slow"), _len(9, "signal"), _src()],
         [Output("macd", color=_BLUE), Output("signal", color=_ORANGE), Output("hist", "histogram", _GREY)],
         lambda c, p: ta.macd(source_series(c, p["source"]), p["fast"], p["slow"], p["signal"]), levels=[0]),
    Spec("cci", "Commodity Channel Index", "momentum", False, [_len(20)], [Output("value", color=_GREEN)],
         _one(lambda c, p: ta.cci(c.high, c.low, c.close, p["length"])), levels=[-100, 100]),
    Spec("roc", "Rate of Change (%)", "momentum", False, [_len(10), _src()], [Output("value", color=_BLUE)],
         _one(lambda c, p: ta.roc(source_series(c, p["source"]), p["length"])), levels=[0]),
    Spec("momentum", "Momentum", "momentum", False, [_len(10), _src()], [Output("value", color=_BLUE)],
         _one(lambda c, p: ta.momentum(source_series(c, p["source"]), p["length"])), levels=[0]),
    Spec("willr", "Williams %R", "momentum", False, [_len(14)], [Output("value", color=_PURPLE)],
         _one(lambda c, p: ta.williams_r(c.high, c.low, c.close, p["length"])), levels=[-80, -20]),
    Spec("mfi", "Money Flow Index", "momentum", False, [_len(14)], [Output("value", color=_GREEN)],
         _one(lambda c, p: ta.mfi(c.high, c.low, c.close, c.volume, p["length"])), levels=[20, 80]),
    # --- volatility
    Spec("atr", "Average True Range", "volatility", False, [_len(14)], [Output("value", color=_RED)],
         _one(lambda c, p: ta.atr(c.high, c.low, c.close, p["length"]))),
    Spec("bbands", "Bollinger Bands", "volatility", True, [_len(20), _flt("stddev", 2.0, 0.1, 10), _src()],
         [Output("mid", color=_ORANGE), Output("upper", color=_BLUE), Output("lower", color=_BLUE)],
         lambda c, p: {k: v for k, v in ta.bbands(source_series(c, p["source"]), p["length"], p["stddev"]).items()
                       if k in ("mid", "upper", "lower")}),
    Spec("bbwidth", "Bollinger Band Width", "volatility", False, [_len(20), _flt("stddev", 2.0, 0.1, 10)],
         [Output("value", color=_BLUE)], _one(lambda c, p: ta.bbands(c.close, p["length"], p["stddev"])["width"])),
    Spec("keltner", "Keltner Channels", "volatility", True,
         [_len(20), _flt("multiplier", 2.0, 0.1, 10), _len(10, "atr_length")],
         [Output("mid", color=_ORANGE), Output("upper", color=_GREEN), Output("lower", color=_GREEN)],
         lambda c, p: ta.keltner(c.high, c.low, c.close, p["length"], p["multiplier"], p["atr_length"])),
    Spec("donchian", "Donchian Channels", "volatility", True, [_len(20)],
         [Output("upper", color=_BLUE), Output("mid", color=_GREY), Output("lower", color=_BLUE)],
         lambda c, p: ta.donchian(c.high, c.low, p["length"])),
    Spec("hv", "Historical Volatility (% annualised)", "volatility", False,
         [_len(20), _flt("periods_per_year", 365.0, 1, 1_000_000)], [Output("value", color=_RED)],
         _one(lambda c, p: ta.hv(c.close, p["length"], p["periods_per_year"]))),
    # --- volume
    Spec("volume", "Volume", "volume", False, [], [Output("value", "histogram", _GREY)],
         _one(lambda c, p: c.volume.astype(float))),
    Spec("obv", "On-Balance Volume", "volume", False, [], [Output("value", color=_BLUE)],
         _one(lambda c, p: ta.obv(c.close, c.volume))),
    Spec("vwap", "VWAP (anchored)", "volume", True, [Param("anchor", "D", "choice", None, None, ("D", "W", "none"))],
         [Output("value", color=_PURPLE)],
         _one(lambda c, p: ta.vwap(c.ts, c.high, c.low, c.close, c.volume, p["anchor"])),
         note="Resets at UTC day/week boundaries, not exchange session open."),
    Spec("ad", "Accumulation / Distribution", "volume", False, [], [Output("value", color=_GREEN)],
         _one(lambda c, p: ta.ad(c.high, c.low, c.close, c.volume))),
    # --- structure
    Spec("swings", "Swing Highs / Lows (confirmed)", "structure", True, [_len(3, "left"), _len(3, "right")],
         [Output("swing_high", "dots", _RED), Output("swing_low", "dots", _GREEN)],
         lambda c, p: {k: v for k, v in st.swing_points(c.high, c.low, p["left"], p["right"]).items()
                       if k in ("swing_high", "swing_low")},
         note="Plotted at the bar where each pivot is confirmed (left/right strength), so it never repaints."),
    Spec("pivots", "Classic Pivot Points (prior UTC day)", "structure", True, [],
         [Output("p", color=_GREY), Output("r1", color=_RED), Output("s1", color=_GREEN),
          Output("r2", color=_RED), Output("s2", color=_GREEN)],
         lambda c, p: {k: v for k, v in st.pivot_points(c.ts, c.high, c.low, c.close).items()
                       if k in ("p", "r1", "s1", "r2", "s2")}),
    Spec("structure", "Market Structure (BOS / CHoCH)", "structure", False, [_len(3, "left"), _len(3, "right")],
         [Output("bos", "histogram", _BLUE), Output("choch", "histogram", _ORANGE)],
         lambda c, p: {k: v for k, v in st.market_structure(c.open, c.high, c.low, c.close, p["left"], p["right"]).items()
                       if k in ("bos", "choch")}),
    Spec("fvg", "Fair Value Gaps", "structure", False, [],
         [Output("bullish", "histogram", _GREEN), Output("bearish", "histogram", _RED)],
         lambda c, p: {k: v for k, v in st.fair_value_gaps(c.high, c.low).items() if k in ("bullish", "bearish")}),
]}


def get_spec(indicator_id: str) -> Spec:
    spec = SPECS.get(indicator_id)
    if not spec:
        raise ValidationError(f"unknown indicator '{indicator_id}'")
    return spec


def resolve_params(spec: Spec, params: dict[str, Any] | None) -> dict[str, Any]:
    params = params or {}
    unknown = set(params) - {p.name for p in spec.params}
    if unknown:
        raise ValidationError(f"unknown parameter(s) for {spec.id}: {', '.join(sorted(unknown))}")
    return {p.name: p.coerce(params.get(p.name)) for p in spec.params}


def compute(indicator_id: str, candles: Candles, params: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = get_spec(indicator_id)
    p = resolve_params(spec, params)
    with np.errstate(all="ignore"):
        values = spec.fn(candles, p)
    outputs = {k: np.asarray(v, dtype=float) for k, v in values.items()}
    first = [int(np.flatnonzero(~np.isnan(v))[0]) for v in outputs.values() if (~np.isnan(v)).any()]
    return {"id": spec.id, "params": p, "outputs": outputs, "warmup": min(first) if first else len(candles)}


def to_json_values(arr: np.ndarray) -> list[float | None]:
    return [None if not np.isfinite(x) else float(x) for x in arr]


def catalog() -> list[dict[str, Any]]:
    return [s.to_dict() for s in SPECS.values()]
