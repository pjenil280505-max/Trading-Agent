from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from omos.marketdata.models import Candles, DataStatus  # noqa: E402
from omos.storage.db import Database  # noqa: E402


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OMOS_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


def make_candles(closes, *, start=1_700_000_000_000, step=60_000, tf="1m", instrument="TEST:X",
                 spread=0.5, volume=100.0) -> Candles:
    closes = np.asarray(closes, dtype=float)
    opens = np.r_[closes[0], closes[:-1]]
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    ts = start + step * np.arange(len(closes), dtype=np.int64)
    return Candles(ts=ts, open=opens, high=highs, low=lows, close=closes, volume=np.full(len(closes), volume),
                   instrument_id=instrument, timeframe=tf, status=DataStatus.HISTORICAL, source="test")


@pytest.fixture
def candles_factory():
    return make_candles


def random_walk(n=500, seed=7, start_price=100.0):
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 1, n)
    return start_price + np.cumsum(steps)


@pytest.fixture
def ohlcv():
    """Realistic random OHLCV for indicator tests."""
    rng = np.random.default_rng(42)
    n = 600
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    close = np.maximum(close, 5)
    open_ = np.r_[close[0], close[:-1]] + rng.normal(0, 0.2, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.6, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.6, n))
    vol = rng.integers(1_000, 10_000, n).astype(float)
    ts = 1_700_000_000_000 + 60_000 * np.arange(n, dtype=np.int64)
    return Candles(ts=ts, open=open_, high=high, low=low, close=close, volume=vol, instrument_id="TEST:R",
                   timeframe="1m", status=DataStatus.HISTORICAL, source="test")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("OMOS_RUN_NETWORK") == "1":
        return
    skip = pytest.mark.skip(reason="network test; set OMOS_RUN_NETWORK=1 to run")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)
