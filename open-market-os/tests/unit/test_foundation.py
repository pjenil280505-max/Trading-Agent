import logging
import sqlite3

import pytest

from omos.config import HostConfig, load_config, save_config
from omos.logging_setup import RedactingFilter, redact
from omos.storage.backup import backup_database, recover_corrupted, restore_database, verify_database
from omos.storage.db import Database


def test_migrations_apply_once(db):
    assert db.schema_version() >= 1
    assert db.migrate() == []  # idempotent
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("candles", "orders", "bots", "risk_events", "auth_tokens", "audit_log", "drawings"):
        assert t in tables


def test_settings_roundtrip(db):
    db.set_setting("ui", {"mode": "beginner", "n": 3})
    assert db.get_setting("ui") == {"mode": "beginner", "n": 3}
    db.set_setting("ui", {"mode": "advanced"})
    assert db.get_setting("ui")["mode"] == "advanced"
    assert db.get_setting("missing", 42) == 42


def test_transaction_rollback(db):
    with pytest.raises(RuntimeError):
        with db.tx() as c:
            c.execute("INSERT INTO watchlists(name, created_at, updated_at) VALUES ('w', 1, 1)")
            raise RuntimeError("boom")
    assert db.scalar("SELECT COUNT(*) FROM watchlists") == 0


def test_nested_transactions_commit_once(db):
    with db.tx():
        db.execute("INSERT INTO watchlists(name, created_at, updated_at) VALUES ('a', 1, 1)")
        with db.tx():
            db.execute("INSERT INTO watchlists(name, created_at, updated_at) VALUES ('b', 1, 1)")
    assert db.scalar("SELECT COUNT(*) FROM watchlists") == 2


def test_cache_ttl(db, monkeypatch):
    import omos.storage.db as dbmod

    db.cache_set("k", {"v": 1}, ttl_s=10)
    assert db.cache_get("k") == {"v": 1}
    real = dbmod.now_ms()
    monkeypatch.setattr(dbmod, "now_ms", lambda: real + 11_000)
    assert db.cache_get("k") is None


def test_backup_restore_roundtrip(tmp_path):
    path = tmp_path / "live.db"
    db = Database(path)
    db.set_setting("marker", "before")
    backups = tmp_path / "backups"
    b = backup_database(path, backups, label="t")
    assert verify_database(b) == "ok"
    db.set_setting("marker", "after")
    db.close()
    safety = restore_database(b, path, backups)
    assert safety is not None and safety.exists()
    db2 = Database(path)
    assert db2.get_setting("marker") == "before"
    db2.close()


def test_restore_refuses_non_database(tmp_path):
    bogus = tmp_path / "bogus.db"
    bogus.write_text("not sqlite")
    from omos.errors import ValidationError

    with pytest.raises(ValidationError):
        restore_database(bogus, tmp_path / "live.db", tmp_path / "b")


def test_recover_corrupted_database(tmp_path):
    path = tmp_path / "live.db"
    db = Database(path)
    db.set_setting("marker", "good")
    backups = tmp_path / "backups"
    backup_database(path, backups)
    db.close()
    path.write_bytes(b"\x00" * 4096)  # simulate corruption
    for s in ("-wal", "-shm"):
        (tmp_path / f"live.db{s}").unlink(missing_ok=True)
    restored = recover_corrupted(path, backups)
    assert restored is not None
    db2 = Database(path)
    assert db2.get_setting("marker") == "good"
    db2.close()
    assert list(tmp_path.glob("live.db.corrupt-*"))


def test_redaction_patterns():
    s = redact("Authorization: Bearer abcdefghijklmnop12345 api_key=SECRETVALUE123 sk-ant-abcdefghijklmnopqrstu "
               "token=omos_ABCDEFGHIJKLMNOPQRST password: hunter22")
    assert "abcdefghijklmnop12345" not in s
    assert "SECRETVALUE123" not in s
    assert "abcdefghijklmnopqrstu" not in s
    assert "ABCDEFGHIJKLMNOPQRST" not in s
    assert "hunter22" not in s


def test_redacting_filter_on_records():
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "key: api_key=%s", ("SUPERSECRET99",), None)
    RedactingFilter().filter(rec)
    assert "SUPERSECRET99" not in rec.getMessage()


def test_host_config_defaults_to_loopback(tmp_path):
    cfg = HostConfig(data_dir=str(tmp_path), host="0.0.0.0", allow_remote=False)
    assert cfg.effective_bind() == "127.0.0.1"
    cfg.allow_remote = True
    assert cfg.effective_bind() == "0.0.0.0"
    save_config(cfg)
    loaded = load_config(tmp_path)
    assert loaded.allow_remote is True and loaded.host == "0.0.0.0"


def test_wal_mode(db):
    assert db.scalar("PRAGMA journal_mode") == "wal"
    assert db.scalar("PRAGMA foreign_keys") == 1


def test_parameterised_queries_resist_injection(db):
    evil = "x'); DROP TABLE settings; --"
    db.set_setting(evil, 1)
    assert db.get_setting(evil) == 1
    assert db.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name='settings'") == 1
    with pytest.raises(sqlite3.ProgrammingError):
        db.conn.execute("SELECT 1; SELECT 2")
