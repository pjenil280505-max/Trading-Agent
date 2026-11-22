"""Managed local AI: free, private, no account.

If Ollama is already installed and running, it is used as-is. Otherwise, with the user's consent,
the official Ollama runtime is downloaded from ``ollama.com/download/<asset>`` (which redirects to
the project's GitHub release), **verified against that release's published sha256sum.txt**, unpacked
into the app's data folder and run as a background child process on 127.0.0.1:11435. A model sized
for this computer is then pulled from Ollama's registry and registered as an AI provider.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from omos.errors import OmosError, PermissionDenied, ValidationError

log = logging.getLogger(__name__)

SYSTEM_PORT = 11434
MANAGED_PORT = 11435
DOWNLOAD_BASE = "https://ollama.com/download/"
RELEASE_HOST = "github.com"
# Only Ollama's official GitHub release is accepted as the source of the runtime and its checksum file.
RELEASE_PATH = re.compile(r"^/ollama/ollama/releases/download/(?P<tag>v[0-9][0-9A-Za-z.\-]*)/(?P<asset>[A-Za-z0-9._\-]+)$")
TRUSTED_HOSTS = {"ollama.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
MAX_RUNTIME_BYTES = 6 * 1024 ** 3  # the Linux and Windows bundles include GPU libraries (~1.6 GB)

# name, approximate download size in GB, recommended minimum RAM in GB
MODELS: list[tuple[str, float, float]] = [
    ("qwen2.5:0.5b", 0.4, 2), ("qwen2.5:1.5b", 1.0, 6), ("qwen2.5:3b", 1.9, 10), ("qwen2.5:7b", 4.7, 16),
]


def runtime_assets() -> list[str]:
    """Candidate archive names for this computer, newest packaging first."""
    sysname, mach = platform.system().lower(), platform.machine().lower()
    arch = "arm64" if mach in ("arm64", "aarch64") else "amd64" if mach in ("x86_64", "amd64") else None
    if sysname == "linux" and arch:
        return [f"ollama-linux-{arch}.tar.zst", f"ollama-linux-{arch}.tgz"]
    if sysname == "windows" and arch:
        return [f"ollama-windows-{arch}.zip"]
    if sysname == "darwin":
        return ["ollama-darwin.tgz"]
    raise ValidationError(f"local AI is not available for {platform.system()} {platform.machine()}")


RUNTIME_DOWNLOAD_GB = {"linux": 1.6, "windows": 1.7, "darwin": 0.2}  # includes GPU libraries on Linux/Windows


def recommend_model(device: dict[str, Any]) -> dict[str, Any]:
    """Largest model this computer runs comfortably. Without a GPU, speed (not memory) is the limit,
    so CPU-only machines get at most a 3B model unless they have plenty of RAM."""
    ram = float(device.get("ram_total_gb") or 0)
    disk = float(device.get("disk_free_gb") or 0)
    gpu = device.get("gpu") or {}
    vram = float(gpu.get("vram_gb") or 0)
    accel = vram >= 4 or bool(gpu.get("apple_silicon"))
    runtime_gb = RUNTIME_DOWNLOAD_GB.get(platform.system().lower(), 1.7)
    choice = MODELS[0]
    for name, size, min_ram in MODELS:
        fits_memory = (vram >= size * 1.5) or (ram >= min_ram)
        fast_enough = accel or size <= 2.0 or ram >= 32
        if fits_memory and fast_enough and size * 2.5 + runtime_gb <= disk:
            choice = (name, size, min_ram)
    hw = f"{ram:.0f} GB RAM" + (f", {gpu.get('name') or 'GPU'} with {vram:.0f} GB" if vram else "") + \
        (", Apple Silicon" if gpu.get("apple_silicon") else "") + f", {disk:.0f} GB free disk"
    return {"model": choice[0], "download_gb": choice[1], "min_ram_gb": choice[2],
            "runtime_download_gb": runtime_gb, "fits": ram >= 2 and choice[1] * 2.5 + runtime_gb <= disk,
            "accelerated": accel, "reason": hw,
            "options": [{"model": m[0], "download_gb": m[1], "min_ram_gb": m[2]} for m in MODELS]}


def _extract_tar(fileobj, dest: Path, mode: str) -> None:
    if not hasattr(tarfile, "data_filter"):  # Python < 3.11.4 / 3.12.0
        raise OmosError("this Python is too old to unpack the AI runtime safely; update Python")
    try:
        with tarfile.open(fileobj=fileobj, mode=mode) as tf:
            tf.extractall(dest, filter="data")  # the 'data' filter rejects absolute paths, '..' and links outside dest
    except tarfile.FilterError as exc:
        raise ValidationError(f"the archive contains an unsafe entry and was rejected: {exc}") from exc
    except (tarfile.TarError, EOFError) as exc:
        raise ValidationError(f"the archive is damaged: {exc}") from exc


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for n in zf.namelist():
        target = (root / n).resolve()
        if root != target and root not in target.parents:
            raise ValidationError(f"archive entry escapes the target folder: {n}")
    zf.extractall(dest)


class LocalAI:
    def __init__(self, data_path: Path, *, download_base: str = DOWNLOAD_BASE, trusted_hosts: set[str] | None = None,
                 release_host: str = RELEASE_HOST, client: httpx.Client | None = None,
                 system_port: int = SYSTEM_PORT, managed_port: int = MANAGED_PORT):
        self.root = data_path / "ai"
        self.runtime_dir = self.root / "ollama"
        self.models_dir = self.root / "models"
        self.download_base = download_base
        self.trusted = trusted_hosts if trusted_hosts is not None else TRUSTED_HOSTS
        self.release_host = release_host
        self.client = client or httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=False)
        self.system_port, self.managed_port = system_port, managed_port
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    # -- discovery ----------------------------------------------------------------------------------
    def _api(self, port: int, path: str, timeout: float = 2.0) -> Any:
        r = httpx.get(f"http://127.0.0.1:{port}{path}", timeout=timeout, trust_env=False)
        r.raise_for_status()
        return r.json()

    def _probe(self, port: int) -> dict[str, Any] | None:
        try:
            ver = self._api(port, "/api/version").get("version")
            models = [m["name"] for m in self._api(port, "/api/tags").get("models", [])]
            return {"port": port, "version": ver, "models": models}
        except (httpx.HTTPError, ValueError, KeyError):
            return None

    def binary(self) -> Path | None:
        for cand in (self.runtime_dir / "bin" / "ollama", self.runtime_dir / "ollama", self.runtime_dir / "ollama.exe",
                     self.runtime_dir / "bin" / "ollama.exe"):
            if cand.exists():
                return cand
        return None

    def status(self, device: dict[str, Any] | None = None) -> dict[str, Any]:
        system = self._probe(self.system_port)
        managed = self._probe(self.managed_port)
        out: dict[str, Any] = {"system": system, "managed_installed": self.binary() is not None,
                               "managed_running": managed is not None, "managed": managed,
                               "active": "system" if system else "managed" if managed else None}
        if device:
            out["recommended"] = recommend_model(device)
        return out

    # -- runtime install -------------------------------------------------------------------------------
    def _get(self, url: str, *, stream: bool = False) -> tuple[list[str], httpx.Response | None]:
        """GET following redirects only between trusted HTTPS hosts. Returns (every URL visited, response);
        the response is None on 404 so callers can try another asset name."""
        chain: list[str] = []
        for _ in range(8):
            p = urlparse(url)
            local_test = p.scheme == "http" and p.hostname == "127.0.0.1" and "127.0.0.1" in self.trusted
            if p.scheme != "https" and not local_test:
                raise PermissionDenied(f"refusing non-HTTPS download: {p.scheme}://{p.hostname}")
            if p.hostname not in self.trusted:
                raise PermissionDenied(f"refusing download from untrusted host {p.hostname}")
            chain.append(url)
            r = self.client.send(self.client.build_request("GET", url), stream=stream)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = str(httpx.URL(url).join(r.headers["location"]))
                r.close()
                continue
            if r.status_code == 404:
                r.close()
                return chain, None
            if r.status_code != 200:
                r.close()
                raise OmosError(f"download failed: HTTP {r.status_code} from {p.hostname}")
            return chain, r
        raise OmosError("too many redirects while downloading the AI runtime")

    def _release_hop(self, chain: list[str], asset: str) -> tuple[str, str] | None:
        """(tag, url) of the hop through Ollama's official release download path, if the chain has one."""
        for u in chain:
            p = urlparse(u)
            m = RELEASE_PATH.match(p.path)
            if p.hostname == self.release_host and m and m.group("asset") == asset:
                return m.group("tag"), u
        return None

    def install_runtime(self, job=None) -> Path:
        resp = None
        for asset in runtime_assets():
            chain, resp = self._get(self.download_base + asset, stream=True)
            if resp is not None:
                break
        if resp is None:
            raise OmosError("Ollama does not publish a runtime for this computer")
        hop = self._release_hop(chain, asset)
        if not hop:
            resp.close()
            raise PermissionDenied("the download did not come from Ollama's official release; refusing to install")
        tag, hop_url = hop
        total = int(resp.headers.get("content-length") or 0)
        if total > MAX_RUNTIME_BYTES:
            resp.close()
            raise ValidationError("runtime download is unexpectedly large")
        self.root.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        fd, tmp_name = tempfile.mkstemp(prefix="ollama-", suffix=".part", dir=self.root)
        os.close(fd)
        tmp = Path(tmp_name)
        staging: Path | None = None
        done = 0
        try:
            with tmp.open("wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    if job:
                        job.check_cancel()
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if done > MAX_RUNTIME_BYTES:
                        raise ValidationError("runtime download is unexpectedly large")
                    if job:
                        job.update(5 + 45 * done / total if total else None,
                                   f"Downloading the Ollama runtime {tag}: {done / 1e6:.0f}"
                                   + (f" of {total / 1e6:.0f} MB" if total else " MB"))
            resp.close()
            _, sums = self._get(hop_url.rsplit("/", 1)[0] + "/sha256sum.txt")
            if sums is None:
                raise PermissionDenied(f"release {tag} publishes no sha256sum.txt; refusing to install")
            expected = None
            for line in sums.text.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].lstrip("*").removeprefix("./") == asset:
                    expected = parts[0].lower()
            if not expected:
                raise PermissionDenied(f"{asset} is not listed in the release's sha256sum.txt; refusing to install")
            if h.hexdigest() != expected:
                raise PermissionDenied("the downloaded AI runtime does not match its published SHA-256; it was deleted")
            if job:
                job.update(51, "Checksum verified; unpacking the runtime")
            staging = Path(tempfile.mkdtemp(prefix="ollama-unpack-", dir=self.root))
            if asset.endswith(".zip"):
                with zipfile.ZipFile(tmp) as zf:
                    _safe_extract_zip(zf, staging)
            elif asset.endswith(".tar.zst"):
                import zstandard
                with tmp.open("rb") as f, zstandard.ZstdDecompressor().stream_reader(f) as zr:
                    _extract_tar(zr, staging, "r|")
            else:
                with tmp.open("rb") as f:
                    _extract_tar(f, staging, "r:*")
            shutil.rmtree(self.runtime_dir, ignore_errors=True)
            staging.rename(self.runtime_dir)
            staging = None
            (self.runtime_dir / "VERSION").write_text(f"{tag}\n{asset}\n{expected}\n")
        finally:
            tmp.unlink(missing_ok=True)
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
        b = self.binary()
        if not b:
            raise OmosError("the Ollama archive did not contain the expected program")
        if os.name != "nt":
            b.chmod(0o755)
        return b

    def installed_version(self) -> str | None:
        try:
            return (self.runtime_dir / "VERSION").read_text().split()[0]
        except (OSError, IndexError):
            return None

    # -- managed process ----------------------------------------------------------------------------
    def start_managed(self, wait_s: float = 30.0) -> dict[str, Any]:
        with self._lock:
            running = self._probe(self.managed_port)
            if running:
                return running
            b = self.binary()
            if not b:
                raise OmosError("local AI runtime is not installed")
            self.models_dir.mkdir(parents=True, exist_ok=True)
            env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{self.managed_port}", "OLLAMA_MODELS": str(self.models_dir)}
            env.pop("OLLAMA_ORIGINS", None)
            log_f = (self.root / "ollama.log").open("ab")
            kw: dict[str, Any] = {"stdout": log_f, "stderr": log_f, "stdin": subprocess.DEVNULL, "env": env}
            if os.name == "nt":
                kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            self._proc = subprocess.Popen([str(b), "serve"], **kw)
        import time
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            st = self._probe(self.managed_port)
            if st:
                return st
            if self._proc and self._proc.poll() is not None:
                raise OmosError(f"the local AI runtime exited (code {self._proc.returncode}); see {self.root / 'ollama.log'}")
            time.sleep(0.5)
        raise OmosError("the local AI runtime did not start in time")

    def stop_managed(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None

    # -- models ------------------------------------------------------------------------------------------
    def pull(self, port: int, model: str, job=None) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._\-/]*(:[A-Za-z0-9._\-]+)?", model):
            raise ValidationError("invalid model name")
        with httpx.stream("POST", f"http://127.0.0.1:{port}/api/pull", json={"model": model, "stream": True},
                          timeout=httpx.Timeout(30.0, read=600.0), trust_env=False) as r:
            if r.status_code != 200:
                raise OmosError(f"model download failed: HTTP {r.status_code}")
            for line in r.iter_lines():
                if job:
                    job.check_cancel()
                if not line.strip():
                    continue
                msg = json.loads(line)
                if msg.get("error"):
                    raise OmosError(f"model download failed: {msg['error']}")
                total, completed = msg.get("total"), msg.get("completed")
                if job and total and completed is not None:
                    job.update(55 + 40 * completed / total, f"Downloading model {model}: {completed / 1e6:.0f} of {total / 1e6:.0f} MB")
                elif job:
                    job.update(None, f"{model}: {msg.get('status', '')}")

    def delete_model(self, port: int, model: str) -> None:
        r = httpx.request("DELETE", f"http://127.0.0.1:{port}/api/delete", json={"model": model}, timeout=30, trust_env=False)
        if r.status_code not in (200, 404):
            raise OmosError(f"could not delete {model}: HTTP {r.status_code}")

    def setup(self, model: str, ai_service, job=None) -> dict[str, Any]:
        """Full one-click setup: runtime (unless Ollama is already running) → model → provider."""
        if job:
            job.update(2, "Checking for an existing Ollama installation")
        system = self._probe(self.system_port)
        if system:
            port, how = self.system_port, "your existing Ollama"
        else:
            if not self.binary():
                self.install_runtime(job)
            if job:
                job.update(53, "Starting the local AI runtime")
            port, how = self.start_managed()["port"], "a private Ollama runtime managed by Open Market OS"
        if model not in ((self._probe(port) or {}).get("models") or []):
            self.pull(port, model, job)
        if job:
            job.update(97, "Registering the local model")
        name = f"Local AI ({model})"
        existing = next((p for p in ai_service.list() if p["name"] == name), None)
        prov = existing or ai_service.add(name, "ollama", base_url=f"http://127.0.0.1:{port}/v1", model=model)
        ai_service.set_enabled(prov["id"], True)
        return {"provider_id": prov["id"], "model": model, "port": port, "runtime": how}
