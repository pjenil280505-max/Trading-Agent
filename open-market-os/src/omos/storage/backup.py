"""Backup, restore and recovery of the local database.

Backups use SQLite's online backup API, so they are consistent even while the
host is writing. A restore always keeps a safety copy of the database it
replaces, and refuses to install a file that fails ``PRAGMA integrity_check``.
"""

from __future__ import annotations

import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from omos.errors import OmosError, ValidationError


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def backup_database(db_path: Path, backups_dir: Path, *, label: str = "manual") -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(ch for ch in label if ch.isalnum() or ch in "-_")[:32] or "backup"
    target = backups_dir / f"omos-{_stamp()}-{safe_label}.db"
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    check = verify_database(target)
    if check != "ok":
        target.unlink(missing_ok=True)
        raise OmosError(f"backup failed integrity check: {check}")
    return target


def verify_database(path: Path) -> str:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            has_schema = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return f"not a valid database: {exc}"
    if not has_schema:
        return "not an Open Market OS database (no schema_migrations table)"
    return str(row[0])


def restore_database(backup_file: Path, db_path: Path, backups_dir: Path) -> Path | None:
    """Replace ``db_path`` with ``backup_file``. The host must be stopped first.

    Returns the path of the safety copy taken of the database being replaced.
    """
    backup_file = Path(backup_file)
    if not backup_file.is_file():
        raise ValidationError(f"backup file not found: {backup_file}")
    check = verify_database(backup_file)
    if check != "ok":
        raise ValidationError(f"refusing to restore: {check}")
    safety: Path | None = None
    if db_path.exists() and verify_database(db_path) == "ok":
        safety = backup_database(db_path, backups_dir, label="pre-restore")
    elif db_path.exists():
        safety = db_path.with_name(f"{db_path.name}.replaced-{_stamp()}")
        shutil.copy2(db_path, safety)
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    shutil.copy2(backup_file, db_path)
    return safety


def recover_corrupted(db_path: Path, backups_dir: Path) -> Path | None:
    """If the live database is corrupt, move it aside and restore the newest good backup.

    Returns the backup that was restored, or None if the database was healthy.
    """
    if not db_path.exists() or verify_database(db_path) == "ok":
        return None
    quarantine = db_path.with_name(f"{db_path.name}.corrupt-{_stamp()}")
    db_path.rename(quarantine)
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    candidates = sorted(backups_dir.glob("omos-*.db"), reverse=True)
    for cand in candidates:
        if verify_database(cand) == "ok":
            shutil.copy2(cand, db_path)
            return cand
    return None


def list_backups(backups_dir: Path) -> list[dict]:
    if not backups_dir.exists():
        return []
    out = []
    for f in sorted(backups_dir.glob("omos-*.db"), reverse=True):
        st = f.stat()
        out.append({"file": f.name, "bytes": st.st_size, "modified": int(st.st_mtime * 1000)})
    return out


def export_bundle(db_path: Path, exports_dir: Path, extra_files: list[Path] | None = None) -> Path:
    """Zip a consistent database snapshot plus host config, for moving to another machine."""
    exports_dir.mkdir(parents=True, exist_ok=True)
    snap = backup_database(db_path, exports_dir, label="export")
    bundle = exports_dir / f"omos-export-{_stamp()}.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(snap, "omos.db")
        for f in extra_files or []:
            if f.exists():
                zf.write(f, f.name)
    snap.unlink(missing_ok=True)
    return bundle
