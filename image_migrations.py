"""Add image metadata only, after a verified SQLite online backup."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

VERSION = "20261011_image_uploads"


def applied(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
                and conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone())


def backup_before_image_migration(database):
    path = Path(database).resolve()
    if not path.exists() or not path.stat().st_size:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        if applied(source):
            return None
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        backup = folder / f"jejuno-before-image-upload-{stamp}.db"
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Image migration backup failed; startup stopped")
        print(f"[JEJUNO] Verified image migration backup: {backup}", flush=True)
        return str(backup)


def migrate_images(database, backup_path):
    with closing(sqlite3.connect(database, timeout=30)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        if applied(conn):
            return
        conn.execute("""CREATE TABLE images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            place_id INTEGER REFERENCES places(id) ON DELETE CASCADE,
            issue_id INTEGER REFERENCES news_posts(id) ON DELETE CASCADE,
            file_path TEXT NOT NULL,
            mime_type TEXT NOT NULL DEFAULT 'image/webp' CHECK(mime_type='image/webp'),
            width INTEGER NOT NULL CHECK(width>0), height INTEGER NOT NULL CHECK(height>0),
            byte_size INTEGER NOT NULL CHECK(byte_size>0),
            sort_order INTEGER NOT NULL CHECK(sort_order BETWEEN 0 AND 4),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK((place_id IS NOT NULL AND issue_id IS NULL) OR
                  (place_id IS NULL AND issue_id IS NOT NULL AND sort_order=0)),
            UNIQUE(place_id,sort_order), UNIQUE(issue_id,sort_order))""")
        conn.execute("CREATE INDEX images_path_idx ON images(file_path)")
        # Deletes are retried after restart if the filesystem is temporarily unavailable.
        conn.execute("CREATE TABLE image_file_gc (file_path TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (VERSION, backup_path))
    print(f"[JEJUNO] Applied migration: {VERSION}", flush=True)
