"""Upgrade managed cafe features without guessing or deleting place relationships."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

VERSION = "20261020_cafe_feature_bakery"
FEATURES = (
    ("coffee", "커피 전문"), ("dessert", "디저트 전문"), ("bakery", "베이커리"),
    ("view", "좋은 뷰"), ("mood", "감성 공간"), ("quiet", "조용한 공간"),
    ("healing", "힐링"), ("work", "작업하기 좋은"), ("garden", "야외·정원"),
    ("local", "제주다움"), ("space", "넓은 공간"), ("premium", "포토스팟"),
    ("unique", "이색 경험"), ("popular", "인기 핫플"), ("value", "가성비"),
)


def applied(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
                and conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone())


def backup_before_cafe_features(database):
    path = Path(database).resolve()
    if not path.exists() or not path.stat().st_size:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        if applied(source):
            return None
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        backup = folder / ("jejuno-before-cafe-feature-bakery-update-" +
                           datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + ".db")
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Cafe feature backup failed; migration stopped")
        print(f"[JEJUNO] Verified cafe feature backup: {backup}", flush=True)
        return str(backup)


def migrate_cafe_features(database, backup_path):
    with closing(sqlite3.connect(database, timeout=30)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        if applied(conn):
            return
        before = conn.execute("SELECT * FROM place_category_links ORDER BY place_id,category_id").fetchall()
        conn.execute("ALTER TABLE categories ADD COLUMN member_policy TEXT NOT NULL DEFAULT 'direct' CHECK(member_policy IN ('direct','review','admin'))")
        root = conn.execute("SELECT id FROM categories WHERE slug='cafe' AND type='place'").fetchone()
        if root:
            conn.execute("UPDATE categories SET is_legacy=1,member_policy='admin' WHERE type='cafe_feature'")
            for order, (key, name) in enumerate(FEATURES, 1):
                # Prefer former names so custom slugs, IDs and links remain stable.
                old_name = {"dessert": "디저트 좋은", "premium": "고급 분위기"}.get(key, name)
                row = conn.execute("SELECT id,name,legacy_aliases FROM categories WHERE type='cafe_feature' AND (name=? OR name=? OR slug=?) ORDER BY name=? DESC,id LIMIT 1",
                                   (old_name, name, "cafe-" + key, old_name)).fetchone()
                policy = "admin" if key == "popular" else "review" if key == "value" else "direct"
                if row:
                    aliases = list(dict.fromkeys(json.loads(row[2]) + ([row[1]] if row[1] != name else [])))
                    conn.execute("UPDATE categories SET name=?,parent_id=?,sort_order=?,is_active=1,is_legacy=0,member_policy=?,legacy_aliases=? WHERE id=?",
                                 (name, root[0], order, policy, json.dumps(aliases, ensure_ascii=False), row[0]))
                else:
                    conn.execute("INSERT INTO categories(name,slug,type,parent_id,sort_order,member_policy) VALUES (?,?,'cafe_feature',?,?,?)",
                                 (name, "cafe-" + key, root[0], order, policy))
            conn.execute("UPDATE categories SET is_active=0 WHERE type='cafe_feature' AND is_legacy=1")
        after = conn.execute("SELECT * FROM place_category_links ORDER BY place_id,category_id").fetchall()
        if before != after or conn.execute("PRAGMA foreign_key_check").fetchall() or conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Cafe feature preservation validation failed")
        conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (VERSION, backup_path))
    print(f"[JEJUNO] Applied migration: {VERSION}; existing cafe links preserved", flush=True)
