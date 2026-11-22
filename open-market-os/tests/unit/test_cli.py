"""CLI helpers: `omos open/status/mcp` must find a host that was started with a non-default --port."""

import json
import os
import socket

from omos import cli
from omos.config import HostConfig


def _cfg(tmp_path) -> HostConfig:
    cfg = HostConfig(data_dir=str(tmp_path))
    cfg.ensure_dirs()
    return cfg


def test_runtime_file_redirects_to_the_live_port(tmp_path):
    cfg = _cfg(tmp_path)
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        live_port = srv.getsockname()[1]
        cfg.port = live_port
        path = cli._write_runtime(cfg)
        assert json.loads(path.read_text())["pid"] == os.getpid()
        other = _cfg(tmp_path)
        assert other.port != live_port
        assert cli._apply_runtime(other).port == live_port


def test_runtime_file_ignored_when_port_is_dead_or_file_is_bad(tmp_path):
    cfg = _cfg(tmp_path)
    with socket.socket() as s:  # find a free port, then close it so nothing listens there
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
    (cfg.data_path / cli.RUNTIME_FILE).write_text(json.dumps({"pid": os.getpid(), "port": dead_port}))
    default = _cfg(tmp_path).port
    assert cli._apply_runtime(_cfg(tmp_path)).port == default
    (cfg.data_path / cli.RUNTIME_FILE).write_text("{not json")
    assert cli._apply_runtime(_cfg(tmp_path)).port == default


def test_clear_runtime_only_removes_our_own_file(tmp_path):
    cfg = _cfg(tmp_path)
    path = cfg.data_path / cli.RUNTIME_FILE
    path.write_text(json.dumps({"pid": os.getpid() + 999_999, "port": 1}))  # another host's file
    cli._clear_runtime(path)
    assert path.exists()
    cli._write_runtime(cfg)
    cli._clear_runtime(path)
    assert not path.exists()
