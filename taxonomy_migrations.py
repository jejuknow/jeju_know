"""Back up first, then add classification links without rewriting any place tags."""
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

VERSION = "20261011_place_filter_redesign"
SEED_PATH = Path(__file__).parent / "static" / "place-taxonomy-seed.json"
TYPES = {"place": "장소 상위 카테고리", "restaurant_subcategory": "맛집 세부유형", "cafe_feature": "카페 특징"}


def applied(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
                and conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone())


def backup_before_taxonomy_migration(database):
    path = Path(database).resolve()
    if not path.exists() or not path.stat().st_size:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        if applied(source):
            return None
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        backup = folder / ("jejuno-before-place-filter-redesign-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + ".db")
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Taxonomy backup failed; migration stopped")
        print(f"[JEJUNO] Verified taxonomy backup: {backup}", flush=True)
        return str(backup)


def normalize(value):
    return re.sub(r"\s+", "", re.sub(r"\s*추천$", "", str(value).strip())).lower()


def json_list(value):
    try:
        result = json.loads(value or "[]")
        return result if isinstance(result, list) else []
    except (ValueError, TypeError):
        return []


def legacy_term_ids(conn, category_id, features):
    tags = {normalize(v) for v in features}
    return [row[0] for row in conn.execute("WITH RECURSIVE tree AS (SELECT id,name,legacy_aliases FROM categories WHERE parent_id=? UNION ALL SELECT c.id,c.name,c.legacy_aliases FROM categories c JOIN tree p ON c.parent_id=p.id) SELECT * FROM tree", (category_id,))
            if tags.intersection(normalize(v) for v in [row[1], *json_list(row[2])])]


def migrate_taxonomy(database, backup_path):
    with closing(sqlite3.connect(database, timeout=30)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        if applied(conn):
            return
        conn.execute("ALTER TABLE categories ADD COLUMN type TEXT NOT NULL DEFAULT 'place' CHECK(type IN ('place','restaurant_subcategory','cafe_feature'))")
        conn.execute("ALTER TABLE categories ADD COLUMN parent_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT")
        conn.execute("ALTER TABLE categories ADD COLUMN legacy_aliases TEXT NOT NULL DEFAULT '[]'")
        conn.execute("CREATE TABLE place_category_links (place_id INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE, category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT, PRIMARY KEY(place_id,category_id))")
        conn.execute("CREATE INDEX place_category_links_category_idx ON place_category_links(category_id)")
        seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        for kind, parent_slug in (("restaurant_subcategory", "food"), ("cafe_feature", "cafe")):
            parent = conn.execute("SELECT id FROM categories WHERE slug=?", (parent_slug,)).fetchone()
            if not parent:
                continue  # Never invent or reclassify an existing top-level category.
            for order, item in enumerate(seed[kind], 1):
                conn.execute("INSERT INTO categories(name,slug,type,parent_id,sort_order,legacy_aliases) VALUES (?,?,?,?,?,?)",
                             (item["label"], parent_slug + "-" + item["id"], kind, parent[0], order, json.dumps(item.get("aliases", []), ensure_ascii=False)))
        for place_id, category_id, features in conn.execute("SELECT id,category_id,features FROM places").fetchall():
            conn.executemany("INSERT OR IGNORE INTO place_category_links VALUES (?,?)", [(place_id, term_id) for term_id in legacy_term_ids(conn, category_id, json_list(features))])
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Taxonomy migration failed foreign key validation")
        conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (VERSION, backup_path))
    print(f"[JEJUNO] Applied migration: {VERSION}", flush=True)
