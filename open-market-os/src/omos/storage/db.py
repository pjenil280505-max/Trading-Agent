"""SQLite access layer.

* One connection per thread (FastAPI runs sync handlers in a thread pool).
* WAL journal so the host, the MCP process and the CLI can read concurrently.
* Numbered migrations in ``migrations/NNN_*.sql`` applied in order at open.
* All statements use parameters; no SQL is ever built from user input.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from omos.util import dumps, loads, now_ms

log = logging.getLogger(__name__)


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    # -- connections -------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._connect()
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """A write transaction. Nested use re-enters the outer transaction."""
        depth = getattr(self._local, "depth", 0)
        conn = self.conn
        if depth == 0:
            self._write_lock.acquire()
            conn.execute("BEGIN IMMEDIATE")
        self._local.depth = depth + 1
        try:
            yield conn
        except BaseException:
            self._local.depth = depth
            if depth == 0:
                conn.execute("ROLLBACK")
                self._write_lock.release()
            raise
        else:
            self._local.depth = depth
            if depth == 0:
                conn.execute("COMMIT")
                self._write_lock.release()

    # -- helpers ------------------------------------------------------------------

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.tx() as c:
            return c.execute(sql, params)

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        with self.tx() as c:
            c.executemany(sql, rows)

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    # -- migrations ---------------------------------------------------------------

    def migrate(self) -> list[int]:
        conn = self.conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)"
        )
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        applied: list[int] = []
        files = sorted(
            (f for f in resources.files("omos.storage").joinpath("migrations").iterdir() if f.name.endswith(".sql")),
            key=lambda f: f.name,
        )
        for f in files:
            version = int(f.name.split("_", 1)[0])
            if version in done:
                continue
            sql = f.read_text(encoding="utf-8")
            with self._write_lock:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in _split_sql(sql):
                        conn.execute(stmt)
                    conn.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (version, now_ms())
                    )
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
            applied.append(version)
            log.info("applied migration %s", f.name)
        return applied

    def schema_version(self) -> int:
        return int(self.scalar("SELECT COALESCE(MAX(version), 0) FROM schema_migrations") or 0)

    # -- settings, audit, cache ----------------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, dumps(value), now_ms()),
        )

    def audit(self, actor: str, action: str, target: str | None = None, detail: Any = None, ip: str | None = None) -> None:
        self.execute(
            "INSERT INTO audit_log(ts, actor, action, target, detail, ip) VALUES (?, ?, ?, ?, ?, ?)",
            (now_ms(), actor, action, target, dumps(detail) if detail is not None else None, ip),
        )

    def cache_get(self, key: str) -> Any:
        row = self.query_one("SELECT value, expires_at FROM kv_cache WHERE key = ?", (key,))
        if not row or row["expires_at"] < now_ms():
            return None
        return loads(row["value"])

    def cache_set(self, key: str, value: Any, ttl_s: float) -> None:
        self.execute(
            "INSERT INTO kv_cache(key, value, expires_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at",
            (key, dumps(value), now_ms() + int(ttl_s * 1000)),
        )

    def heartbeat(self, component: str, info: Any = None) -> None:
        self.execute(
            "INSERT INTO heartbeats(component, ts, info) VALUES (?, ?, ?) "
            "ON CONFLICT(component) DO UPDATE SET ts = excluded.ts, info = excluded.info",
            (component, now_ms(), dumps(info) if info is not None else None),
        )

    def integrity_check(self) -> str:
        return str(self.scalar("PRAGMA integrity_check"))


def _split_sql(sql: str) -> list[str]:
    """Split a migration script on ';' at line ends, ignoring comment-only lines."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    parts, buf = [], []
    for ln in lines:
        buf.append(ln)
        if ln.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                parts.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts
