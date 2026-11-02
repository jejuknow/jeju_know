"""Run additive migrations after copying and verifying the live SQLite database."""
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from community_migrations import KST


@contextmanager
def verified_migration(database, version, prefix):
    path = Path(database).resolve()
    receipt = "SELECT 1 FROM schema_migrations WHERE version=?"
    with closing(sqlite3.connect(path, timeout=30)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(receipt, (version,)).fetchone():
            yield None
            return
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
        backup = folder / f"{prefix}-{stamp}.db"
        # Never overwrite a manual backup made in the same second.
        if backup.exists():
            backup = backup.with_name(f"{backup.stem}-{datetime.now(KST).microsecond:06}.db")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(backup)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Database backup failed verification")
        yield conn
        if conn.execute("PRAGMA foreign_key_check").fetchone() or conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Database migration failed verification")
        conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (version, str(backup)))
    print(f"[JEJUNO] Applied {version}; verified backup: {backup}", flush=True)
