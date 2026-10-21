"""Add cafe visit times and approval policy without rewriting existing place facts."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

VERSION = "20261021_cafe_flow"


def applied(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
                and conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone())


def backup_before_cafe_flow(database):
    path = Path(database).resolve()
    if not path.exists() or not path.stat().st_size:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        if applied(source):
            return None
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        backup = folder / ("jejuno-before-cafe-flow-redesign-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + ".db")
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Cafe flow backup failed; migration stopped")
        print(f"[JEJUNO] Verified cafe flow backup: {backup}", flush=True)
        return str(backup)


def migrate_cafe_flow(database, backup_path):
    with closing(sqlite3.connect(database, timeout=30)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        if applied(conn):
            return
        columns = [r[1] for r in conn.execute("PRAGMA table_info(places)")]
        projection = ','.join('"' + c + '"' for c in columns)
        before = conn.execute(f"SELECT {projection} FROM places ORDER BY id").fetchall()
        links = conn.execute("SELECT * FROM place_category_links ORDER BY place_id,category_id").fetchall()
        if "cafe_visit_times" not in columns:
            conn.execute("ALTER TABLE places ADD COLUMN cafe_visit_times TEXT NOT NULL DEFAULT '[]'")
        conn.execute("UPDATE categories SET member_policy='review' WHERE type='cafe_feature' AND is_legacy=0 AND member_policy='direct'")
        if (before != conn.execute(f"SELECT {projection} FROM places ORDER BY id").fetchall()
                or links != conn.execute("SELECT * FROM place_category_links ORDER BY place_id,category_id").fetchall()
                or conn.execute("PRAGMA foreign_key_check").fetchall()
                or conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"):
            raise RuntimeError("Cafe flow preservation validation failed")
        conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (VERSION, backup_path))
    print(f"[JEJUNO] Applied migration: {VERSION}; existing places and links preserved", flush=True)
