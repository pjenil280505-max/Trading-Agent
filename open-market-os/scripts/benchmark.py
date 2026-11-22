"""Reproducible performance benchmarks for Open Market OS.

    python scripts/benchmark.py [--out benchmarks.json] [--quick] [--no-ui]

* Compute benchmarks (indicators, OMS Script, backtester, optimiser) run on a synthetic random-walk
  OHLCV series with a fixed seed, so they do not depend on the network or on market hours.
* API latency is measured over real HTTP (uvicorn on 127.0.0.1) against the full app stack
  (middleware, auth, JSON) using SIMULATED demo data, after one warm-up request per endpoint.
* UI timings (optional) use Node + Playwright + Chromium against the same server.
Numbers depend heavily on the machine; the JSON records the hardware they came from.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def synthetic(n: int, seed: int = 1):
    from omos.marketdata.models import Candles, DataStatus

    rng = np.random.default_rng(seed)
    ts = 1_600_000_000_000 + np.arange(n, dtype=np.int64) * 3_600_000
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
    vol = rng.uniform(100, 1000, n)
    rows = [[int(t), float(o), float(h), float(lo), float(c), float(v)] for t, o, h, lo, c, v in zip(ts, open_, high, low, close, vol)]
    return Candles.from_rows(rows, instrument_id="BENCH:X", timeframe="1h", status=DataStatus.SIMULATED, source="bench")


def timeit(fn, repeat: int = 5, warmup: int = 1) -> dict:
    for _ in range(warmup):
        fn()
    xs = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000)
    return {"median_ms": round(statistics.median(xs), 2), "min_ms": round(min(xs), 2), "max_ms": round(max(xs), 2), "runs": repeat}


def machine() -> dict:
    cpu = platform.processor() or ""
    try:
        cpu = next(line.split(":", 1)[1].strip() for line in open("/proc/cpuinfo") if line.startswith("model name"))
    except (OSError, StopIteration):
        pass
    import psutil

    return {"cpu": cpu, "logical_cpus": os.cpu_count(), "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
            "os": f"{platform.system()} {platform.release()}", "python": platform.python_version(),
            "numpy": np.__version__}


def bench_compute(quick: bool) -> dict:
    from omos.backtest.engine import backtest_script
    from omos.backtest.optimize import monte_carlo, optimize, walk_forward
    from omos.backtest.strategies import TEMPLATES
    from omos.indicators.registry import SPECS, compute
    from omos.script.lang import run

    out: dict = {}
    sizes = [10_000] if quick else [10_000, 50_000]
    for n in sizes:
        c = synthetic(n)
        per = {sid: timeit(lambda sid=sid: compute(sid, c), repeat=3 if quick else 5)["median_ms"] for sid in SPECS}
        out[f"indicators_{n}_bars"] = {"each_median_ms": per, "all_total_ms": round(sum(per.values()), 1),
                                       "slowest": sorted(per.items(), key=lambda kv: -kv[1])[:5]}
    src = TEMPLATES["ema_cross"]["source"]
    for n in sizes:
        c = synthetic(n)
        out[f"script_eval_ema_cross_{n}_bars"] = timeit(lambda c=c: run(src, c), repeat=5)
        out[f"backtest_ema_cross_{n}_bars"] = timeit(lambda c=c: backtest_script(src, c), repeat=5)
    c5 = synthetic(5_000)
    t0 = time.perf_counter()
    opt = optimize(src, c5)
    out["optimise_ema_cross_5000_bars"] = {"combinations": opt["combinations"], "total_ms": round((time.perf_counter() - t0) * 1000, 1)}
    out["optimise_ema_cross_5000_bars"]["per_backtest_ms"] = round(out["optimise_ema_cross_5000_bars"]["total_ms"] / opt["combinations"], 2)
    if not quick:
        t0 = time.perf_counter()
        wf = walk_forward(src, c5, folds=4)
        out["walk_forward_4_folds_5000_bars"] = {"total_ms": round((time.perf_counter() - t0) * 1000, 1), "folds": len(wf["folds"])}
    trades = backtest_script(src, synthetic(10_000)).trades
    out["monte_carlo_1000_runs"] = {"trades": len(trades), **timeit(lambda: monte_carlo(trades, 10_000, runs=1000), repeat=3)}
    return out


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def bench_api_and_ui(quick: bool, ui: bool) -> dict:
    import httpx
    import uvicorn

    from omos.api.app import create_app
    from omos.backtest.strategies import TEMPLATES
    from omos.config import HostConfig
    from omos.host import Host
    from omos.marketdata.providers.local import SimulatedProvider

    os.environ.setdefault("OMOS_DISABLE_KEYRING", "1")
    os.environ.setdefault("OMOS_VAULT_PASSPHRASE", "benchmark only")
    tmp = tempfile.mkdtemp(prefix="omos-bench-")
    port = free_port()
    host = Host(HostConfig(data_dir=tmp, port=port), start_loops=False, providers={"SIM": SimulatedProvider()})
    server = uvicorn.Server(uvicorn.Config(create_app(host), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{base}/api/health", timeout=1, trust_env=False)
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    tok = httpx.post(f"{base}/api/auth/exchange", json={"code": host.auth.create_bootstrap_code(), "device_name": "bench"},
                     trust_env=False).json()["token"]
    cli = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {tok}"}, trust_env=False, timeout=60)
    wl = cli.post("/api/watchlists", json={"name": "bench"}).json()
    for s in ("SIM:DEMO-TREND", "SIM:DEMO-RANGE", "SIM:DEMO-VOLATILE"):
        cli.post(f"/api/watchlists/{wl['id']}/items", json={"instrument_id": s})
    calls = {
        "GET /api/health": lambda: cli.get("/api/health"),
        "GET /api/candles (1,000 bars, cached)": lambda: cli.get("/api/candles", params={"instrument": "SIM:DEMO-TREND", "tf": "1h", "limit": 1000}),
        "GET /api/candles (5,000 bars, cached)": lambda: cli.get("/api/candles", params={"instrument": "SIM:DEMO-TREND", "tf": "1h", "limit": 5000}),
        "POST /api/indicators/compute (RSI, 1,000 bars)": lambda: cli.post("/api/indicators/compute", json={"instrument": "SIM:DEMO-TREND", "tf": "1h", "id": "rsi", "limit": 1000}),
        "GET /api/watchlists/{id}/quotes (3 symbols)": lambda: cli.get(f"/api/watchlists/{wl['id']}/quotes", params={"tf": "1h"}),
        "POST /api/screener/run (3 symbols)": lambda: cli.post("/api/screener/run", json={"definition": {"universe": {"watchlist_id": wl["id"]}, "timeframe": "1h", "filter": {"op": "and", "rules": [{"field": "rsi14", "cmp": ">", "value": 0}]}}}),
        "POST /api/backtest (EMA cross, 2,000 bars)": lambda: cli.post("/api/backtest", json={"source": TEMPLATES["ema_cross"]["source"], "instrument": "SIM:DEMO-TREND", "tf": "1h", "limit": 2000, "save": False}),
        "GET /api/accounts/{id}/summary": lambda: cli.get("/api/accounts/1/summary"),
    }
    api: dict = {}
    n = 10 if quick else 30
    for name, fn in calls.items():
        r = fn()
        assert r.status_code == 200, (name, r.status_code, r.text[:200])
        xs = []
        for _ in range(n):
            t0 = time.perf_counter()
            fn()
            xs.append((time.perf_counter() - t0) * 1000)
        xs.sort()
        api[name] = {"p50_ms": round(xs[len(xs) // 2], 2), "p95_ms": round(xs[min(len(xs) - 1, int(len(xs) * 0.95))], 2), "requests": n}
    result: dict = {"api_latency_http": api}

    dist = ROOT / "src" / "omos" / "web" / "dist" / "assets"
    if dist.exists():
        import gzip
        assets = {f.name: {"bytes": f.stat().st_size, "gzip_bytes": len(gzip.compress(f.read_bytes(), 9))} for f in dist.iterdir()}
        result["ui_bundle"] = assets
    if ui:
        script = ROOT / "scripts" / "benchmark_ui.cjs"
        url = f"{base}/#code={host.auth.create_bootstrap_code()}"
        try:
            p = subprocess.run(["node", str(script)], env={**os.environ, "URL": url}, capture_output=True, text=True, timeout=180)
            result["ui_timings"] = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 else {"error": p.stderr[-500:]}
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as exc:
            result["ui_timings"] = {"error": f"not measured: {exc}"}
    server.should_exit = True
    th.join(timeout=10)
    host.db.close()
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "benchmarks.json"))
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-ui", action="store_true")
    a = ap.parse_args()
    from omos import __version__

    res = {"version": __version__, "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "machine": machine(),
           "compute": bench_compute(a.quick)}
    res.update(bench_api_and_ui(a.quick, not a.no_ui))
    Path(a.out).write_text(json.dumps(res, indent=2))
    print(json.dumps({k: res[k] for k in ("version", "machine")}, indent=2))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
