"""Fixture for a fifth image written by the previous application version."""
import sqlite3
import uuid
from contextlib import closing


def add_fifth(store, database, place_id):
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.row_factory = sqlite3.Row
        original = conn.execute("SELECT * FROM images WHERE place_id=? AND sort_order=0", (place_id,)).fetchone()
        path = "places/" + uuid.uuid4().hex + ".webp"
        store.path(path).write_bytes(store.path(original['file_path']).read_bytes())
        conn.execute("INSERT INTO images(place_id,file_path,width,height,byte_size,sort_order) VALUES (?,?,?,?,?,4)",
                     (place_id, path, original['width'], original['height'], original['byte_size']))
