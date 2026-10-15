"""Add an empty optional field; never infer orientation from existing cafe tags."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

VERSION = "20261015_cafe_focus"


def applied(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
                and conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone())


def backup_before_cafe_focus(database):
    path = Path(database).resolve()
    if not path.exists() or not path.stat().st_size:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        if applied(source):
            return None
        folder = path.parent / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        backup = folder / f"jejuno-before-cafe-focus-{stamp}.db"
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Cafe focus backup failed; migration stopped")
        print(f"[JEJUNO] Verified cafe focus backup: {backup}", flush=True)
        return str(backup)


def migrate_cafe_focus(database, backup_path):
    with closing(sqlite3.connect(database, timeout=30)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        if applied(conn):
            return
        count = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        conn.execute("ALTER TABLE places ADD COLUMN cafe_focus TEXT NOT NULL DEFAULT '[]'")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Cafe focus migration failed foreign key validation")
        conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (VERSION, backup_path))
    print(f"[JEJUNO] Applied migration: {VERSION}; preserved {count} places; no orientations assigned", flush=True)
