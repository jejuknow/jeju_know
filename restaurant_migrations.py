"""Versioned restaurant hierarchy; stable IDs and links survive the upgrade."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

VERSION = "20261020_restaurant_taxonomy"
# Slugs of existing terms stay unchanged, including legacy shared URLs.
TREE = [
    ("korean", "한식", [("soup", "국밥·해장국"), ("stew", "찌개·전골"), ("set-meal", "백반·정식"), ("grill", "고기·구이"), ("pork", "돼지고기"), ("beef", "소고기"), ("poultry", "닭·오리"), ("seafood", "해산물"), ("raw-fish", "회·물회"), ("fish", "생선구이·조림"), ("noodles", "국수·면"), ("cold-noodles", "냉면·밀면"), ("porridge", "죽·수제비"), ("hanjeongsik", "한정식"), ("korean-other", "기타 한식")]),
    ("japanese", "일식", [("sushi", "초밥·스시"), ("sashimi", "회·사시미"), ("pork-cutlet", "돈가스"), ("ramen", "라멘"), ("udon", "우동"), ("soba", "소바"), ("rice-bowl", "덮밥"), ("izakaya", "이자카야"), ("japanese-other", "기타 일식")]),
    ("chinese", "중식", [("jajang", "짜장면"), ("jjamppong", "짬뽕"), ("tangsuyuk", "탕수육"), ("dim-sum", "딤섬·만두"), ("chinese-dishes", "중식요리"), ("chinese-other", "기타 중식")]),
    ("western", "양식", [("pasta", "파스타"), ("pizza", "피자"), ("steak", "스테이크"), ("brunch", "브런치"), ("sandwich", "샌드위치"), ("craft-burger", "수제버거"), ("western-other", "기타 양식")]),
    ("asia", "아시아", [("vietnamese", "베트남"), ("thai", "태국"), ("indian", "인도"), ("mexican", "멕시코"), ("southeast-asian", "동남아 기타"), ("world-other", "기타 세계음식")]),
    ("snack", "분식", [("gimbap", "김밥"), ("tteokbokki", "떡볶이"), ("instant-noodles", "라면"), ("dumplings", "만두"), ("fried-snacks", "튀김"), ("snack-other", "분식 기타")]),
    ("fast-food", "패스트푸드", [("burger", "햄버거"), ("fried-chicken", "치킨"), ("hot-dog", "핫도그"), ("fast-food-other", "기타")]),
    ("other", "기타", []),
]


def applied(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
                and conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone())


def backup_before_restaurants(database):
    path = Path(database).resolve()
    if not path.exists() or not path.stat().st_size:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        if applied(source):
            return None
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        backup = folder / ("jejuno-before-restaurant-taxonomy-upgrade-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + ".db")
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Restaurant backup failed; migration stopped")
        print(f"[JEJUNO] Verified restaurant backup: {backup}", flush=True)
        return str(backup)


def migrate_restaurants(database, backup_path):
    with closing(sqlite3.connect(database, timeout=30)) as conn, conn:
        if applied(conn):
            return
        # SQLite's documented table rebuild procedure: FK checks run before commit.
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='categories'").fetchone()[0]
        indexes = [r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name='categories' AND type IN ('index','trigger') AND sql IS NOT NULL")]
        sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='categories'").fetchone()
        schema = schema.replace("CREATE TABLE categories", "CREATE TABLE categories_upgrade", 1)
        schema = schema.replace("name TEXT NOT NULL UNIQUE COLLATE NOCASE", "name TEXT NOT NULL COLLATE NOCASE", 1)
        conn.execute(schema)
        conn.execute("INSERT INTO categories_upgrade SELECT * FROM categories")
        conn.execute("DROP TABLE categories")
        conn.execute("ALTER TABLE categories_upgrade RENAME TO categories")
        for sql in indexes:
            conn.execute(sql)
        conn.execute("CREATE UNIQUE INDEX categories_sibling_name ON categories(parent_id,name COLLATE NOCASE) WHERE parent_id IS NOT NULL")
        conn.execute("CREATE UNIQUE INDEX categories_root_name ON categories(name COLLATE NOCASE) WHERE parent_id IS NULL")
        conn.execute("ALTER TABLE categories ADD COLUMN is_legacy INTEGER NOT NULL DEFAULT 0 CHECK(is_legacy IN (0,1))")
        if sequence:
            conn.execute("UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name='categories'", (sequence[0],))
        food = conn.execute("SELECT id FROM categories WHERE slug='food' AND type='place'").fetchone()
        if food:
            # Ambiguous/custom flat values remain readable and linked, outside new choices.
            conn.execute("UPDATE categories SET is_legacy=1 WHERE type='restaurant_subcategory'")
            def upsert(key, name, parent, order):
                slug = "food-" + key
                aliases = ["베트남식"] if key == "vietnamese" else []
                row = conn.execute("SELECT id,name,legacy_aliases FROM categories WHERE type='restaurant_subcategory' AND (slug=? OR name=?) ORDER BY slug=? DESC,id LIMIT 1", (slug, "베트남식" if key == "vietnamese" else name, slug)).fetchone()
                # The second '기타' is a new child, never reuse the major.
                if key == "fast-food-other":
                    row = None
                if row:
                    aliases = list(dict.fromkeys(json.loads(row[2]) + aliases + ([row[1]] if row[1] != name else [])))
                    conn.execute("UPDATE categories SET name=?,parent_id=?,sort_order=?,is_legacy=0,legacy_aliases=? WHERE id=?", (name,parent,order,json.dumps(aliases,ensure_ascii=False),row[0]))
                    return row[0]
                return conn.execute("INSERT INTO categories(name,slug,type,parent_id,sort_order,legacy_aliases) VALUES (?,?,'restaurant_subcategory',?,?,?)", (name,slug,parent,order,json.dumps(aliases,ensure_ascii=False))).lastrowid
            # Create majors before children; existing rows retain activation status.
            majors = [(upsert(key,name,food[0],order), children) for order,(key,name,children) in enumerate(TREE,1)]
            for parent, children in majors:
                for order,(key,name) in enumerate(children,1):
                    upsert(key,name,parent,order)
        if conn.execute("PRAGMA foreign_key_check").fetchall() or conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Restaurant migration integrity validation failed")
        conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (VERSION,backup_path))
    print(f"[JEJUNO] Applied migration: {VERSION}", flush=True)
