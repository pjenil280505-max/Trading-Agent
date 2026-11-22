"""App lifecycle, first-run setup, background jobs and the managed local AI.

Everything a user would otherwise do in a terminal: quit, restart, start at login, keep awake,
install and run a local AI model.
"""

from __future__ import annotations

import platform
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Body, Depends, Request

from omos import __version__, desktop
from omos.ai.local import recommend_model
from omos.api.deps import auth
from omos.errors import OmosError, ValidationError
from omos.util import now_ms

router = APIRouter(prefix="/api")
READ, ADMIN = auth("read"), auth("admin")

# one instrument per default source, used only for the first-run connectivity check
SOURCE_PROBES = [
    ("OKX", "OKX:BTC-USDT", "1h", "Crypto prices (OKX)"),
    ("COINBASE", "COINBASE:BTC-USD", "1h", "Crypto prices (Coinbase)"),
    ("KRAKEN", "KRAKEN:XBTUSD", "1h", "Crypto prices (Kraken)"),
    ("BINANCE", "BINANCE:BTCUSDT", "1h", "Crypto prices (Binance)"),
    ("YAHOO", "YAHOO:SPY", "1d", "Stocks, ETFs, indices, FX (Yahoo, delayed)"),
    ("ECB", "ECB:EURUSD", "1d", "Euro FX reference rates (ECB, daily)"),
]


def H(request: Request):
    return request.app.state.host


def _server(request: Request):
    srv = getattr(request.app.state, "server", None)
    if srv is None:
        raise ValidationError("this host was not started by the app, so it cannot quit or restart itself")
    return srv


def _exit_soon(server, delay: float = 0.6) -> None:
    def go() -> None:
        time.sleep(delay)  # let the HTTP response reach the browser first
        server.should_exit = True
    threading.Thread(target=go, name="omos-exit", daemon=True).start()


# ====================================================================== lifecycle
@router.post("/system/shutdown")
def shutdown(request: Request, info=Depends(ADMIN)):
    h = H(request)
    srv = _server(request)
    h.db.audit(info.actor, "system.shutdown", "host", {"running_bots": h.running_bots()})
    _exit_soon(srv)
    return {"ok": True, "message": "Open Market OS is shutting down. Running bots stop until it starts again.",
            "running_bots": h.running_bots()}


def _restart(request: Request, info) -> None:
    h = H(request)
    srv = _server(request)
    cfg = h.cfg
    extra = ["--wait-port", "--port", str(cfg.port)]
    if cfg.allow_remote:
        extra.append("--allow-remote")
    desktop.spawn_host(extra, cfg.logs_path / "host-console.log", global_args=["--data-dir", str(cfg.data_path)])
    h.db.audit(info.actor, "system.restart", "host", {})
    _exit_soon(srv)


@router.post("/system/restart")
def restart(request: Request, info=Depends(ADMIN)):
    _restart(request, info)
    return {"ok": True, "message": "Restarting. The page reconnects by itself in a few seconds."}


def lan_info(request: Request) -> dict[str, Any]:
    h = H(request)
    live = getattr(request.app.state, "lan", None)
    return {"enabled": bool(h.cfg.allow_remote), "active": bool(live), "port": (live or {}).get("port"),
            "tls": bool((live or {}).get("tls")), "fingerprint": (live or {}).get("fingerprint"),
            "fingerprint_short": (live or {}).get("fingerprint_short"), "urls": (live or {}).get("urls", []),
            "restart_needed": bool(h.cfg.allow_remote) != bool(live)}


@router.get("/system/lan")
def get_lan(request: Request, info=Depends(READ)):
    return lan_info(request)


@router.put("/system/lan")
def put_lan(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    """Allow or stop access from other devices on this network (saved, then the app restarts)."""
    from pathlib import Path

    from omos.config import load_config, save_config

    h = H(request)
    enabled = bool(payload.get("enabled"))
    saved = load_config(Path(h.cfg.data_dir) if h.cfg.data_dir else None)
    saved.allow_remote = enabled
    save_config(saved)
    h.cfg.allow_remote = enabled
    h.db.audit(info.actor, "system.lan", "host", {"enabled": enabled})
    if getattr(request.app.state, "server", None) is None:
        return {**lan_info(request), "restarting": False}
    _restart(request, info)
    return {**lan_info(request), "restarting": True}


@router.get("/system/autostart")
def get_autostart(info=Depends(READ)):
    return desktop.get_autostart()


@router.put("/system/autostart")
def put_autostart(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    enabled = bool(payload.get("enabled"))
    try:
        out = desktop.set_autostart(enabled)
    except OSError as exc:
        raise OmosError(f"could not change start-at-login: {exc}") from exc
    H(request).db.audit(info.actor, "system.autostart", "host", {"enabled": enabled})
    return out


@router.get("/system/power")
def get_power(request: Request, info=Depends(READ)):
    h = H(request)
    return {**h.keep_awake.status(), "enabled": bool(h.db.get_setting("power.keep_awake", True)),
            "running_bots": h.running_bots()}


@router.put("/system/power")
def put_power(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    h = H(request)
    h.db.set_setting("power.keep_awake", bool(payload.get("keep_awake")))
    h._power()
    return get_power(request, info)


@router.get("/system/diagnostics")
def diagnostics(request: Request, info=Depends(ADMIN)):
    """What `omos doctor` printed, plus recent warnings and errors: a file the user can attach to a bug report."""
    import json
    import sys

    from fastapi.responses import Response

    from omos.api.app import web_dist
    from omos.security.secrets import KeyringBackend

    h = H(request)
    log_lines: list[str] = []
    log_file = h.cfg.logs_path / "omos.log"
    try:
        with log_file.open("r", encoding="utf-8", errors="replace") as f:
            log_lines = [ln.rstrip() for ln in f.readlines()[-5000:] if " WARNING " in ln or " ERROR " in ln][-200:]
    except OSError:
        pass
    st = h.status()
    report = {"generated_at": now_ms(), "version": __version__, "python": sys.version.split()[0], "platform": sys.platform,
              "packaged_app": desktop.frozen(), "data_dir": str(h.cfg.data_path),
              "os_keyring_usable": KeyringBackend.available(), "web_ui_built": web_dist() is not None,
              "device": h.device, "loops": st["loops"], "providers": st["providers"], "schema_version": st["schema_version"],
              "keep_awake": st["keep_awake"], "autostart": desktop.get_autostart(), "local_ai": h.local_ai.status(),
              "recent_warnings_and_errors": log_lines}
    name = f"omos-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.json"
    return Response(json.dumps(report, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ====================================================================== first-run setup
def _system_summary(h) -> dict[str, Any]:
    d = h.device
    return {"os": f"{platform.system()} {platform.release()}", "machine": platform.machine(),
            "cpu_count": d.get("cpu_logical"), "ram_total_gb": d.get("ram_total_gb"), "disk_free_gb": d.get("disk_free_gb"),
            "gpu": d.get("gpu"), "tier": (d.get("profile") or {}).get("tier"),
            "poll_seconds": (d.get("profile") or {}).get("poll_seconds"), "data_dir": str(h.cfg.data_path),
            "version": __version__, "packaged": desktop.frozen()}


@router.get("/setup")
def setup_state(request: Request, info=Depends(READ)):
    h = H(request)
    return {"completed": bool(h.db.get_setting("setup.completed", False)),
            "completed_at": h.db.get_setting("setup.completed_at"),
            "system": _system_summary(h), "ai": recommend_model(h.device),
            "autostart": desktop.get_autostart(), "keep_awake": bool(h.db.get_setting("power.keep_awake", True))}


@router.post("/setup/check_sources")
def check_sources(request: Request, info=Depends(READ)):
    h = H(request)
    enabled = set(h.market.providers)

    def probe(item: tuple[str, str, str, str]) -> dict[str, Any]:
        prov, iid, tf, label = item
        out: dict[str, Any] = {"provider": prov, "label": label, "instrument": iid}
        if prov not in enabled:
            return {**out, "ok": False, "status": "DISABLED", "detail": "turned off in Settings > Data"}
        t0 = time.monotonic()
        try:
            c = h.market.get_candles(iid, tf, limit=5, max_age_s=0)
            out.update(ok=len(c) > 0, status=c.status.value, detail=f"{len(c)} bars from {c.source}")
        except OmosError as exc:
            out.update(ok=False, status="UNAVAILABLE", detail=exc.message[:200])
        out["latency_ms"] = round((time.monotonic() - t0) * 1000)
        return out

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(probe, SOURCE_PROBES))
    return {"checked_at": now_ms(), "results": results, "ok": sum(r["ok"] for r in results),
            "total": len(results)}


@router.post("/setup/complete")
def complete_setup(request: Request, payload: dict[str, Any] = Body(default={}), info=Depends(ADMIN)):
    h = H(request)
    applied: dict[str, Any] = {}
    if "autostart" in payload:
        try:
            applied["autostart"] = desktop.set_autostart(bool(payload["autostart"]))
        except OSError as exc:
            applied["autostart"] = {"error": str(exc)}
    if "keep_awake" in payload:
        h.db.set_setting("power.keep_awake", bool(payload["keep_awake"]))
        applied["keep_awake"] = bool(payload["keep_awake"])
    h.db.set_setting("setup.completed", True)
    h.db.set_setting("setup.completed_at", now_ms())
    h.db.audit(info.actor, "setup.complete", "host", {k: v for k, v in payload.items() if k in ("autostart", "keep_awake")})
    return {"completed": True, "applied": applied}


@router.post("/setup/reset")
def reset_setup(request: Request, info=Depends(ADMIN)):
    H(request).db.set_setting("setup.completed", False)
    return {"completed": False}


# ====================================================================== jobs
@router.get("/jobs")
def jobs(request: Request, info=Depends(READ)):
    return H(request).jobs.list()


@router.get("/jobs/{job_id}")
def job(job_id: str, request: Request, info=Depends(READ)):
    return H(request).jobs.get(job_id).to_dict()


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, info=Depends(ADMIN)):
    return H(request).jobs.cancel(job_id)


# ====================================================================== local AI
@router.get("/ai/local")
def local_status(request: Request, info=Depends(READ)):
    h = H(request)
    st = h.local_ai.status(h.device)
    st["managed_enabled"] = bool(h.db.get_setting("ai.local.managed", False))
    st["runtime_version"] = h.local_ai.installed_version()
    st["models_dir"] = str(h.local_ai.models_dir)
    st["running_job"] = next((j for j in h.jobs.list() if j["kind"] == "local_ai" and j["status"] == "running"), None)
    return st


def _active_port(h) -> int:
    st = h.local_ai.status()
    if st["system"]:
        return st["system"]["port"]
    if st["managed"]:
        return st["managed"]["port"]
    raise ValidationError("the local AI runtime is not running; set it up or start it first")


@router.post("/ai/local/setup")
def local_setup(request: Request, payload: dict[str, Any] = Body(default={}), info=Depends(ADMIN)):
    h = H(request)
    model = str(payload.get("model") or recommend_model(h.device)["model"])

    def run(job):
        res = h.local_ai.setup(model, h.ai, job)
        if res["port"] == h.local_ai.managed_port:
            h.db.set_setting("ai.local.managed", True)
        h.db.audit(info.actor, "ai.local.setup", model, {"runtime": res["runtime"]})
        h.notifier.notify("Local AI is ready", f"{model} runs on this computer through {res['runtime']}. "
                          "It is free and your data stays here.", category="ai")
        return res

    return h.jobs.start("local_ai", f"Set up local AI ({model})", run).to_dict()


@router.post("/ai/local/pull")
def local_pull(request: Request, payload: dict[str, Any] = Body(...), info=Depends(ADMIN)):
    h = H(request)
    model = str(payload.get("model") or "")
    port = _active_port(h)
    return h.jobs.start("local_ai", f"Download model {model}", lambda job: h.local_ai.pull(port, model, job) or
                        {"model": model}).to_dict()


@router.delete("/ai/local/models/{model:path}")
def local_delete(model: str, request: Request, info=Depends(ADMIN)):
    h = H(request)
    h.local_ai.delete_model(_active_port(h), model)
    h.db.audit(info.actor, "ai.local.delete_model", model, {})
    return {"deleted": model}


@router.post("/ai/local/start")
def local_start(request: Request, info=Depends(ADMIN)):
    h = H(request)
    st = h.local_ai.start_managed()
    h.db.set_setting("ai.local.managed", True)
    return st


@router.post("/ai/local/stop")
def local_stop(request: Request, info=Depends(ADMIN)):
    h = H(request)
    h.local_ai.stop_managed()
    h.db.set_setting("ai.local.managed", False)
    return {"stopped": True}
