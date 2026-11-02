"""Add optional ISSUE location and time fields without rewriting existing posts."""
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from community_migrations import KST

VERSION = "20261102_issue_hybrid_map"
COLUMNS = {"start_time": "TEXT NOT NULL DEFAULT ''", "end_time": "TEXT NOT NULL DEFAULT ''",
           "region": "TEXT NOT NULL DEFAULT ''", "address": "TEXT NOT NULL DEFAULT ''",
           "latitude": "REAL", "longitude": "REAL",
           "show_on_map": "INTEGER NOT NULL DEFAULT 0 CHECK(show_on_map IN (0,1))"}


def migrate(database):
    path = Path(database).resolve()
    receipt = "SELECT 1 FROM schema_migrations WHERE version=?"
    with closing(sqlite3.connect(path, timeout=30)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(receipt, (VERSION,)).fetchone():
            return
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
        backup = folder / f"jejuno-before-issue-hybrid-map-{stamp}.db"
        # Never overwrite a manual backup made in the same second.
        if backup.exists():
            backup = backup.with_name(f"{backup.stem}-{datetime.now(KST).microsecond:06}.db")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(backup)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("ISSUE backup failed verification")
        existing = {r[1] for r in conn.execute("PRAGMA table_info(news_posts)")}
        for name, declaration in COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE news_posts ADD COLUMN {name} {declaration}")
        conn.execute("CREATE INDEX IF NOT EXISTS news_public_calendar ON news_posts(event_end,event_start) WHERE published=1 AND deleted_at IS NULL")
        if conn.execute("PRAGMA foreign_key_check").fetchone() or conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("ISSUE migration failed verification")
        conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (VERSION, str(backup)))
    print(f"[JEJUNO] Applied {VERSION}; verified backup: {backup}", flush=True)
