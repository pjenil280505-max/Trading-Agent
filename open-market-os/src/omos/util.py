"""Small shared helpers: clocks, ids, JSON."""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any


def now_ms() -> int:
    """Current UTC time in epoch milliseconds."""
    return int(time.time() * 1000)


def iso(ts_ms: int | None) -> str | None:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def utc_day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def utc_week(ts_ms: int) -> str:
    y, w, _ = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isocalendar()
    return f"{y}-W{w:02d}"


def new_id(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_hex(8)}"


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), default=_default)


def loads(text: str | None, default: Any = None) -> Any:
    if text is None or text == "":
        return default
    return json.loads(text)


def _default(o: Any) -> Any:
    # numpy scalars / arrays, dataclasses and enums end up here
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "value"):
        return o.value
    if hasattr(o, "__dataclass_fields__"):
        return {k: getattr(o, k) for k in o.__dataclass_fields__}
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
