"""Add optional ISSUE location and time fields without rewriting existing posts."""
from sqlite_migrations import verified_migration

VERSION = "20261102_issue_hybrid_map"
COLUMNS = {"start_time": "TEXT NOT NULL DEFAULT ''", "end_time": "TEXT NOT NULL DEFAULT ''",
           "region": "TEXT NOT NULL DEFAULT ''", "address": "TEXT NOT NULL DEFAULT ''",
           "latitude": "REAL", "longitude": "REAL",
           "show_on_map": "INTEGER NOT NULL DEFAULT 0 CHECK(show_on_map IN (0,1))"}


def apply_columns(database, version, prefix, columns, index):
    with verified_migration(database, version, prefix) as conn:
        if conn is None:
            return
        existing = {r[1] for r in conn.execute("PRAGMA table_info(news_posts)")}
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE news_posts ADD COLUMN {name} {declaration}")
        conn.execute(index)


def migrate(database):
    apply_columns(database, VERSION, "jejuno-before-issue-hybrid-map", COLUMNS,
                  "CREATE INDEX IF NOT EXISTS news_public_calendar ON news_posts(event_end,event_start) WHERE published=1 AND deleted_at IS NULL")
