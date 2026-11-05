"""Extend only the correction status constraint, preserving every existing row."""
from sqlite_migrations import verified_migration

VERSION = "20261105_correction_resolved"


def migrate(database):
    with verified_migration(database, VERSION, "jejuno-before-correction-resolved") as conn:
        if conn is None:
            return
        table = "place_correction_suggestions"
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        old = "CHECK(status IN ('pending','accepted','rejected'))"
        if old not in schema:
            raise RuntimeError("Unexpected correction status constraint; migration stopped")
        # No table currently references corrections. Stop safely if that changes.
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            quoted = '"' + name.replace('"', '""') + '"'
            if any(row[2] == table for row in conn.execute("PRAGMA foreign_key_list(" + quoted + ")")):
                raise RuntimeError("Correction references require a separate migration")
        objects = conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (table,)).fetchall()
        sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
        replacement = table + "_resolved"
        conn.execute(schema.replace(table, replacement, 1).replace(old, "CHECK(status IN ('pending','accepted','resolved','rejected'))", 1))
        conn.execute(f"INSERT INTO {replacement} SELECT * FROM {table}")
        if conn.execute(f"SELECT * FROM {table} EXCEPT SELECT * FROM {replacement}").fetchone():
            raise RuntimeError("Correction row verification failed")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {replacement} RENAME TO {table}")
        for (sql,) in objects:
            conn.execute(sql)
        if sequence:
            conn.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name=?", (sequence[0], table))
