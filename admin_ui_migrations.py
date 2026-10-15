"""Back up before the minimal settings/BEST metadata migration."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

VERSION = "20261015_admin_ui_redesign"


def applied(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
                and conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone())


def backup_before_admin_ui(database):
    path = Path(database).resolve()
    if not path.exists() or not path.stat().st_size:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        if applied(source):
            return None
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        backup = folder / ("jejuno-before-admin-ui-redesign-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + ".db")
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Admin UI backup failed; startup stopped")
        print(f"[JEJUNO] Verified admin UI backup: {backup}", flush=True)
        return str(backup)


def migrate_admin_ui(database, backup_path):
    with closing(sqlite3.connect(database, timeout=30)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        if applied(conn):
            return
        conn.execute("CREATE TABLE site_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("ALTER TABLE best_collections ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE best_collections ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE best_collections ADD COLUMN deleted_at TEXT")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Admin UI migration foreign key check failed")
        conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (VERSION, backup_path))
    print(f"[JEJUNO] Applied migration: {VERSION}; existing places, users and images preserved", flush=True)
