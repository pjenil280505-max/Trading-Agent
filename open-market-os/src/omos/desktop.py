"""Desktop integration, so that nothing needs a terminal.

* ``launch()`` is what the "Open Market OS" icon runs: it starts the host in the background if it is
  not running (no console window), waits until it is healthy, and opens the app in the browser
  already signed in. Clicking the icon again just opens another signed-in window.
* Start-at-login (Windows Run key, macOS LaunchAgent, Linux XDG autostart) — user-level, no admin.
* ``KeepAwake`` stops the computer from sleeping while bots run (display sleep is still allowed).
* ``notify_user`` shows a native message box when something goes wrong before the UI exists.
"""

from __future__ import annotations

import json
import logging
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

APP_NAME = "Open Market OS"
AUTOSTART_ID = "OpenMarketOS"
MAC_AGENT = "org.openmarketos.host"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_command(*args: str) -> list[str]:
    """Command running this installation's CLI, without a console window where the OS has one."""
    if frozen():
        return [sys.executable, *args]  # the app executable accepts CLI arguments
    exe = sys.executable
    if os.name == "nt":
        pyw = Path(exe).with_name("pythonw.exe")
        if pyw.exists():
            exe = str(pyw)
    return [exe, "-m", "omos", *args]


# ---------------------------------------------------------------------------------- host process
def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never send localhost via a proxy


def healthy(port: int, timeout: float = 2.0) -> bool:
    try:
        with _opener().open(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            return r.status == 200 and json.loads(r.read().decode()).get("status") == "ok"
    except (OSError, ValueError):
        return False


def wait_healthy(port: int, timeout: float = 90.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if healthy(port):
            return True
        time.sleep(0.5)
    return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def pick_port(preferred: int) -> int:
    for p in [preferred, *range(8765, 8800)]:
        if port_free(p):
            return p
    raise RuntimeError("no free local port between 8765 and 8799")


def spawn_host(extra: list[str], log_path: Path, global_args: list[str] | None = None) -> subprocess.Popen:
    """Start the host as a detached background process that survives the launcher exiting."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = open(log_path, "ab")  # noqa: SIM115 - handed to the child process
    kw: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": out, "stderr": out, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(app_command("start", "--no-browser", "--managed", *extra), **kw)


def launch(*, open_browser: bool = True, timeout: float = 90.0) -> int:
    """What the desktop icon does."""
    from omos.cli import _apply_runtime, _bootstrap_url, _cfg
    from omos.config import save_config

    class _A:  # minimal args object for _cfg
        data_dir = None

    cfg = _apply_runtime(_cfg(_A()))
    cfg.ensure_dirs()
    if not healthy(cfg.port):
        if not port_free(cfg.port):  # held by another program: move to a free port and remember it
            new = pick_port(cfg.port + 1)
            log.warning("port %s is used by another program; using %s", cfg.port, new)
            cfg.port = new
            save_config(cfg)
        spawn_host([], cfg.logs_path / "host-console.log")
        if not wait_healthy(cfg.port, timeout):
            notify_user(APP_NAME, "Open Market OS could not start.\n\nDetails are in:\n"
                        f"{cfg.logs_path / 'host-console.log'}\n\nTry again, or see Troubleshooting in the user guide.")
            return 1
    url = _bootstrap_url(cfg)
    if open_browser and not os.environ.get("OMOS_NO_BROWSER"):
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - no browser available
            notify_user(APP_NAME, f"Open this address in your browser:\n{url}")
    return 0


def entry() -> int:
    """Entry point of the windowed app executable: no arguments = launcher, otherwise the CLI."""
    if len(sys.argv) > 1:
        from omos.cli import main
        return main()
    try:
        return launch()
    except Exception as exc:  # noqa: BLE001 - last-resort message for a GUI user
        notify_user(APP_NAME, f"Open Market OS could not start: {exc}")
        return 1


# ---------------------------------------------------------------------------------- user messages
def notify_user(title: str, message: str) -> None:
    log.error("%s: %s", title, message)
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            script = f'display dialog {json.dumps(message)} with title {json.dumps(title)} buttons {{"OK"}}'
            subprocess.run(["osascript", "-e", script], timeout=120, check=False)
        else:
            for tool, argv in (("zenity", ["--info", "--title", title, "--text", message]),
                               ("kdialog", ["--title", title, "--msgbox", message]),
                               ("notify-send", [title, message])):
                if shutil.which(tool):
                    subprocess.run([tool, *argv], timeout=120, check=False)
                    break
    except Exception:  # noqa: BLE001 - message boxes are best effort
        log.debug("could not show a message box", exc_info=True)


# ---------------------------------------------------------------------------------- start at login
def _home() -> Path:
    return Path.home()


def _autostart_cmd() -> list[str]:
    return app_command("start", "--no-browser", "--managed")


def _env_block() -> dict[str, str]:
    return {"OMOS_HOME": os.environ["OMOS_HOME"]} if os.environ.get("OMOS_HOME") else {}


def get_autostart() -> dict[str, Any]:
    try:
        if os.name == "nt":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                try:
                    val, _ = winreg.QueryValueEx(k, AUTOSTART_ID)
                    return {"supported": True, "enabled": True, "method": "Windows sign-in (Run key)", "command": val}
                except FileNotFoundError:
                    return {"supported": True, "enabled": False, "method": "Windows sign-in (Run key)"}
        if sys.platform == "darwin":
            p = _home() / "Library" / "LaunchAgents" / f"{MAC_AGENT}.plist"
            return {"supported": True, "enabled": p.exists(), "method": "macOS login (LaunchAgent)", "path": str(p)}
        p = _home() / ".config" / "autostart" / "open-market-os.desktop"
        return {"supported": True, "enabled": p.exists(), "method": "desktop login (XDG autostart)", "path": str(p),
                "note": "Runs when you log in to a desktop session. Headless servers: use 'omos service install'."}
    except Exception as exc:  # noqa: BLE001
        return {"supported": False, "enabled": False, "error": str(exc)}


def set_autostart(enabled: bool) -> dict[str, Any]:
    cmd = _autostart_cmd()
    if os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, AUTOSTART_ID, 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_ID)
                except FileNotFoundError:
                    pass
    elif sys.platform == "darwin":
        p = _home() / "Library" / "LaunchAgents" / f"{MAC_AGENT}.plist"
        if enabled:
            p.parent.mkdir(parents=True, exist_ok=True)
            plist = {"Label": MAC_AGENT, "ProgramArguments": cmd, "RunAtLoad": True,
                     "KeepAlive": {"SuccessfulExit": False}, "ProcessType": "Interactive"}
            if _env_block():
                plist["EnvironmentVariables"] = _env_block()
            p.write_bytes(plistlib.dumps(plist))
        else:
            p.unlink(missing_ok=True)
    else:
        p = _home() / ".config" / "autostart" / "open-market-os.desktop"
        if enabled:
            p.parent.mkdir(parents=True, exist_ok=True)
            env = "".join(f"{k}={v} " for k, v in _env_block().items())
            exec_line = ("env " + env if env else "") + " ".join(_quote_desktop(a) for a in cmd)
            p.write_text("[Desktop Entry]\nType=Application\nName=Open Market OS (background)\n"
                         f"Exec={exec_line}\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\nTerminal=false\n")
        else:
            p.unlink(missing_ok=True)
    return get_autostart()


def _quote_desktop(arg: str) -> str:
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"' if any(c in arg for c in ' "\\') else arg


# ---------------------------------------------------------------------------------- keep awake
class KeepAwake:
    """Prevents idle system sleep while active (the display may still turn off)."""

    def __init__(self) -> None:
        self.active = False
        self.method = "none"
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set(self, on: bool) -> None:
        if on == self.active:
            return
        try:
            (self._on if on else self._off)()
        except Exception:
            log.exception("keep-awake change failed")

    def _on(self) -> None:
        if os.name == "nt":
            self._stop.clear()
            self._thread = threading.Thread(target=self._win_hold, name="keep-awake", daemon=True)
            self._thread.start()
            self.method, self.active = "SetThreadExecutionState", True
        elif sys.platform == "darwin" and shutil.which("caffeinate"):
            self._proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            self.method, self.active = "caffeinate", True
        elif shutil.which("systemd-inhibit"):
            self._proc = subprocess.Popen(
                ["systemd-inhibit", "--what=sleep:idle", f"--who={APP_NAME}", "--why=Trading bots are running",
                 "--mode=block", "sleep", "infinity"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.3)
            if self._proc.poll() is not None:  # no logind (containers, minimal systems)
                self._proc, self.method = None, "unsupported"
                return
            self.method, self.active = "systemd-inhibit", True
        else:
            self.method = "unsupported"

    def _win_hold(self) -> None:
        import ctypes
        es_continuous, es_system_required = 0x80000000, 0x00000001
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        k32.SetThreadExecutionState(es_continuous | es_system_required)
        self._stop.wait()
        k32.SetThreadExecutionState(es_continuous)

    def _off(self) -> None:
        self._stop.set()
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        self.active = False

    def status(self) -> dict[str, Any]:
        return {"active": self.active, "method": self.method}
