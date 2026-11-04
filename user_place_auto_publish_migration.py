"""Nullable inspection results; never change visibility of existing places."""
from sqlite_migrations import verified_migration

VERSION = "20261104_user_place_auto_publish"


def migrate(database):
    with verified_migration(database, VERSION, "jejuno-before-user-place-auto-publish") as conn:
        if conn is None:
            return
        columns = {r[1] for r in conn.execute("PRAGMA table_info(places)")}
        for name, declaration in {
            "auto_check_status": "TEXT CHECK(auto_check_status IN ('normal','review','hold'))",
            "auto_check_reason": "TEXT", "auto_checked_at": "TEXT",
        }.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE places ADD COLUMN {name} {declaration}")
        conn.execute("""CREATE INDEX IF NOT EXISTS places_auto_check_queue ON places(auto_check_status,id)
                        WHERE source_type='user' AND verification_status='unverified'
                        AND deleted_at IS NULL AND merged_into_id IS NULL""")
        conn.execute("INSERT OR IGNORE INTO site_settings(key,value) VALUES ('user_place_auto_publish','false')")
