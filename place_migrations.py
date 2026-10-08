"""Additive, versioned migrations. Never replace or re-seed an existing database."""
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "20261008_place_categories_map"
DEFAULT_SLUGS = {"맛집": "food", "카페": "cafe", "가볼 곳": "attraction"}


def migration_applied(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
    return bool(exists and conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone())


def backup_before_migration(database):
    path = Path(database).resolve()
    if not path.exists() or not path.stat().st_size:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        if migration_applied(source):
            return None
        folder = path.parent / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d-%H%M%S-%f")
        backup = folder / f"jejuno-before-map-category-{stamp}.db"
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Database backup integrity check failed; migration stopped")
        print(f"[JEJUNO] Verified pre-migration backup: {backup}", flush=True)
        return str(backup)


def migrate_place_categories(database, backup_path=None):
    with closing(sqlite3.connect(database, timeout=30)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if migration_applied(conn):
                return
            conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                backup_path TEXT)""")
            conn.execute("""CREATE TABLE categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK(length(trim(name))>0),
                slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
                sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order>=0),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            conn.execute("ALTER TABLE places ADD COLUMN category_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT")
            conn.execute("ALTER TABLE places ADD COLUMN latitude REAL CHECK(latitude IS NULL OR latitude BETWEEN -90 AND 90)")
            conn.execute("ALTER TABLE places ADD COLUMN longitude REAL CHECK(longitude IS NULL OR longitude BETWEEN -180 AND 180)")
            names = [r[0] for r in conn.execute("SELECT DISTINCT category FROM places ORDER BY category")]
            # A genuinely empty installation still needs initial choices; existing DBs use only their own values.
            if not names:
                names = list(DEFAULT_SLUGS)
            names.sort(key=lambda name: (list(DEFAULT_SLUGS).index(name) if name in DEFAULT_SLUGS else 3, name))
            for order, name in enumerate(names, 1):
                slug = DEFAULT_SLUGS.get(name, f"category-{order}")
                cursor = conn.execute("INSERT INTO categories(name,slug,sort_order) VALUES (?,?,?)", (name, slug, order))
                conn.execute("UPDATE places SET category_id=? WHERE category=?", (cursor.lastrowid, name))
            conn.execute("CREATE INDEX places_category_id_idx ON places(category_id)")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Category migration failed foreign key validation")
            conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (VERSION, backup_path))
        print(f"[JEJUNO] Applied migration: {VERSION}", flush=True)
