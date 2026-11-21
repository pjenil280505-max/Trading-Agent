"""Plugin manager.

Plugin kinds:
  indicator           an OMS Script file with plot() statements        (sandboxed by the language)
  strategy            an OMS Script strategy template                  (sandboxed by the language)
  screener            a screener definition (JSON)                     (data only)
  workflow            a workflow template (JSON)                       (data only, runs through the engine)
  data_provider_rest  a REST candle endpoint description (JSON)        (network only to declared hosts)
  process             a separate program speaking JSON-RPC over stdio  (isolated process, see below)

Every plugin declares its permissions in ``plugin.json``; it is installed DISABLED and only runs
after a person grants every permission it asks for. Nothing a plugin declares can grant it
access to secrets, trading, settings or the filesystem outside its own folder.

Process plugins run with a scrubbed environment (no secrets), their own folder as working
directory, a wall-clock timeout, and CPU/memory limits on POSIX. They only receive the data a
call passes them. Their own network access cannot be blocked portably — this is shown when you
install one, and documented in SECURITY.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess  # noqa: S404 - process plugins are explicit, permissioned, argv-only
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from omos import __version__
from omos.errors import NotFound, OmosError, PermissionDenied, ValidationError
from omos.storage.db import Database
from omos.util import dumps, loads, now_ms

KINDS = ("indicator", "strategy", "screener", "workflow", "data_provider_rest", "process")
PERM = re.compile(r"^(read:market_data|network:[a-z0-9.-]{1,253}|compute)$")
ID = re.compile(r"^[a-z0-9][a-z0-9.-]{2,80}$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
MAX_FILES = 200
MAX_BYTES = 5_000_000


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def validate_manifest(m: dict[str, Any]) -> dict[str, Any]:
    required = ("id", "name", "version", "author", "license", "kind", "entry", "description")
    missing = [k for k in required if not m.get(k)]
    if missing:
        raise ValidationError(f"plugin.json is missing: {', '.join(missing)}")
    if not ID.match(str(m["id"])):
        raise ValidationError("plugin id must be lowercase letters, digits, '.' or '-' (3–80 chars)")
    if not SEMVER.match(str(m["version"])):
        raise ValidationError("version must be semantic, e.g. 1.0.0")
    if m["kind"] not in KINDS:
        raise ValidationError(f"kind must be one of {', '.join(KINDS)}")
    perms = m.get("permissions", [])
    if not isinstance(perms, list) or any(not isinstance(p, str) or not PERM.match(p) for p in perms):
        raise ValidationError("permissions may only be read:market_data, compute, or network:<exact host>")
    entry = str(m["entry"])
    if entry.startswith(("/", "\\")) or ".." in Path(entry).parts:
        raise ValidationError("entry must be a file inside the plugin folder")
    req = str(m.get("omos_version", ">=0.0.0"))
    mt = re.match(r"^>=\s*(\d+\.\d+(\.\d+)?)$", req)
    if not mt:
        raise ValidationError("omos_version must look like '>=0.1.0'")
    if _ver(__version__) < _ver(mt.group(1)):
        raise ValidationError(f"plugin needs Open Market OS {req}; this is {__version__}")
    if m["kind"] == "data_provider_rest" and not any(p.startswith("network:") for p in perms):
        raise ValidationError("a REST data provider must declare network:<host>")
    if m["kind"] == "process" and "compute" not in perms:
        raise ValidationError("a process plugin must declare the 'compute' permission")
    deps = m.get("dependencies", [])
    if not isinstance(deps, list):
        raise ValidationError("dependencies must be a list of plugin ids")
    return m


def folder_digest(folder: Path) -> str:
    h = hashlib.sha256()
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    if len(files) > MAX_FILES:
        raise ValidationError(f"plugin has more than {MAX_FILES} files")
    total = 0
    for p in files:
        if p.is_symlink():
            raise ValidationError("plugins may not contain symbolic links")
        data = p.read_bytes()
        total += len(data)
        if total > MAX_BYTES:
            raise ValidationError("plugin is larger than 5 MB")
        h.update(str(p.relative_to(folder)).encode())
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


class PluginManager:
    def __init__(self, db: Database, plugins_dir: Path):
        self.db = db
        self.dir = plugins_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def install(self, source: Path, *, actor: str) -> dict[str, Any]:
        source = Path(source).resolve()
        mf = source / "plugin.json"
        if not mf.is_file():
            raise ValidationError("folder has no plugin.json")
        try:
            manifest = validate_manifest(json.loads(mf.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"plugin.json is not valid JSON: {exc}") from exc
        if not (source / manifest["entry"]).is_file():
            raise ValidationError(f"entry file '{manifest['entry']}' not found")
        for dep in manifest.get("dependencies", []):
            if not self.db.query_one("SELECT 1 FROM plugins WHERE id = ?", (dep,)):
                raise ValidationError(f"missing dependency plugin '{dep}'")
        digest = folder_digest(source)
        self._check_entry(manifest, source)
        target = self.dir / manifest["id"]
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, symlinks=False)
        self.db.execute("INSERT INTO plugins(id, name, version, kind, manifest, granted_permissions, enabled, sha256, installed_at) "
                        "VALUES (?,?,?,?,?, '[]', 0, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
                        "version=excluded.version, kind=excluded.kind, manifest=excluded.manifest, granted_permissions='[]', "
                        "enabled=0, sha256=excluded.sha256, installed_at=excluded.installed_at",
                        (manifest["id"], manifest["name"][:80], manifest["version"], manifest["kind"], dumps(manifest),
                         digest, now_ms()))
        self.db.audit(actor, "plugin.install", manifest["id"], {"version": manifest["version"], "sha256": digest,
                                                                "permissions": manifest.get("permissions", [])})
        return self.get(manifest["id"])

    def _check_entry(self, m: dict[str, Any], folder: Path) -> None:
        entry = folder / m["entry"]
        if m["kind"] in ("indicator", "strategy"):
            from omos.script.lang import parse

            prog = parse(entry.read_text(encoding="utf-8"))
            if m["kind"] == "strategy" and not prog.is_strategy:
                raise ValidationError("strategy plugins must set long_entry and/or short_entry")
            if m["kind"] == "indicator" and not any(s.kind == "plot" for s in prog.statements):
                raise ValidationError("indicator plugins need at least one plot(...)")
        elif m["kind"] == "data_provider_rest":
            spec = json.loads(entry.read_text(encoding="utf-8"))
            host = urlparse(spec.get("url", "")).hostname
            if urlparse(spec.get("url", "")).scheme != "https" or not host:
                raise ValidationError("REST provider url must be https://")
            if f"network:{host}" not in m.get("permissions", []):
                raise ValidationError(f"REST provider calls {host} but does not declare network:{host}")
        elif m["kind"] in ("screener", "workflow"):
            json.loads(entry.read_text(encoding="utf-8"))

    def get(self, pid: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM plugins WHERE id = ?", (pid,))
        if not row:
            raise NotFound(f"plugin '{pid}' is not installed")
        row["manifest"] = loads(row["manifest"], {})
        row["granted_permissions"] = loads(row["granted_permissions"], [])
        row["requested_permissions"] = row["manifest"].get("permissions", [])
        row["isolation"] = ("separate process; scrubbed environment; CPU/memory limits on POSIX; network NOT blockable"
                            if row["kind"] == "process" else "data/script interpreted by Open Market OS (sandboxed)")
        return row

    def list(self) -> list[dict[str, Any]]:
        return [self.get(r["id"]) for r in self.db.query("SELECT id FROM plugins ORDER BY id")]

    def enable(self, pid: str, *, grant: list[str], actor: str) -> dict[str, Any]:
        p = self.get(pid)
        missing = set(p["requested_permissions"]) - set(grant)
        if missing:
            raise PermissionDenied(f"grant every requested permission to enable it: {', '.join(sorted(missing))}")
        if folder_digest(self.dir / pid) != p["sha256"]:
            raise PermissionDenied("plugin files changed since installation; reinstall it")
        self.db.execute("UPDATE plugins SET enabled = 1, granted_permissions = ? WHERE id = ?",
                        (dumps(sorted(set(grant) & set(p["requested_permissions"]))), pid))
        self.db.audit(actor, "plugin.enable", pid, {"granted": grant})
        return self.get(pid)

    def disable(self, pid: str, *, actor: str) -> dict[str, Any]:
        self.get(pid)
        self.db.execute("UPDATE plugins SET enabled = 0 WHERE id = ?", (pid,))
        self.db.audit(actor, "plugin.disable", pid)
        return self.get(pid)

    def uninstall(self, pid: str, *, actor: str) -> None:
        self.get(pid)
        shutil.rmtree(self.dir / pid, ignore_errors=True)
        self.db.execute("DELETE FROM plugins WHERE id = ?", (pid,))
        self.db.audit(actor, "plugin.uninstall", pid)

    def enabled(self, kind: str | None = None) -> list[dict[str, Any]]:
        return [p for p in self.list() if p["enabled"] and (kind is None or p["kind"] == kind)]

    def read_entry(self, pid: str) -> str:
        p = self.get(pid)
        if not p["enabled"]:
            raise PermissionDenied(f"plugin '{pid}' is disabled")
        return (self.dir / pid / p["manifest"]["entry"]).read_text(encoding="utf-8")

    # -- process plugins ---------------------------------------------------------------
    def call_process(self, pid: str, method: str, params: dict[str, Any], *, timeout_s: float = 10.0) -> Any:
        p = self.get(pid)
        if p["kind"] != "process":
            raise ValidationError("not a process plugin")
        if not p["enabled"] or "compute" not in p["granted_permissions"]:
            raise PermissionDenied(f"plugin '{pid}' is not enabled with the compute permission")
        folder = self.dir / pid
        if folder_digest(folder) != p["sha256"]:
            raise PermissionDenied("plugin files changed since installation; reinstall it")
        entry = folder / p["manifest"]["entry"]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "OMOS_PLUGIN_ID": pid,
               "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        if os.name == "nt":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        cmd = [sys.executable, "-I", str(entry)] if entry.suffix == ".py" else [str(entry)]
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if len(request) > 20_000_000:
            raise ValidationError("request too large for a plugin")
        try:
            proc = subprocess.run(cmd, input=request, capture_output=True, text=True, timeout=timeout_s,  # noqa: S603
                                  cwd=folder, env=env, preexec_fn=_limits if os.name == "posix" else None)
        except subprocess.TimeoutExpired as exc:
            raise OmosError(f"plugin '{pid}' timed out after {timeout_s}s", code="plugin_timeout") from exc
        if proc.returncode != 0:
            raise OmosError(f"plugin '{pid}' failed (exit {proc.returncode}): {proc.stderr[-300:]}", code="plugin_error")
        try:
            resp = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise OmosError(f"plugin '{pid}' returned an invalid response", code="plugin_error") from exc
        if "error" in resp:
            raise OmosError(f"plugin '{pid}': {str(resp['error'])[:300]}", code="plugin_error")
        return resp.get("result")


def _limits() -> None:  # pragma: no cover - runs in the child process
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
