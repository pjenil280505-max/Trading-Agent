"""Data directory resolution and the bootstrap host configuration.

The bootstrap file (``host.toml`` in the data directory) only holds what is
needed before the database opens: bind address, port, remote-access opt-in and
log level. Everything else is a runtime setting stored in the database.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR_NAME = "open-market-os"
DEFAULT_PORT = 8765


def default_data_dir() -> Path:
    override = os.environ.get("OMOS_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "OpenMarketOS"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpenMarketOS"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_DIR_NAME


@dataclass
class HostConfig:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    allow_remote: bool = False  # LAN access is an explicit opt-in
    extra_allowed_hosts: list[str] = field(default_factory=list)  # Host-header allow-list additions
    log_level: str = "INFO"
    tls_certfile: str = ""   # your own certificate for the network listener (default: one made on this computer)
    tls_keyfile: str = ""
    lan_port: int = 0        # port other devices use (0 = this port + 1)
    lan_tls: bool = True     # HTTPS for other devices; turning it off sends their sign-in tokens unencrypted
    data_dir: str = ""

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir) if self.data_dir else default_data_dir()

    @property
    def db_path(self) -> Path:
        return self.data_path / "omos.db"

    @property
    def logs_path(self) -> Path:
        return self.data_path / "logs"

    @property
    def backups_path(self) -> Path:
        return self.data_path / "backups"

    @property
    def exports_path(self) -> Path:
        return self.data_path / "exports"

    @property
    def plugins_path(self) -> Path:
        return self.data_path / "plugins"

    def ensure_dirs(self) -> None:
        for p in (self.data_path, self.logs_path, self.backups_path, self.exports_path, self.plugins_path):
            p.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.data_path, 0o700)

    @property
    def effective_lan_port(self) -> int:
        return self.lan_port or self.port + 1

    def local_bind(self) -> str:
        """The computer's own browser always uses loopback; other devices use the separate network listener."""
        return self.host if self.host in ("127.0.0.1", "localhost", "::1") else "127.0.0.1"

    def lan_bind(self) -> str:
        return self.host if self.host not in ("127.0.0.1", "localhost", "::1", "") else "0.0.0.0"

    def effective_bind(self) -> str:
        """Never bind to a non-loopback address unless remote access was opted into."""
        if not self.allow_remote and self.host not in ("127.0.0.1", "localhost", "::1"):
            return "127.0.0.1"
        return self.host

    def allowed_host_headers(self) -> set[str]:
        hosts = {"127.0.0.1", "localhost", "[::1]"}
        if self.allow_remote:
            hosts.update(h.lower() for h in self.extra_allowed_hosts)
            # Devices on the LAN address this computer by its own IP or name. DNS-rebinding attacks need
            # an attacker-controlled hostname, so this machine's own addresses are safe to accept.
            hosts.update(local_host_names())
            if self.host not in ("0.0.0.0", "::"):
                hosts.add(self.host.lower())
        return hosts


def local_host_names() -> set[str]:
    """This machine's interface addresses (IPv6 in [brackets], as in Host headers) and hostname."""
    import socket

    import psutil

    out: set[str] = set()
    try:
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET:
                    out.add(a.address)
                elif a.family == socket.AF_INET6:
                    out.add(f"[{a.address.split('%')[0].lower()}]")
    except OSError:
        pass
    name = socket.gethostname().lower()
    if name:
        out.update({name, f"{name}.local"})
    return out


def config_file(data_dir: Path | None = None) -> Path:
    return (data_dir or default_data_dir()) / "host.toml"


def load_config(data_dir: Path | None = None) -> HostConfig:
    path = config_file(data_dir)
    cfg = HostConfig(data_dir=str(data_dir) if data_dir else "")
    if path.exists():
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        known = {k: v for k, v in raw.get("host", {}).items() if k in HostConfig.__dataclass_fields__}
        for k, v in known.items():
            setattr(cfg, k, v)
        if data_dir:
            cfg.data_dir = str(data_dir)
    env_port = os.environ.get("OMOS_PORT")
    if env_port:
        cfg.port = int(env_port)
    return cfg


def save_config(cfg: HostConfig) -> Path:
    path = config_file(cfg.data_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Open Market OS host configuration", "[host]"]
    for k, v in asdict(cfg).items():
        lines.append(f"{k} = {_toml_value(v)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _toml_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'
