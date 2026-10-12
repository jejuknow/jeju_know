"""Add community participation without replacing any existing content or files."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "20261012_community_places"
KST = timezone(timedelta(hours=9))


def today():
    return datetime.now(KST).date()


def applied(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
                and conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone())


def backup_before_community_migration(database):
    path = Path(database).resolve()
    if not path.exists() or not path.stat().st_size:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        if applied(source):
            return None
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        target_path = folder / ("jejuno-before-community-places-" + datetime.now(KST).strftime("%Y%m%d-%H%M%S-%f") + ".db")
        with closing(sqlite3.connect(target_path)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Community migration backup failed; startup stopped")
        print(f"[JEJUNO] Verified community migration backup: {target_path}", flush=True)
        return str(target_path)


def migrate_community(database, backup_path):
    with closing(sqlite3.connect(database, timeout=30)) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        if applied(conn):
            return
        columns = {
            "submitted_by_user_id": "INTEGER REFERENCES users(id) ON DELETE RESTRICT",
            "source_type": "TEXT NOT NULL DEFAULT 'jejuno' CHECK(source_type IN ('jejuno','user'))",
            "verification_status": "TEXT NOT NULL DEFAULT 'unverified' CHECK(verification_status IN ('unverified','community_confirmed','jejuno_verified'))",
            "opened_year": "INTEGER CHECK(opened_year BETWEEN 1900 AND 9999)",
            "opened_month": "INTEGER CHECK(opened_month BETWEEN 1 AND 12)",
            "website_url": "TEXT NOT NULL DEFAULT ''",
            "new_override": "TEXT NOT NULL DEFAULT 'auto' CHECK(new_override IN ('auto','show','hide'))",
            "verified_at": "TEXT",
            "verified_by": "TEXT",
            "deleted_at": "TEXT",
            "merged_into_id": "INTEGER REFERENCES places(id) ON DELETE RESTRICT",
        }
        for name, definition in columns.items():
            conn.execute(f"ALTER TABLE places ADD COLUMN {name} {definition}")
        conn.execute("CREATE INDEX places_submitter_idx ON places(submitted_by_user_id)")
        conn.execute("CREATE INDEX places_merged_idx ON places(merged_into_id)")
        conn.execute("""CREATE TABLE place_correction_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            place_id INTEGER NOT NULL REFERENCES places(id) ON DELETE RESTRICT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            field TEXT NOT NULL, new_value TEXT NOT NULL, original_value TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT, reviewed_by TEXT, review_note TEXT NOT NULL DEFAULT '')""")
        conn.execute("CREATE INDEX place_suggestions_status_idx ON place_correction_suggestions(status,place_id)")
        conn.execute("""CREATE TABLE place_pick_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            place_id INTEGER NOT NULL REFERENCES places(id) ON DELETE RESTRICT,
            year INTEGER NOT NULL CHECK(year BETWEEN 1900 AND 9999),
            month INTEGER NOT NULL CHECK(month BETWEEN 1 AND 12),
            sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order BETWEEN 0 AND 9999),
            pick_note TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(place_id,year,month))""")
        conn.execute("CREATE INDEX picks_period_idx ON place_pick_periods(year,month,is_active,sort_order)")
        now = today()
        # Carry forward an existing editorial selection, never imply a visit/verification.
        conn.execute("""INSERT INTO place_pick_periods(place_id,year,month,sort_order,pick_note)
            SELECT id,?,?,id,reason FROM places WHERE is_pick=1""", (now.year, now.month))
        conn.execute("""CREATE TABLE best_collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            emoji TEXT NOT NULL DEFAULT '📍', sort_order INTEGER NOT NULL DEFAULT 0,
            published INTEGER NOT NULL DEFAULT 1 CHECK(published IN (0,1)))""")
        conn.execute("""CREATE TABLE best_collection_places (
            collection_id INTEGER NOT NULL REFERENCES best_collections(id) ON DELETE CASCADE,
            place_id INTEGER NOT NULL REFERENCES places(id) ON DELETE RESTRICT,
            sort_order INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(collection_id,place_id))""")
        # The previous three home collections were tag queries, not a separate database.
        # Freeze their existing membership once; later member submissions never auto-enter BEST.
        definitions = [
            ("parents", "부모님과 가기 좋은 제주", "부모님과 함께 둘러볼 장소를 모았습니다.", "👨‍👩‍👦"),
            ("soloCafe", "혼자 쉬기 좋은 카페", "혼자 들르기 좋은 카페를 모았습니다.", "☕"),
            ("rain", "비 오는 날의 제주", "비 오는 날 둘러볼 장소를 모았습니다.", "🌧️"),
        ]
        rows = conn.execute("SELECT * FROM places WHERE published=1").fetchall()
        for order, (slug, title, description, emoji) in enumerate(definitions):
            cid = conn.execute("INSERT INTO best_collections(slug,title,description,emoji,sort_order) VALUES (?,?,?,?,?)", (slug,title,description,emoji,order)).lastrowid
            for row in rows:
                def tags(value):
                    try:
                        result = json.loads(value or "[]")
                        return result if isinstance(result, list) else []
                    except (ValueError, TypeError):
                        return []
                companions, features = tags(row["companions"]), tags(row["features"])
                matches = ((slug == "parents" and "부모님" in companions)
                           or (slug == "soloCafe" and row["category"] == "카페" and "혼자" in companions)
                           or (slug == "rain" and any(v in features for v in ("비오는날","비올때","비 오는 날","비올때 추천"))))
                if matches:
                    conn.execute("INSERT INTO best_collection_places VALUES (?,?,?)", (cid,row["id"],row["id"]))
        conn.execute("""CREATE TABLE place_merge_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES places(id) ON DELETE RESTRICT,
            target_id INTEGER NOT NULL REFERENCES places(id) ON DELETE RESTRICT,
            merged_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Community migration foreign key validation failed")
        conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (VERSION,backup_path))
    print(f"[JEJUNO] Applied migration: {VERSION}", flush=True)
