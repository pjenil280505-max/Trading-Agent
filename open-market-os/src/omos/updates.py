"""Updates without a terminal: check, download, verify, back up, install.

* **Check:** the newest *published* release of the project's GitHub repository (drafts and
  pre-releases are ignored) is compared with this version.
* **Download:** only the installer for this computer, and only if its SHA-256 matches the
  ``SHA256SUMS.txt`` published in the same release. This detects damaged or swapped downloads; it
  cannot detect a compromised repository (the installers are not code-signed).
* **Install:** a backup of all data is taken first ("before an update" in Settings > Backups).
  Windows: the installer runs silently, stops the app, updates it and starts it again.
  AppImage: the file replaces itself, then the app restarts. macOS and Debian packages: the
  downloaded file is opened for you (drag to Applications / the package installer).
* **Rollback:** restore the "before an update" backup, and install the previous version from the
  release page.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from omos import __version__
from omos.errors import OmosError, PermissionDenied, ValidationError

DEFAULT_REPO = "pjenil280505-max/Trading-Agent"
API = "https://api.github.com"


def parse_version(v: str) -> tuple[int, ...]:
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", v.strip())
    if not m:
        raise ValidationError(f"not a version: {v}")
    return tuple(int(x) for x in m.groups())


def asset_pattern() -> re.Pattern:
    """Which release file updates THIS installation."""
    mach = platform.machine().lower()
    if sys.platform == "win32":
        return re.compile(r"-windows-x64-setup\.exe$")
    if sys.platform == "darwin":
        return re.compile(r"-macos-arm64\.dmg$" if mach in ("arm64", "aarch64") else r"-macos-x86_64\.dmg$")
    arch = "aarch64" if mach in ("arm64", "aarch64") else "x86_64"
    if os.environ.get("APPIMAGE"):
        return re.compile(rf"-{arch}\.AppImage$")
    if Path(sys.executable).resolve().parts[:3] == ("/", "opt", "open-market-os"):
        return re.compile(rf"_{'arm64' if arch == 'aarch64' else 'amd64'}\.deb$")
    return re.compile(rf"-linux-{arch}\.tar\.gz$")


class Updater:
    def __init__(self, data_path: Path, *, repo: str = DEFAULT_REPO, api: str = API,
                 client: httpx.Client | None = None, current: str = __version__):
        self.dir = data_path / "updates"
        self.repo, self.api, self.current = repo, api.rstrip("/"), current
        self.client = client or httpx.Client(timeout=httpx.Timeout(20.0, read=120.0), follow_redirects=True,
                                             headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": "open-market-os-updater"})

    def check(self) -> dict[str, Any]:
        r = self.client.get(f"{self.api}/repos/{self.repo}/releases/latest")
        if r.status_code == 404:
            return {"available": False, "current": self.current, "latest": None,
                    "message": "No published release yet."}
        r.raise_for_status()
        rel = r.json()
        latest = str(rel.get("tag_name", ""))
        newer = parse_version(latest) > parse_version(self.current)
        pat = asset_pattern()
        assets = {a["name"]: a for a in rel.get("assets", [])}
        mine = next((a for n, a in assets.items() if pat.search(n)), None)
        sums = assets.get("SHA256SUMS.txt")
        out: dict[str, Any] = {"available": bool(newer and mine), "current": self.current, "latest": latest,
                               "published_at": rel.get("published_at"), "notes": (rel.get("body") or "")[:4000],
                               "page": rel.get("html_url")}
        if newer and not mine:
            out["message"] = f"Version {latest} has no installer for this computer."
        if mine:
            out["asset"] = {"name": mine["name"], "size": mine.get("size"), "url": mine["browser_download_url"]}
            out["sums_url"] = sums["browser_download_url"] if sums else None
        return out

    def download(self, info: dict[str, Any], job=None) -> Path:
        asset = info.get("asset")
        if not asset or not info.get("sums_url"):
            raise OmosError("this release has no verifiable installer for this computer")
        sums = self.client.get(info["sums_url"])
        sums.raise_for_status()
        expected = next((ln.split()[0].lower() for ln in sums.text.splitlines()
                         if len(ln.split()) == 2 and ln.split()[1].lstrip("*").removeprefix("./") == asset["name"]), None)
        if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise PermissionDenied(f"{asset['name']} is not listed in the release's SHA256SUMS.txt; not downloading it")
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.dir / Path(asset["name"]).name
        fd, tmp_name = tempfile.mkstemp(dir=self.dir, suffix=".part")
        os.close(fd)
        tmp = Path(tmp_name)
        h, done = hashlib.sha256(), 0
        try:
            with self.client.stream("GET", asset["url"]) as r, tmp.open("wb") as f:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or asset.get("size") or 0)
                for chunk in r.iter_bytes(1 << 20):
                    if job:
                        job.check_cancel()
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if job:
                        job.update(5 + 85 * done / total if total else None, f"Downloading {asset['name']}: {done / 1e6:.0f} MB")
            if h.hexdigest() != expected:
                raise PermissionDenied("the download does not match its published SHA-256; it was deleted")
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return target

    @staticmethod
    def install(path: Path) -> dict[str, Any]:
        """Start installing a verified file. Returns how (the caller restarts or exits the app if needed)."""
        name = path.name
        if name.endswith("-setup.exe"):
            # the installer stops this app, updates it, then starts it again (/RELAUNCH=1)
            subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1"],  # noqa: S603
                             creationflags=0x00000008 | 0x00000200, close_fds=True)
            return {"how": "installer", "message": "The installer is updating the app; it reopens by itself."}
        if name.endswith(".AppImage") and os.environ.get("APPIMAGE"):
            current = Path(os.environ["APPIMAGE"])
            staged = current.with_name(current.name + ".new")
            staged.write_bytes(path.read_bytes())
            staged.chmod(0o755)
            os.replace(staged, current)  # the running copy keeps its open file; the next start uses the new one
            return {"how": "restart", "message": "Updated. The app restarts now."}
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(path)], close_fds=True)  # noqa: S603
        hint = ("Drag Open Market OS to Applications, replacing the old one, then open it again."
                if name.endswith(".dmg") else "Confirm the update in the package installer, then open the app again.")
        return {"how": "manual", "message": f"The update was downloaded and verified. {hint}", "file": str(path)}
