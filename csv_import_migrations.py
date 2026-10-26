"""Add import receipts and external photo references without changing place rows."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

VERSION = '20261026_csv_place_import'


def backup(database, kind):
    path = Path(database).resolve()
    folder = path.parent / 'backups'
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    target_path = folder / f'jejuno-before-{kind}-{stamp}.db'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(target_path)) as target:
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('CSV import backup verification failed')
    return str(target_path)


def migrate(database):
    with closing(sqlite3.connect(database)) as conn:
        if conn.execute('SELECT 1 FROM schema_migrations WHERE version=?', (VERSION,)).fetchone():
            return
    path = backup(database, 'csv-import-migration')
    with closing(sqlite3.connect(database, timeout=30)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT 1 FROM schema_migrations WHERE version=?', (VERSION,)).fetchone():
            return
        conn.execute('''CREATE TABLE csv_import_runs (
            token TEXT PRIMARY KEY, actor TEXT NOT NULL, kind TEXT NOT NULL,
            result TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE place_image_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            place_id INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
            sort_order INTEGER NOT NULL CHECK(sort_order BETWEEN 0 AND 3),
            url TEXT NOT NULL, UNIQUE(place_id,sort_order))''')
        conn.execute("ALTER TABLE places ADD COLUMN admin_import_note TEXT NOT NULL DEFAULT ''")
        conn.execute('INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)', (VERSION,path))
    print(f'[JEJUNO] Applied {VERSION}; verified backup: {path}', flush=True)
