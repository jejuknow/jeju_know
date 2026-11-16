"""Add editorial rankings without changing existing collections or place taxonomy."""
from sqlite_migrations import verified_migration

VERSION = '20261116_restaurant_rankings'


def migrate(database):
    with verified_migration(database, VERSION, 'jejuno-before-restaurant-ranking') as conn:
        if conn is None:
            return
        conn.execute("""CREATE TABLE restaurant_rankings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subtype_taxonomy_id INTEGER NOT NULL UNIQUE REFERENCES categories(id) ON DELETE RESTRICT,
            title TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
            intro TEXT NOT NULL DEFAULT '', criteria_note TEXT NOT NULL DEFAULT '', reference_date TEXT,
            is_public INTEGER NOT NULL DEFAULT 0 CHECK(is_public IN (0,1)), deleted_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE restaurant_ranking_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ranking_id INTEGER NOT NULL REFERENCES restaurant_rankings(id) ON DELETE RESTRICT,
            place_id INTEGER NOT NULL REFERENCES places(id) ON DELETE RESTRICT,
            sort_order INTEGER NOT NULL CHECK(sort_order>0), reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ranking_id,place_id))""")
        conn.execute('CREATE INDEX ranking_items_order ON restaurant_ranking_items(ranking_id,sort_order,id)')
        conn.execute('CREATE INDEX ranking_items_place ON restaurant_ranking_items(place_id)')
