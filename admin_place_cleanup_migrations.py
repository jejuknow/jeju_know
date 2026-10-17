"""Back up first; retain original focus JSON and add only exact taxonomy links."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "20261017_admin_place_form_cleanup"
FOCUS_TERMS = {"taste": "cafe-taste", "experience": "cafe-unique"}


def applied(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
                and conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone())


def backup_before_admin_place_cleanup(database):
    path = Path(database).resolve()
    if not path.exists() or not path.stat().st_size:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        if applied(source):
            return None
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        stamp = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d-%H%M%S-%f")
        backup = folder / f"jejuno-before-admin-place-form-cleanup-{stamp}.db"
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Admin place cleanup backup validation failed")
        print(f"[JEJUNO] Verified admin place cleanup backup: {backup}", flush=True)
        return str(backup)


def migrate_admin_place_cleanup(database, backup_path):
    with closing(sqlite3.connect(database, timeout=30)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        if applied(conn):
            return
        counts = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("places", "images", "place_category_links")}
        legacy = conn.execute("SELECT count(*) FROM places WHERE verification_status='community_confirmed'").fetchone()[0]
        mapped = {key: 0 for key in FOCUS_TERMS}
        for place_id, raw in conn.execute("SELECT p.id,p.cafe_focus FROM places p JOIN categories c ON c.id=p.category_id WHERE c.slug='cafe'"):
            try:
                focus = json.loads(raw or "[]")
            except (ValueError, TypeError):
                continue  # Preserve unrecognized legacy data as-is.
            if not isinstance(focus, list):
                continue
            for value, slug in FOCUS_TERMS.items():
                if value in focus:
                    cursor = conn.execute("INSERT OR IGNORE INTO place_category_links(place_id,category_id) SELECT ?,t.id FROM categories t JOIN categories p ON p.id=t.parent_id WHERE t.slug=? AND t.type='cafe_feature' AND p.slug='cafe'", (place_id, slug))
                    mapped[value] += cursor.rowcount
        conn.execute("UPDATE places SET verification_status='unverified',verified_at=NULL,verified_by=NULL WHERE verification_status='community_confirmed'")
        if any(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] < count for table, count in counts.items()) or conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Admin place cleanup preservation check failed")
        conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (VERSION, backup_path))
    print(f"[JEJUNO] Applied migration: {VERSION}; unverified={legacy}; added focus links={mapped}; preserved places/images", flush=True)
