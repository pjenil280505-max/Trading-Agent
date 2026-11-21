"""``omos`` command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import time
import webbrowser
from pathlib import Path

from omos import __version__
from omos.config import HostConfig, load_config, save_config


def _cfg(args) -> HostConfig:
    data_dir = Path(args.data_dir).expanduser().resolve() if getattr(args, "data_dir", None) else None
    cfg = load_config(data_dir)
    if data_dir:
        cfg.data_dir = str(data_dir)
    return cfg


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host if host not in ("0.0.0.0", "") else "127.0.0.1", port)) == 0


def _bootstrap_url(cfg: HostConfig) -> str:
    from omos.security.auth import AuthService
    from omos.storage.db import Database

    db = Database(cfg.db_path)
    code = AuthService(db).create_bootstrap_code()
    db.close()
    return f"http://127.0.0.1:{cfg.port}/#code={code}"


def cmd_start(args) -> int:
    import uvicorn

    from omos.api.app import create_app
    from omos.host import Host
    from omos.logging_setup import setup_logging

    cfg = _cfg(args)
    if args.port:
        cfg.port = args.port
    if args.allow_remote:
        cfg.allow_remote, cfg.host = True, args.host or "0.0.0.0"
        print("WARNING: remote access enabled. Pair each device with a code (Settings > Devices). "
              "Never expose this port directly to the internet.", file=sys.stderr)
    elif args.host:
        cfg.host = args.host
    cfg.ensure_dirs()
    setup_logging(cfg.log_level, cfg.logs_path)
    if _port_in_use("127.0.0.1", cfg.port):
        print(f"Port {cfg.port} is already in use — is Open Market OS already running? Try 'omos open'.", file=sys.stderr)
        return 2
    if args.demo:
        from omos.storage.db import Database
        db = Database(cfg.db_path)
        prov = db.get_setting("data.providers", {}) or {}
        prov["SIM"] = {"enabled": True}
        db.set_setting("data.providers", prov)
        db.close()
    host = Host(cfg)
    app = create_app(host)
    url = _bootstrap_url(cfg)
    print(f"\n  Open Market OS {__version__} is running.\n  Open this link to sign in (valid 10 minutes, single use):\n\n    {url}\n")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - headless machines have no browser
            pass
    ssl = {}
    if cfg.tls_certfile and cfg.tls_keyfile:
        ssl = {"ssl_certfile": cfg.tls_certfile, "ssl_keyfile": cfg.tls_keyfile}
    try:
        uvicorn.run(app, host=cfg.effective_bind(), port=cfg.port, log_level="warning", access_log=False, **ssl)
    finally:
        host.stop()
    return 0


def cmd_open(args) -> int:
    cfg = _cfg(args)
    if not _port_in_use("127.0.0.1", cfg.port):
        print("The host is not running. Start it with 'omos start'.", file=sys.stderr)
        return 2
    url = _bootstrap_url(cfg)
    print(url)
    if not args.print_only:
        webbrowser.open(url)
    return 0


def cmd_mcp(args) -> int:
    from omos.mcp.server import serve_stdio

    cfg = _cfg(args)
    serve_stdio(cfg.data_path, f"http://127.0.0.1:{cfg.port}")
    return 0


def cmd_status(args) -> int:
    import httpx

    cfg = _cfg(args)
    out = {"version": __version__, "data_dir": str(cfg.data_path), "port": cfg.port,
           "running": _port_in_use("127.0.0.1", cfg.port)}
    if out["running"]:
        try:
            out["health"] = httpx.get(f"http://127.0.0.1:{cfg.port}/api/health", timeout=5, trust_env=False).json()
        except httpx.HTTPError as exc:
            out["health"] = {"error": str(exc)}
    if cfg.db_path.exists():
        from omos.storage.db import Database
        db = Database(cfg.db_path)
        out["heartbeats"] = {r["component"]: r["ts"] for r in db.query("SELECT component, ts FROM heartbeats")}
        out["bots_running"] = db.scalar("SELECT COUNT(*) FROM bots WHERE state = 'running'")
        db.close()
    print(json.dumps(out, indent=2))
    return 0


def cmd_backup(args) -> int:
    from omos.storage.backup import backup_database

    cfg = _cfg(args)
    print(backup_database(cfg.db_path, cfg.backups_path, label="cli"))
    return 0


def cmd_restore(args) -> int:
    from omos.storage.backup import restore_database

    cfg = _cfg(args)
    if _port_in_use("127.0.0.1", cfg.port):
        print("Stop the host before restoring (Ctrl+C in its window, or stop the service).", file=sys.stderr)
        return 2
    safety = restore_database(Path(args.file), cfg.db_path, cfg.backups_path)
    print(f"Restored {args.file}. The previous database was saved as {safety}.")
    return 0


def cmd_export(args) -> int:
    from omos.storage.backup import export_bundle

    cfg = _cfg(args)
    print(export_bundle(cfg.db_path, cfg.exports_path, [cfg.data_path / "host.toml"]))
    return 0


def cmd_token(args) -> int:
    from omos.security.auth import AuthService
    from omos.storage.db import Database

    cfg = _cfg(args)
    db = Database(cfg.db_path)
    tok, info = AuthService(db).create_token(args.name, "cli", args.scopes.split(","), ttl_days=args.days, actor="cli")
    db.close()
    print(json.dumps({"token": tok, "info": info}, indent=2))
    print("Store this token safely; it is shown only once.", file=sys.stderr)
    return 0


def cmd_config(args) -> int:
    cfg = _cfg(args)
    if args.set:
        for kv in args.set:
            k, _, v = kv.partition("=")
            if k not in HostConfig.__dataclass_fields__ or k == "data_dir":
                print(f"unknown setting {k}", file=sys.stderr)
                return 2
            cur = getattr(cfg, k)
            setattr(cfg, k, v.lower() in ("1", "true", "yes") if isinstance(cur, bool) else int(v) if isinstance(cur, int)
                    else v.split(",") if isinstance(cur, list) else v)
        print(save_config(cfg))
    print(json.dumps({k: getattr(cfg, k) for k in HostConfig.__dataclass_fields__}, indent=2))
    return 0


def cmd_service(args) -> int:
    exe = shutil.which("omos") or f"{sys.executable} -m omos"
    cfg = _cfg(args)
    if sys.platform.startswith("linux"):
        unit = f"""[Unit]
Description=Open Market OS host
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={exe} start --no-browser
Restart=on-failure
RestartSec=10
Environment=OMOS_HOME={cfg.data_path}
# If no desktop keyring is available, uncomment and use a strong passphrase (see SECURITY_GUIDE):
# Environment=OMOS_VAULT_PASSPHRASE=...

[Install]
WantedBy=default.target
"""
        target = Path.home() / ".config" / "systemd" / "user" / "open-market-os.service"
        if args.action == "print":
            print(unit)
            return 0
        if args.action == "install":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(unit)
            print(f"Wrote {target}\nEnable it with:\n  systemctl --user daemon-reload\n  systemctl --user enable --now open-market-os\n"
                  "  loginctl enable-linger $USER   # keep running after you log out")
            return 0
        if args.action == "uninstall":
            target.unlink(missing_ok=True)
            print("Removed. Run: systemctl --user daemon-reload")
            return 0
    elif sys.platform == "darwin":
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>org.openmarketos.host</string>
  <key>ProgramArguments</key><array><string>{exe}</string><string>start</string><string>--no-browser</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>EnvironmentVariables</key><dict><key>OMOS_HOME</key><string>{cfg.data_path}</string></dict>
</dict></plist>
"""
        target = Path.home() / "Library" / "LaunchAgents" / "org.openmarketos.host.plist"
        if args.action == "print":
            print(plist)
        elif args.action == "install":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(plist)
            print(f"Wrote {target}\nLoad it with: launchctl load {target}")
        else:
            target.unlink(missing_ok=True)
            print("Removed (run: launchctl unload first if loaded).")
        return 0
    else:
        cmd = f'schtasks /Create /TN "OpenMarketOS" /SC ONLOGON /RL LIMITED /TR "\\"{exe}\\" start --no-browser" /F'
        print("On Windows, run this in a Command Prompt to start the host when you log in:\n\n  " + cmd +
              "\n\nRemove it with: schtasks /Delete /TN \"OpenMarketOS\" /F")
        return 0
    return 0


def cmd_reset(args) -> int:
    cfg = _cfg(args)
    if _port_in_use("127.0.0.1", cfg.port):
        print("Stop the host first.", file=sys.stderr)
        return 2
    if args.confirm != "DELETE ALL LOCAL DATA":
        print('This deletes your database, backups, plugins and secrets vault in\n  '
              f'{cfg.data_path}\nRe-run with: omos reset --confirm "DELETE ALL LOCAL DATA"', file=sys.stderr)
        return 2
    shutil.rmtree(cfg.data_path, ignore_errors=True)
    print(f"Deleted {cfg.data_path}")
    return 0


def cmd_doctor(args) -> int:
    from omos.device.profile import detect
    from omos.security.secrets import KeyringBackend

    cfg = _cfg(args)
    report = {"version": __version__, "python": sys.version.split()[0], "platform": sys.platform,
              "data_dir": str(cfg.data_path), "writable": os.access(cfg.data_path.parent if not cfg.data_path.exists()
                                                                   else cfg.data_path, os.W_OK),
              "os_keyring_usable": KeyringBackend.available(), "port_in_use": _port_in_use("127.0.0.1", cfg.port),
              "device": detect(str(cfg.data_path.parent))}
    from omos.api.app import web_dist
    report["web_ui_built"] = web_dist() is not None
    print(json.dumps(report, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="omos", description="Open Market OS — local-first market platform")
    p.add_argument("--data-dir", help="data directory (default: platform app-data folder or $OMOS_HOME)")
    p.add_argument("--version", action="version", version=f"omos {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start", help="run the host (API, UI, bots, alerts)")
    s.add_argument("--port", type=int)
    s.add_argument("--host", help="bind address (non-loopback requires --allow-remote)")
    s.add_argument("--allow-remote", action="store_true", help="allow paired devices on your network")
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--demo", action="store_true", help="enable the SIMULATED data provider (labelled, not real prices)")
    s.set_defaults(fn=cmd_start)
    o = sub.add_parser("open", help="open the UI with a fresh sign-in link")
    o.add_argument("--print-only", action="store_true")
    o.set_defaults(fn=cmd_open)
    sub.add_parser("mcp", help="run the MCP bridge on stdio (for Claude Desktop/Code etc.)").set_defaults(fn=cmd_mcp)
    sub.add_parser("status", help="show host status").set_defaults(fn=cmd_status)
    sub.add_parser("backup", help="write a database backup").set_defaults(fn=cmd_backup)
    r = sub.add_parser("restore", help="restore a backup (host must be stopped)")
    r.add_argument("file")
    r.set_defaults(fn=cmd_restore)
    sub.add_parser("export", help="export database + config as a zip").set_defaults(fn=cmd_export)
    t = sub.add_parser("token", help="create an API token")
    t.add_argument("--name", default="cli")
    t.add_argument("--scopes", default="read")
    t.add_argument("--days", type=float, default=30)
    t.set_defaults(fn=cmd_token)
    c = sub.add_parser("config", help="show or change host.toml settings")
    c.add_argument("--set", nargs="*", help="key=value pairs, e.g. port=8800 allow_remote=false")
    c.set_defaults(fn=cmd_config)
    sv = sub.add_parser("service", help="run the host at login/boot (systemd, launchd, Task Scheduler)")
    sv.add_argument("action", choices=["install", "uninstall", "print"])
    sv.set_defaults(fn=cmd_service)
    rs = sub.add_parser("reset", help="delete ALL local data")
    rs.add_argument("--confirm", default="")
    rs.set_defaults(fn=cmd_reset)
    sub.add_parser("doctor", help="diagnostics for troubleshooting").set_defaults(fn=cmd_doctor)
    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


_ = time  # imported for type-checkers in some environments
