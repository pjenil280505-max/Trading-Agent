"""In-app updates (checked against a fake GitHub API) and restoring a backup from the app."""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from omos import updates
from omos.errors import PermissionDenied, ValidationError
from omos.storage.backup import apply_pending_restore, backup_database, list_backups, request_restore
from omos.storage.db import Database
from omos.updates import Updater, parse_version


class FakeGitHub:
    def __init__(self):
        self.release: dict | None = None
        self.files: dict[str, bytes] = {}
        gh = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body: bytes, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.endswith("/releases/latest"):
                    if gh.release is None:
                        return self._send(404, b'{"message":"Not Found"}')
                    return self._send(200, json.dumps(gh.release).encode())
                name = self.path.rsplit("/", 1)[-1]
                if name in gh.files:
                    return self._send(200, gh.files[name], "application/octet-stream")
                self._send(404, b"")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, tag: str, assets: dict[str, bytes], *, listed: dict[str, str] | None = None):
        self.files = dict(assets)
        sums = listed if listed is not None else {n: hashlib.sha256(b).hexdigest() for n, b in assets.items()}
        self.files["SHA256SUMS.txt"] = "".join(f"{h}  {n}\n" for n, h in sums.items()).encode()
        self.release = {"tag_name": tag, "published_at": "2026-12-01T00:00:00Z", "body": "notes", "html_url": "x",
                        "assets": [{"name": n, "size": len(b), "browser_download_url": f"{self.base}/dl/{n}"}
                                   for n, b in self.files.items()]}


@pytest.fixture
def gh(monkeypatch):
    g = FakeGitHub()
    monkeypatch.setattr(updates, "asset_pattern", lambda: __import__("re").compile(r"-windows-x64-setup\.exe$"))
    yield g
    g.server.shutdown()


def up(tmp_path, gh, current="0.2.0"):
    return Updater(tmp_path, repo="o/r", api=gh.base, client=httpx.Client(trust_env=False, follow_redirects=True),
                   current=current)


def test_versions_compare_numerically():
    assert parse_version("v0.10.0") > parse_version("0.9.9") and parse_version("1.0.0") == (1, 0, 0)
    with pytest.raises(ValidationError):
        parse_version("latest")


def test_no_release_newer_release_and_missing_installer(tmp_path, gh):
    assert up(tmp_path, gh).check() == {"available": False, "current": "0.2.0", "latest": None,
                                        "message": "No published release yet."}
    gh.publish("v0.2.0", {"OpenMarketOS-0.2.0-windows-x64-setup.exe": b"same"})
    assert up(tmp_path, gh).check()["available"] is False
    gh.publish("v0.3.0", {"OpenMarketOS-0.3.0-macos-arm64.dmg": b"mac only"})
    res = up(tmp_path, gh).check()
    assert res["available"] is False and "no installer for this computer" in res["message"]
    gh.publish("v0.3.0", {"OpenMarketOS-0.3.0-windows-x64-setup.exe": b"new installer"})
    res = up(tmp_path, gh).check()
    assert res["available"] and res["latest"] == "v0.3.0" and res["asset"]["name"].endswith("setup.exe")


def test_download_is_verified_against_the_published_checksums(tmp_path, gh):
    gh.publish("v0.3.0", {"OpenMarketOS-0.3.0-windows-x64-setup.exe": b"genuine installer"})
    u = up(tmp_path, gh)
    path = u.download(u.check())
    assert path.read_bytes() == b"genuine installer" and not list(path.parent.glob("*.part"))
    gh.publish("v0.3.1", {"OpenMarketOS-0.3.1-windows-x64-setup.exe": b"tampered"},
               listed={"OpenMarketOS-0.3.1-windows-x64-setup.exe": "0" * 64})
    with pytest.raises(PermissionDenied, match="does not match"):
        u.download(u.check())
    assert not (tmp_path / "updates" / "OpenMarketOS-0.3.1-windows-x64-setup.exe").exists()
    gh.publish("v0.3.2", {"OpenMarketOS-0.3.2-windows-x64-setup.exe": b"unlisted"}, listed={})
    with pytest.raises(PermissionDenied, match="not listed"):
        u.download(u.check())


def test_this_computer_gets_its_own_installer(monkeypatch):
    monkeypatch.setattr(updates.sys, "platform", "darwin")
    monkeypatch.setattr(updates.platform, "machine", lambda: "arm64")
    assert updates.asset_pattern().search("OpenMarketOS-0.3.0-macos-arm64.dmg")
    monkeypatch.setattr(updates.sys, "platform", "linux")
    monkeypatch.setattr(updates.platform, "machine", lambda: "x86_64")
    monkeypatch.setenv("APPIMAGE", "/home/u/OpenMarketOS.AppImage")
    assert updates.asset_pattern().search("OpenMarketOS-0.3.0-x86_64.AppImage")


def test_restore_from_the_app_is_scheduled_applied_and_undoable(tmp_path):
    data, backups = tmp_path / "data", tmp_path / "data" / "backups"
    data.mkdir()
    db_path = data / "omos.db"
    db = Database(db_path)
    db.set_setting("marker", "before")
    db.close()
    snap = backup_database(db_path, backups, label="manual")
    db = Database(db_path)
    db.set_setting("marker", "after")
    db.close()
    with pytest.raises(ValidationError):
        request_restore(data, backups, "../omos.db", "ui")  # only listed backups
    with pytest.raises(ValidationError):
        request_restore(data, backups, "omos-nope.db", "ui")
    request_restore(data, backups, snap.name, "ui")
    res = apply_pending_restore(data, db_path, backups)
    assert res["ok"] and res["restored"] == snap.name and res["safety_copy"]
    db = Database(db_path)
    assert db.get_setting("marker") == "before"
    db.close()
    kinds = {b["file"]: b["kind"] for b in list_backups(backups)}
    assert kinds[res["safety_copy"]] == "before a restore"
    # undo: restore the safety copy
    request_restore(data, backups, res["safety_copy"], "ui")
    apply_pending_restore(data, db_path, backups)
    db = Database(db_path)
    assert db.get_setting("marker") == "after"
    db.close()
    assert apply_pending_restore(data, db_path, backups) is None  # nothing pending


def test_restore_api_needs_admin_and_schedules(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from omos.api.app import create_app
    from omos.config import HostConfig
    from omos.host import Host
    monkeypatch.setenv("OMOS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OMOS_VAULT_PASSPHRASE", "test vault passphrase")
    h = Host(HostConfig(data_dir=str(tmp_path / "home")), start_loops=False)
    c = TestClient(create_app(h, testing=True))
    tok = c.post("/api/auth/exchange", json={"code": h.auth.create_bootstrap_code(), "device_name": "t"}).json()["token"]
    c.headers["Authorization"] = f"Bearer {tok}"
    f = c.post("/api/backups").json()["file"]
    r = c.post("/api/backups/restore", json={"file": f}).json()
    assert r["file"] == f and r["restarting"] is False and "next time" in r["message"]
    assert (Path(h.cfg.data_path) / "restore-pending.json").exists()
    phone, _ = h.auth.create_token("phone", "cli", ["read"])
    assert c.post("/api/backups/restore", json={"file": f}, headers={"Authorization": f"Bearer {phone}"}).status_code == 403
    h.db.close()
