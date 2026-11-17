"""SQL operational queues and transactionally invalidated duplicate snapshots."""
from place_freshness import STALE, NEVER
from sqlite_migrations import verified_migration
import hashlib
import json

ACTIVE = "p.deleted_at IS NULL AND p.merged_into_id IS NULL"
MISSING_COORDINATES = "(p.latitude IS NULL OR p.longitude IS NULL)"
# Match ImageStore.decorate: primary local/reference image, legacy URL, merged gallery fallback.
MISSING_IMAGE = """(NULLIF(TRIM(p.image_url),'') IS NULL
    AND NOT EXISTS(SELECT 1 FROM images i WHERE i.place_id=p.id AND i.sort_order=0)
    AND NOT EXISTS(SELECT 1 FROM place_image_references r WHERE r.place_id=p.id AND r.sort_order=0)
    AND NOT EXISTS(SELECT 1 FROM places child WHERE child.merged_into_id=p.id AND
        (NULLIF(TRIM(child.image_url),'') IS NOT NULL
         OR EXISTS(SELECT 1 FROM images i WHERE i.place_id=child.id)
         OR EXISTS(SELECT 1 FROM place_image_references r WHERE r.place_id=child.id))))"""


def counts(conn, cutoff):
    return dict(conn.execute(f"""SELECT
        count(*) FILTER (WHERE {MISSING_COORDINATES}) AS coordinates,
        count(*) FILTER (WHERE {MISSING_IMAGE}) AS images,
        count(*) FILTER (WHERE {STALE}) AS stale,
        count(*) FILTER (WHERE {NEVER}) AS never
        FROM places p WHERE {ACTIVE}""", (cutoff,)).fetchone())


def migrate(database):
    # One metadata row prevents scanning/materializing every place on every admin page.
    # Triggers cover all writers (including SQL/CSV), with rollback-safe invalidation.
    with verified_migration(database, "20261117_operational_health", "jejuno-before-operational-health") as conn:
        if conn is None:
            return
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        def fingerprint():
            result = {}
            for table in tables:
                name = '"' + table.replace('"', '""') + '"'
                # Stable multiset comparison, including tables without an integer primary key.
                rows = sorted(hashlib.sha256(repr(tuple(row)).encode()).digest() for row in conn.execute("SELECT * FROM " + name))
                result[table] = (len(rows), hashlib.sha256(b''.join(rows)).hexdigest())
            return result
        before = fingerprint()
        conn.execute("CREATE TABLE place_duplicate_revision(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL)")
        conn.execute("INSERT INTO place_duplicate_revision VALUES(1,0)")
        from place_duplicates import FIELDS
        columns = ",".join((*FIELDS, "deleted_at", "merged_into_id"))
        for table in ("places", "place_duplicate_ignores"):
            for operation in ("INSERT", "UPDATE", "DELETE"):
                event = "UPDATE OF " + columns if table == "places" and operation == "UPDATE" else operation
                conn.execute(f"""CREATE TRIGGER duplicate_revision_{table}_{operation.lower()} AFTER {event} ON {table}
                    BEGIN UPDATE place_duplicate_revision SET revision=revision+1 WHERE id=1; END""")
        if before != fingerprint():
            raise RuntimeError("Operational migration changed existing records")
        print("[JEJUNO] Operational migration: all existing table rows unchanged; counts=" +
              json.dumps({name: before[name][0] for name in ("places", "users", "news_posts", "images")}), flush=True)
