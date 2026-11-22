"""No-terminal experience: managed local AI install (against a fake release server and a fake Ollama),
background jobs, first-run setup, start-at-login, keep-awake, quit/restart endpoints."""

from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import sys
import tarfile
import textwrap
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import numpy as np
import pytest
import zstandard
from fastapi.testclient import TestClient

from helpers import ControlledProvider
from omos import desktop
from omos.ai import local as local_mod
from omos.ai.local import LocalAI, recommend_model
from omos.api.app import create_app
from omos.config import HostConfig
from omos.errors import OmosError, PermissionDenied, ValidationError
from omos.host import Host
from omos.jobs import JobRegistry

TAG = "v9.9.9"

# A stand-in for the real `ollama` program: `ollama serve` answers the few API calls Open Market OS makes.
FAKE_OLLAMA = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    host, port = os.environ["OLLAMA_HOST"].rsplit(":", 1)
    store = os.path.join(os.environ["OLLAMA_MODELS"], "pulled.json")
    def models():
        try:
            return json.load(open(store))
        except OSError:
            return []
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def _json(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            if self.path == "/api/version":
                return self._json({"version": "9.9.9-fake"})
            if self.path == "/api/tags":
                return self._json({"models": [{"name": m} for m in models()]})
            self._json({"error": "not found"}, 404)
        def _body(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        def do_POST(self):
            if self.path == "/api/pull":
                name = self._body()["model"]
                self.send_response(200); self.send_header("Content-Type", "application/x-ndjson"); self.end_headers()
                if name == "bad:model":
                    self.wfile.write(b'{"error":"pull model manifest: file does not exist"}\\n'); return
                for line in ({"status": "pulling manifest"}, {"status": "downloading", "total": 1000, "completed": 500},
                             {"status": "downloading", "total": 1000, "completed": 1000}, {"status": "success"}):
                    self.wfile.write((json.dumps(line) + "\\n").encode()); self.wfile.flush()
                json.dump(sorted(set(models()) | {name}), open(store, "w"))
                return
            self._json({"error": "not found"}, 404)
        def do_DELETE(self):
            if self.path == "/api/delete":
                name = self._body()["model"]
                json.dump([m for m in models() if m != name], open(store, "w"))
                return self._json({})
            self._json({"error": "not found"}, 404)
    if sys.argv[1:] == ["serve"]:
        ThreadingHTTPServer((host, int(port)), H).serve_forever()
    ''')


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tar_bytes(files: dict[str, bytes], *, compress: str = "zst", extra: list[tarfile.TarInfo] | None = None) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode = len(data), 0o755
            tf.addfile(ti, io.BytesIO(data))
        for ti in extra or []:
            tf.addfile(ti, io.BytesIO(b"x" * ti.size) if ti.isfile() else None)
    if compress == "zst":
        return zstandard.ZstdCompressor().compress(raw.getvalue())
    import gzip
    return gzip.compress(raw.getvalue())


class Release:
    """Fake ollama.com/download → github.com release → asset CDN chain, all on 127.0.0.1."""

    def __init__(self):
        self.assets: dict[str, bytes] = {}
        self.sums: dict[str, str] = {}
        self.sums_published = True
        self.offsite_redirect: str | None = None
        self.skip_release_hop = False
        rel = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _redirect(self, to: str, code: int = 302):
                self.send_response(code)
                self.send_header("Location", to)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                p = self.path
                if p.startswith("/download/"):
                    asset = p.rsplit("/", 1)[1]
                    if rel.offsite_redirect:
                        return self._redirect(rel.offsite_redirect, 307)
                    if rel.skip_release_hop:
                        return self._redirect(f"/blob/{asset}", 307)
                    return self._redirect(f"/ollama/ollama/releases/latest/download/{asset}", 307)
                if p.startswith("/ollama/ollama/releases/latest/download/"):
                    return self._redirect(f"/ollama/ollama/releases/download/{TAG}/{p.rsplit('/', 1)[1]}")
                if p == f"/ollama/ollama/releases/download/{TAG}/sha256sum.txt":
                    if not rel.sums_published:
                        self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
                        return
                    body = "".join(f"{h}  ./{n}\n" for n, h in rel.sums.items()).encode()
                    self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
                    self.wfile.write(body)
                    return
                if p.startswith(f"/ollama/ollama/releases/download/{TAG}/"):
                    asset = p.rsplit("/", 1)[1]
                    if asset not in rel.assets:
                        self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
                        return
                    return self._redirect(f"/blob/{asset}")
                if p.startswith("/blob/") and p[6:] in rel.assets:
                    body = rel.assets[p[6:]]
                    self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, asset: str, data: bytes, *, listed_hash: str | None = None) -> None:
        self.assets[asset] = data
        self.sums[asset] = listed_hash or hashlib.sha256(data).hexdigest()

    def local_ai(self, data: Path, **kw) -> LocalAI:
        return LocalAI(data, download_base=f"http://127.0.0.1:{self.port}/download/", trusted_hosts={"127.0.0.1"},
                       release_host="127.0.0.1", client=httpx.Client(timeout=10, trust_env=False),
                       system_port=kw.get("system_port", free_port()), managed_port=kw.get("managed_port", free_port()))


@pytest.fixture
def release(monkeypatch):
    rel = Release()
    monkeypatch.setattr(local_mod, "runtime_assets", lambda: ["ollama-linux-amd64.tar.zst", "ollama-linux-amd64.tgz"])
    yield rel
    rel.server.shutdown()


# ---------------------------------------------------------------------------------------- runtime install
def test_install_verifies_checksum_and_unpacks(release, tmp_path):
    release.publish("ollama-linux-amd64.tar.zst", tar_bytes({"bin/ollama": FAKE_OLLAMA.encode(), "lib/ollama/x.so": b"lib"}))
    la = release.local_ai(tmp_path)
    b = la.install_runtime()
    assert b == la.runtime_dir / "bin" / "ollama" and b.read_text().startswith("#!/usr/bin/env python3")
    assert la.installed_version() == TAG
    assert (la.runtime_dir / "lib" / "ollama" / "x.so").read_bytes() == b"lib"
    assert not list(la.root.glob("*.part")) and not list(la.root.glob("ollama-unpack-*"))


def test_falls_back_to_tgz_when_zst_is_not_published(release, tmp_path):
    release.publish("ollama-linux-amd64.tgz", tar_bytes({"bin/ollama": b"#!/bin/sh\n"}, compress="gz"))
    la = release.local_ai(tmp_path)
    assert la.install_runtime().exists()


def test_tampered_download_is_rejected_and_deleted(release, tmp_path):
    release.publish("ollama-linux-amd64.tar.zst", tar_bytes({"bin/ollama": b"evil"}), listed_hash="0" * 64)
    la = release.local_ai(tmp_path)
    with pytest.raises(PermissionDenied, match="does not match its published SHA-256"):
        la.install_runtime()
    assert la.binary() is None and not list(la.root.glob("*.part"))


def test_asset_missing_from_checksum_file_or_no_checksum_file(release, tmp_path):
    data = tar_bytes({"bin/ollama": b"x"})
    release.assets["ollama-linux-amd64.tar.zst"] = data  # served but not listed
    with pytest.raises(PermissionDenied, match="not listed"):
        release.local_ai(tmp_path).install_runtime()
    release.publish("ollama-linux-amd64.tar.zst", data)
    release.sums_published = False
    with pytest.raises(PermissionDenied, match="no sha256sum.txt"):
        release.local_ai(tmp_path).install_runtime()


def test_download_must_pass_through_the_official_release(release, tmp_path):
    release.publish("ollama-linux-amd64.tar.zst", tar_bytes({"bin/ollama": b"x"}))
    release.skip_release_hop = True
    with pytest.raises(PermissionDenied, match="official release"):
        release.local_ai(tmp_path).install_runtime()


def test_redirect_to_untrusted_host_is_refused(release, tmp_path):
    release.offsite_redirect = "https://evil.example.com/ollama.tar.zst"
    with pytest.raises(PermissionDenied, match="untrusted host evil.example.com"):
        release.local_ai(tmp_path).install_runtime()
    release.offsite_redirect = "http://127.0.0.2/x"  # plain HTTP anywhere but the local test server
    with pytest.raises(PermissionDenied, match="non-HTTPS"):
        release.local_ai(tmp_path).install_runtime()


def test_archive_path_traversal_and_outside_links_are_rejected(release, tmp_path):
    ti = tarfile.TarInfo("../escape")
    ti.size = 1
    release.publish("ollama-linux-amd64.tar.zst", tar_bytes({"bin/ollama": b"x"}, extra=[ti]))
    la = release.local_ai(tmp_path)
    with pytest.raises(ValidationError, match="unsafe entry"):
        la.install_runtime()
    assert la.binary() is None and not (la.root / "escape").exists()
    link = tarfile.TarInfo("bin/link")
    link.type, link.linkname = tarfile.SYMTYPE, "../../../../etc/passwd"
    release.publish("ollama-linux-amd64.tar.zst", tar_bytes({"bin/ollama": b"x"}, extra=[link]))
    with pytest.raises(ValidationError, match="unsafe entry"):
        release.local_ai(tmp_path).install_runtime()
    # absolute names are made relative: they land inside the runtime folder, never at the absolute path
    ab = tarfile.TarInfo("/omos-abs-test/escape")
    ab.size = 1
    release.publish("ollama-linux-amd64.tar.zst", tar_bytes({"bin/ollama": b"x"}, extra=[ab]))
    la = release.local_ai(tmp_path)
    la.install_runtime()
    assert (la.runtime_dir / "omos-abs-test" / "escape").exists() and not Path("/omos-abs-test").exists()


def test_zip_traversal_is_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.txt", "x")
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf, pytest.raises(ValidationError, match="escapes"):
        local_mod._safe_extract_zip(zf, tmp_path / "out")


def test_real_asset_names_match_the_platform():
    names = local_mod.runtime_assets()
    assert all(n.startswith("ollama-") for n in names)
    if sys.platform.startswith("linux"):
        assert names[0].endswith(".tar.zst") and names[1].endswith(".tgz")


# ---------------------------------------------------------------------------------------- model choice
@pytest.mark.parametrize("ram,disk,gpu,expected", [
    (4, 50, {}, "qwen2.5:0.5b"), (8, 50, {}, "qwen2.5:1.5b"), (16, 50, {}, "qwen2.5:3b"), (32, 50, {}, "qwen2.5:7b"),
    (16, 50, {"apple_silicon": True}, "qwen2.5:7b"), (8, 50, {"vram_gb": 12, "name": "RTX"}, "qwen2.5:7b"),
    (16, 3, {}, "qwen2.5:0.5b"),
])
def test_model_recommendation_follows_hardware(ram, disk, gpu, expected):
    rec = recommend_model({"ram_total_gb": ram, "disk_free_gb": disk, "gpu": gpu})
    assert rec["model"] == expected and rec["options"]


# ---------------------------------------------------------------------------------------- jobs
def test_jobs_progress_failure_cancel_and_uniqueness():
    events = []
    reg = JobRegistry(publish=lambda topic, d: events.append((topic, d["status"], d["progress"])))
    gate = threading.Event()

    def slow(job):
        job.update(40, "working")
        while not gate.is_set():
            job.check_cancel()
            time.sleep(0.01)
        return {"ok": 1}

    j = reg.start("demo", "Demo", slow)
    assert reg.start("demo", "Demo again", slow) is j  # one job of a kind at a time
    gate.set()
    for _ in range(200):
        if j.status != "running":
            break
        time.sleep(0.01)
    assert j.status == "succeeded" and j.result == {"ok": 1} and j.progress == 100
    assert ("job", "running", 40.0) in events and events[-1][1] == "succeeded"

    def boom(job):
        raise RuntimeError("disk on fire")
    jb = reg.start("boom", "Boom", boom)
    for _ in range(200):
        if jb.status != "running":
            break
        time.sleep(0.01)
    assert jb.status == "failed" and "disk on fire" in jb.error

    gate.clear()
    jc = reg.start("demo", "Cancel me", slow)
    reg.cancel(jc.id)
    for _ in range(200):
        if jc.status != "running":
            break
        time.sleep(0.01)
    assert jc.status == "cancelled"
    with pytest.raises(OmosError):
        reg.get("job_nope")


# ---------------------------------------------------------------------------------------- full API flow
@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OMOS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OMOS_VAULT_PASSPHRASE", "test vault passphrase")
    monkeypatch.setattr(desktop, "_home", lambda: tmp_path / "userhome")
    prov = ControlledProvider()
    prov.set_closes("ABC", 100 + np.cumsum(np.sin(np.arange(300) / 7.0)))
    host = Host(HostConfig(data_dir=str(tmp_path / "home")), start_loops=False, providers={"TEST": prov})
    client = TestClient(create_app(host, testing=True))
    code = host.auth.create_bootstrap_code()
    token = client.post("/api/auth/exchange", json={"code": code, "device_name": "t"}).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    yield {"host": host, "client": client, "tmp": tmp_path}
    host.local_ai.stop_managed()
    host.db.close()


def wait_job(client, job_id, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


@pytest.mark.skipif(os.name == "nt", reason="the fake runtime is a POSIX script")
def test_one_click_local_ai_setup_through_the_api(env, release):
    release.publish("ollama-linux-amd64.tar.zst", tar_bytes({"bin/ollama": FAKE_OLLAMA.encode()}))
    h, c = env["host"], env["client"]
    h.local_ai = release.local_ai(h.cfg.data_path)
    st = c.get("/api/ai/local").json()
    assert st["system"] is None and not st["managed_installed"] and st["recommended"]["model"]

    job = c.post("/api/ai/local/setup", json={"model": "qwen2.5:0.5b"}).json()
    assert job["kind"] == "local_ai" and job["status"] == "running"
    done = wait_job(c, job["id"])
    assert done["status"] == "succeeded", done
    assert done["result"]["model"] == "qwen2.5:0.5b" and "managed by Open Market OS" in done["result"]["runtime"]

    prov = next(p for p in c.get("/api/ai/providers").json() if p["name"] == "Local AI (qwen2.5:0.5b)")
    assert prov["enabled"] and prov["base_url"] == f"http://127.0.0.1:{h.local_ai.managed_port}/v1"
    st = c.get("/api/ai/local").json()
    assert st["managed_running"] and st["managed"]["models"] == ["qwen2.5:0.5b"] and st["runtime_version"] == TAG
    assert h.db.get_setting("ai.local.managed") is True

    # a second model, a failing model, then delete
    assert wait_job(c, c.post("/api/ai/local/pull", json={"model": "qwen2.5:1.5b"}).json()["id"])["status"] == "succeeded"
    bad = wait_job(c, c.post("/api/ai/local/pull", json={"model": "bad:model"}).json()["id"])
    assert bad["status"] == "failed" and "does not exist" in bad["error"]
    assert c.delete("/api/ai/local/models/qwen2.5:1.5b").json() == {"deleted": "qwen2.5:1.5b"}
    assert c.get("/api/ai/local").json()["managed"]["models"] == ["qwen2.5:0.5b"]

    # setting it up again reuses the runtime, model and provider
    again = wait_job(c, c.post("/api/ai/local/setup", json={"model": "qwen2.5:0.5b"}).json()["id"])
    assert again["status"] == "succeeded" and again["result"]["provider_id"] == prov["id"]

    assert c.post("/api/ai/local/stop").json() == {"stopped": True}
    assert not c.get("/api/ai/local").json()["managed_running"]
    assert c.post("/api/ai/local/start").json()["models"] == ["qwen2.5:0.5b"]


def test_existing_ollama_is_reused_instead_of_installing(env, release, monkeypatch):
    h, c = env["host"], env["client"]
    la = release.local_ai(h.cfg.data_path)
    h.local_ai = la
    pulled = []
    monkeypatch.setattr(la, "_probe", lambda port: {"port": port, "version": "x", "models": []} if port == la.system_port else None)
    monkeypatch.setattr(la, "pull", lambda port, model, job=None: pulled.append((port, model)))
    done = wait_job(c, c.post("/api/ai/local/setup", json={"model": "qwen2.5:3b"}).json()["id"])
    assert done["status"] == "succeeded" and done["result"]["runtime"] == "your existing Ollama"
    assert pulled == [(la.system_port, "qwen2.5:3b")] and la.binary() is None  # nothing downloaded
    assert h.db.get_setting("ai.local.managed", False) is False


def test_invalid_model_name_is_rejected(env):
    with pytest.raises(ValidationError):
        env["host"].local_ai.pull(1, "../../etc")


def test_first_run_setup_flow(env, monkeypatch):
    c, h = env["client"], env["host"]
    st = c.get("/api/setup").json()
    assert st["completed"] is False and st["system"]["ram_total_gb"] > 0 and st["ai"]["model"]
    assert c.get("/api/system/status").json()["setup_completed"] is False
    monkeypatch.setattr("omos.api.routes_system.SOURCE_PROBES", [("TEST", "TEST:ABC", "1m", "Test feed"),
                                                                  ("NOPE", "NOPE:X", "1d", "Disabled feed")])
    chk = c.post("/api/setup/check_sources").json()
    by = {r["provider"]: r for r in chk["results"]}
    assert by["TEST"]["ok"] and by["NOPE"]["status"] == "DISABLED" and chk["ok"] == 1
    out = c.post("/api/setup/complete", json={"autostart": True, "keep_awake": False}).json()
    assert out["completed"] and out["applied"]["autostart"]["enabled"] is True and out["applied"]["keep_awake"] is False
    assert c.get("/api/setup").json()["completed"] is True
    assert h.db.get_setting("power.keep_awake") is False


def test_autostart_toggle_writes_and_removes_the_login_entry(env):
    c = env["client"]
    if sys.platform.startswith("linux"):
        entry = env["tmp"] / "userhome" / ".config" / "autostart" / "open-market-os.desktop"
        assert c.put("/api/system/autostart", json={"enabled": True}).json()["enabled"] is True
        text = entry.read_text()
        assert "Exec=" in text and "start --no-browser --managed" in text and "Terminal=false" in text
        assert c.get("/api/system/autostart").json()["enabled"] is True
        assert c.put("/api/system/autostart", json={"enabled": False}).json()["enabled"] is False
        assert not entry.exists()


def test_keep_awake_follows_running_bots(env, monkeypatch):
    h, c = env["host"], env["client"]
    calls = []
    monkeypatch.setattr(h.keep_awake, "set", lambda on: calls.append(on))
    monkeypatch.setattr(h, "running_bots", lambda: 2)
    assert c.put("/api/system/power", json={"keep_awake": True}).json()["enabled"] is True
    monkeypatch.setattr(h, "running_bots", lambda: 0)
    h._power()
    assert calls == [True, False]


def test_quit_and_restart_need_admin_and_an_app_started_host(env, monkeypatch):
    c, h = env["client"], env["host"]
    r = c.post("/api/system/shutdown")
    assert r.status_code == 422 and "not started by the app" in r.json()["message"]

    class FakeServer:
        should_exit = False
    srv = FakeServer()
    c.app.state.server = srv
    spawned = []
    monkeypatch.setattr(desktop, "spawn_host", lambda extra, log, global_args=None: spawned.append((extra, global_args)))
    r = c.post("/api/system/restart")
    assert r.status_code == 200 and spawned[0][0][:3] == ["--wait-port", "--port", str(h.cfg.port)]
    assert spawned[0][1] == ["--data-dir", str(h.cfg.data_path)]
    for _ in range(50):
        if srv.should_exit:
            break
        time.sleep(0.05)
    assert srv.should_exit
    srv.should_exit = False
    assert c.post("/api/system/shutdown").json()["ok"] is True
    # a read-only device token cannot quit the app
    tok, _ = h.auth.create_token("phone", "cli", ["read"])
    assert c.post("/api/system/shutdown", headers={"Authorization": f"Bearer {tok}"}).status_code == 403


def test_desktop_helpers():
    assert desktop._quote_desktop("plain") == "plain"
    assert desktop._quote_desktop('a b"c') == '"a b\\"c"'
    cmd = desktop.app_command("start", "--managed")
    assert cmd[-2:] == ["start", "--managed"] and (cmd[1:3] == ["-m", "omos"] or desktop.frozen())
    assert 8765 <= desktop.pick_port(8765) <= 8799
    ka = desktop.KeepAwake()
    ka.set(False)
    assert ka.status()["active"] is False


def test_launcher_starts_a_background_host_and_the_app_can_quit_it(tmp_path):
    """The desktop icon path end to end: launch → detached host → healthy → quit from the API."""
    import subprocess
    home = tmp_path / "launch-home"
    port = free_port()
    env = {**os.environ, "OMOS_HOME": str(home), "OMOS_NO_BROWSER": "1", "OMOS_DISABLE_KEYRING": "1",
           "OMOS_VAULT_PASSPHRASE": "test vault passphrase", "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    subprocess.run([sys.executable, "-m", "omos", "config", "--set", f"port={port}"], env=env, check=True,
                   capture_output=True, timeout=60)
    r = subprocess.run([sys.executable, "-m", "omos", "launch"], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert desktop.healthy(port)
    tok = json.loads(subprocess.run([sys.executable, "-m", "omos", "token", "--scopes", "read,admin", "--days", "1"], env=env,
                                    capture_output=True, text=True, timeout=60).stdout)["token"]
    # launching again while it runs just opens another window (no second host)
    assert subprocess.run([sys.executable, "-m", "omos", "launch"], env=env, timeout=60).returncode == 0
    hdr = {"Authorization": f"Bearer {tok}"}
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10) as cl:
        assert cl.get("/api/system/status", headers=hdr).json()["port"] == port
        assert cl.post("/api/system/shutdown", headers=hdr).json()["ok"] is True
    for _ in range(100):
        if not desktop.healthy(port, timeout=0.5) and desktop.port_free(port):
            break
        time.sleep(0.2)
    assert desktop.port_free(port), "host did not exit after shutdown"
    log = (home / "logs" / "host-console.log")
    assert log.exists()
