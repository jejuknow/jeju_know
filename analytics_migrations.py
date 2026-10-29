"""Add first-party event storage after a verified SQLite backup; never seed events."""
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = '20261029_first_party_analytics'


def migrate(database):
    path = Path(database).resolve()
    with closing(sqlite3.connect(path)) as conn:
        if conn.execute('SELECT 1 FROM schema_migrations WHERE version=?', (VERSION,)).fetchone():
            return
    folder = path.parent / 'backups'
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(timezone(timedelta(hours=9))).strftime('%Y%m%d-%H%M%S-%f')
    backup = folder / f'jejuno-before-analytics-{stamp}.db'
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Analytics backup verification failed')
    with closing(sqlite3.connect(path, timeout=30)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT 1 FROM schema_migrations WHERE version=?', (VERSION,)).fetchone():
            return
        conn.execute('''CREATE TABLE analytics_events (
            id INTEGER PRIMARY KEY, event_key TEXT NOT NULL, event_type TEXT NOT NULL,
            visitor_id TEXT NOT NULL, session_id TEXT NOT NULL, path TEXT NOT NULL,
            feature TEXT NOT NULL, device_type TEXT NOT NULL CHECK(device_type IN ('mobile','desktop')),
            filters TEXT NOT NULL DEFAULT '{}', is_test INTEGER NOT NULL DEFAULT 0 CHECK(is_test IN (0,1)),
            created_at INTEGER NOT NULL, UNIQUE(event_key,event_type))''')
        conn.execute('CREATE INDEX analytics_period ON analytics_events(is_test,created_at,event_type)')
        conn.execute('CREATE INDEX analytics_visitors ON analytics_events(visitor_id,created_at)')
        conn.execute('CREATE INDEX analytics_retention ON analytics_events(created_at)')
        conn.execute('''CREATE TABLE analytics_sessions (
            visitor_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, last_seen INTEGER NOT NULL,
            minute INTEGER NOT NULL, attempts INTEGER NOT NULL)''')
        conn.execute('CREATE INDEX analytics_session_expiry ON analytics_sessions(last_seen)')
        conn.execute('CREATE TABLE analytics_meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        conn.executemany('INSERT INTO analytics_meta VALUES (?,?)', [('started_at',int(time.time())),('last_cleanup',0)])
        conn.execute('INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)', (VERSION,str(backup)))
    print(f'[JEJUNO] Applied {VERSION}; backup verified: {backup}; existing content preserved', flush=True)
