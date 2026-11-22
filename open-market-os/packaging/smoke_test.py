"""Smoke test of an INSTALLED (or freshly built) app, run on each operating system in CI.

It does what a user does - opens the app from its icon - and then checks that the app really works:
  launcher → background host → healthy → UI served → API answers → restart → quit, plus the CLI.

Usage: python packaging/smoke_test.py --launcher PATH [--cli PATH] [--expect-version X.Y.Z]
Only the Python standard library is used, so any Python 3.9+ can run it.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
FAILURES: list[str] = []


def step(name: str):
    print(f"-- {name}", flush=True)


def check(cond: bool, what: str) -> None:
    print(("   ok   " if cond else "   FAIL ") + what, flush=True)
    if not cond:
        FAILURES.append(what)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(method: str, url: str, token: str | None = None, body: dict | None = None, timeout: float = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with OPENER.open(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def healthy(port: int) -> bool:
    try:
        st, raw = http("GET", f"http://127.0.0.1:{port}/api/health", timeout=3)
        return st == 200 and json.loads(raw).get("status") == "ok"
    except (OSError, ValueError):
        return False


def wait(pred, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.5)
    return False


def run(cmd: list[str], env: dict, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--launcher", required=True, help="the app executable the desktop icon runs")
    ap.add_argument("--cli", help="the 'omos' command (default: the launcher with arguments)")
    ap.add_argument("--expect-version")
    ap.add_argument("--keyring", action="store_true", help="use the OS keyring instead of a passphrase vault")
    ap.add_argument("--data-dir", help="keep data here instead of a temporary folder")
    a = ap.parse_args()
    launcher = [a.launcher]
    cli = [a.cli] if a.cli else launcher
    home = Path(a.data_dir or tempfile.mkdtemp(prefix="omos-smoke-"))
    port = free_port()
    env = {**os.environ, "OMOS_HOME": str(home), "OMOS_NO_BROWSER": "1"}
    if not a.keyring:
        env.update(OMOS_DISABLE_KEYRING="1", OMOS_VAULT_PASSPHRASE="smoke test vault passphrase")
    print(f"data folder {home}, port {port}")

    step("command line")
    r = run([*cli, "--version"], env)
    check(r.returncode == 0, f"--version exits 0 ({r.stdout.strip() or r.stderr.strip()})")
    if a.expect_version:
        check(a.expect_version in r.stdout + r.stderr, f"reports version {a.expect_version}")
    r = run([*cli, "doctor"], env)
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        doc = {}
    check(r.returncode == 0 and doc.get("web_ui_built") is True, "doctor: web UI is bundled")
    check(run([*cli, "config", "--set", f"port={port}"], env).returncode == 0, "config set port")

    step("open the app the way the icon does")
    t0 = time.monotonic()
    r = run(launcher, env, timeout=180)
    check(r.returncode == 0, f"launcher exits 0 after starting the background host ({time.monotonic() - t0:.1f} s) {r.stderr[-500:]}")
    check(healthy(port), "host is healthy")
    st, raw = http("GET", f"http://127.0.0.1:{port}/")
    check(st == 200 and b'<div id="app"' in raw, "web UI is served")
    r = run(launcher, env, timeout=60)
    check(r.returncode == 0 and healthy(port), "opening the app again reuses the running host")

    step("API")
    r = run([*cli, "token", "--scopes", "read,admin,trade:paper", "--days", "1"], env)
    try:
        tok = json.loads(r.stdout)["token"]
    except (ValueError, KeyError):
        tok = ""
        check(False, f"token created ({r.stdout[-300:]} {r.stderr[-300:]})")
    base = f"http://127.0.0.1:{port}/api"
    st, raw = http("GET", f"{base}/system/status", tok)
    status = json.loads(raw) if st == 200 else {}
    check(st == 200 and all(lp["alive"] for lp in status.get("loops", [])), "all background loops alive")
    if a.expect_version:
        check(status.get("version") == a.expect_version, "API reports the expected version")
    st, raw = http("GET", f"{base}/setup", tok)
    check(st == 200 and json.loads(raw)["completed"] is False, "first-run setup assistant pending")
    st, raw = http("POST", f"{base}/strategies/validate", tok, {"source": "plot(sma(close, 20))"})
    check(st in (200, 404), f"strategy validation answers ({st})")
    st, raw = http("GET", f"{base}/indicators", tok)
    check(st == 200 and len(json.loads(raw)) >= 30, "indicator catalog")
    st, raw = http("GET", f"{base}/ai/local", tok)
    check(st == 200 and "recommended" in json.loads(raw), "local AI status and model recommendation")
    st, raw = http("GET", f"{base}/system/diagnostics", tok)
    check(st == 200 and b'"loops"' in raw, "diagnostics report")

    step("start at login (then removed again)")
    r = run([*cli, "autostart", "on"], env)
    on = json.loads(r.stdout or "{}").get("enabled") if r.returncode == 0 else None
    check(on is True, f"autostart on ({r.stdout.strip()[:200]} {r.stderr.strip()[-200:]})")
    r = run([*cli, "autostart", "off"], env)
    check(r.returncode == 0 and json.loads(r.stdout or "{}").get("enabled") is False, "autostart off")

    step("restart from the app")
    st, raw = http("POST", f"{base}/system/restart", tok)
    check(st == 200, "restart accepted")
    went_down = wait(lambda: not healthy(port), 30)
    came_back = wait(lambda: healthy(port), 120)
    check(went_down and came_back, "host restarted on the same port")

    step("quit")
    r = run([*cli, "stop"], env, timeout=90)
    check(r.returncode == 0, f"stop exits 0 ({r.stdout.strip()} {r.stderr.strip()[-300:]})")
    check(wait(lambda: not healthy(port), 30), "host stopped")
    log = home / "logs" / "host-console.log"
    check(log.exists(), "background host wrote its console log")
    check((home / "omos.db").exists() or any(home.glob("*.db")), "database kept in the data folder")
    if FAILURES:
        print("\nSMOKE TEST FAILED:\n  " + "\n  ".join(FAILURES))
        for f in sorted((home / "logs").glob("*.log")):
            print(f"\n==== {f.name} (tail)\n" + f.read_text(errors="replace")[-4000:])
        return 1
    print("\nSMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
