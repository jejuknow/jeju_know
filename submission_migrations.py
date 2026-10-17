"""Add only missing price/payment facts; keep all previous values and relations."""
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "20261017_submission_form_upgrade"


def applied(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
                and conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone())


def backup_before_submission(database):
    path = Path(database).resolve()
    if not path.exists() or not path.stat().st_size:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        if applied(source):
            return None
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        stamp = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d-%H%M%S-%f")
        backup = folder / f"jejuno-before-submission-form-upgrade-{stamp}.db"
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Submission backup validation failed")
        print(f"[JEJUNO] Verified submission backup: {backup}", flush=True)
        return str(backup)


def migrate_submission(database, backup_path):
    with closing(sqlite3.connect(database, timeout=30)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        if applied(conn):
            return
        columns = {r[1] for r in conn.execute("PRAGMA table_info(places)")}
        count = conn.execute("SELECT count(*) FROM places").fetchone()[0]
        for name, definition in {"representative_price": "INTEGER CHECK(representative_price IS NULL OR representative_price BETWEEN 0 AND 99999999)",
                                 "tamnaneunjeon_status": "TEXT NOT NULL DEFAULT '미확인' CHECK(tamnaneunjeon_status IN ('미확인','가능','불가'))"}.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE places ADD COLUMN {name} {definition}")
        if count != conn.execute("SELECT count(*) FROM places").fetchone()[0] or conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Submission migration validation failed")
        conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (VERSION, backup_path))
    print(f"[JEJUNO] Applied migration: {VERSION}; preserved {count} places", flush=True)
