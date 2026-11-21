"""Hardware detection and adaptive defaults.

The UI also reports screen size and touch support; the host combines that with what it can
measure (CPU, RAM, disk, battery, GPU presence) to pick defaults that will not overload a
small device. Users can override every default in Settings.
"""

from __future__ import annotations

import os
import platform
import shutil
from typing import Any

import psutil


def detect(data_dir: str | None = None) -> dict[str, Any]:
    vm = psutil.virtual_memory()
    du = shutil.disk_usage(data_dir or os.getcwd())
    try:
        batt = psutil.sensors_battery()
    except (AttributeError, NotImplementedError, OSError):
        batt = None
    gpu = {"nvidia": shutil.which("nvidia-smi") is not None}
    info = {
        "os": platform.system(), "os_release": platform.release(), "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_logical": psutil.cpu_count(logical=True) or 1, "cpu_physical": psutil.cpu_count(logical=False) or 1,
        "ram_total_gb": round(vm.total / 2 ** 30, 2), "ram_available_gb": round(vm.available / 2 ** 30, 2),
        "disk_free_gb": round(du.free / 2 ** 30, 2),
        "battery": None if batt is None else {"percent": batt.percent, "plugged_in": batt.power_plugged},
        "gpu": gpu, "android": "ANDROID_ROOT" in os.environ or "com.termux" in os.environ.get("PREFIX", ""),
    }
    info["profile"] = profile(info)
    return info


def profile(info: dict[str, Any]) -> dict[str, Any]:
    ram, cpus = info["ram_total_gb"], info["cpu_logical"]
    on_battery = bool(info.get("battery")) and not info["battery"].get("plugged_in", True)
    if ram < 3 or cpus <= 2 or info.get("android"):
        tier = "low"
    elif ram < 12 or cpus < 8:
        tier = "medium"
    else:
        tier = "high"
    defaults = {
        "low": {"chart_bars": 1500, "max_charts": 2, "workers": 1, "poll_seconds": 30, "screener_universe": 30,
                "ai_model_hint": "use a hosted provider or a ~0.5B local model (e.g. qwen2.5:0.5b)"},
        "medium": {"chart_bars": 5000, "max_charts": 4, "workers": 2, "poll_seconds": 15, "screener_universe": 100,
                   "ai_model_hint": "local 1.5B–3B model at 4-bit (e.g. qwen2.5:3b)"},
        "high": {"chart_bars": 20000, "max_charts": 8, "workers": 4, "poll_seconds": 5, "screener_universe": 200,
                 "ai_model_hint": "local 7B–8B model at 4-bit, or larger with a GPU"},
    }[tier]
    if on_battery:
        defaults = {**defaults, "poll_seconds": max(defaults["poll_seconds"], 30), "workers": 1}
    return {"tier": tier, "on_battery": on_battery, **defaults}
