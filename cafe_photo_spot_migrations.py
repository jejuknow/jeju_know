"""Rename the existing cafe feature without changing its identity or relationships."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = '20261029_cafe_photo_spot'
OLD_NAME = '고급 분위기'
NEW_NAME = '포토스팟'


def migrate(database):
    path = Path(database).resolve()
    with closing(sqlite3.connect(path)) as conn:
        if conn.execute('SELECT 1 FROM schema_migrations WHERE version=?', (VERSION,)).fetchone():
            return
    folder = path.parent / 'backups'
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(timezone(timedelta(hours=9))).strftime('%Y%m%d-%H%M%S-%f')
    backup = folder / f'jejuno-before-photo-spot-feature-{stamp}.db'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Photo spot backup verification failed')
    with closing(sqlite3.connect(path, timeout=30)) as conn, conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT 1 FROM schema_migrations WHERE version=?', (VERSION,)).fetchone():
            return
        rows = conn.execute("""SELECT id,name,legacy_aliases FROM categories
            WHERE type='cafe_feature' AND is_legacy=0
            AND (name IN (?,?) OR slug='cafe-premium')""", (OLD_NAME, NEW_NAME)).fetchall()
        if len(rows) > 1:
            raise RuntimeError('Ambiguous cafe photo spot feature; rename stopped without merging rows')
        for feature_id, _, aliases in rows:
            aliases = list(dict.fromkeys([*json.loads(aliases), OLD_NAME]))
            # Intentionally leave slug, ordering, activity, policy and link rows untouched.
            conn.execute('UPDATE categories SET name=?,legacy_aliases=? WHERE id=?',
                         (NEW_NAME, json.dumps(aliases, ensure_ascii=False), feature_id))
        if conn.execute('PRAGMA foreign_key_check').fetchall() or conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Photo spot migration integrity check failed')
        conn.execute('INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)', (VERSION, str(backup)))
    print(f'[JEJUNO] Applied {VERSION}; backup verified: {backup}; cafe feature identity preserved', flush=True)
