"""Back up before any startup writes; add member tables without rewriting content."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

VERSION = "20261008_local_users_issues"


def applied(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
                and conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone())


def backup_before_user_migration(database):
    path = Path(database).resolve()
    if not path.exists() or not path.stat().st_size:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        if applied(source):
            return None
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        target_path = folder / f"jejuno-before-user-auth-{stamp}.db"
        with closing(sqlite3.connect(target_path)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("User migration backup failed integrity check")
        print(f"[JEJUNO] Verified user migration backup: {target_path}", flush=True)
        return str(target_path)


def migrate_users(database, backup_path):
    with closing(sqlite3.connect(database, timeout=30)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        if applied(conn):
            return
        conn.execute("""CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            nickname TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin')),
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','blocked','deleted')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_login_at TEXT)""")
        conn.execute("""CREATE TABLE auth_sessions (
            token_hash TEXT PRIMARY KEY, data TEXT NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            admin_id INTEGER REFERENCES admins(id) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL)""")
        conn.execute("CREATE INDEX auth_sessions_user_idx ON auth_sessions(user_id)")
        conn.execute("CREATE INDEX auth_sessions_expiry_idx ON auth_sessions(expires_at)")
        conn.execute("""CREATE TABLE auth_rate_limits (
            key_hash TEXT PRIMARY KEY, attempts INTEGER NOT NULL,
            expires_at INTEGER NOT NULL)""")
        conn.execute("ALTER TABLE news_posts ADD COLUMN author_id INTEGER REFERENCES users(id) ON DELETE RESTRICT")
        conn.execute("ALTER TABLE news_posts ADD COLUMN author_type TEXT NOT NULL DEFAULT 'admin' CHECK(author_type IN ('admin','user'))")
        conn.execute("ALTER TABLE news_posts ADD COLUMN deleted_at TEXT")
        conn.execute("CREATE INDEX news_posts_author_idx ON news_posts(author_id,author_type)")
        # published remains the single source of truth for public visibility.
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("User migration failed foreign key validation")
        conn.execute("INSERT INTO schema_migrations(version,backup_path) VALUES (?,?)", (VERSION, backup_path))
    print(f"[JEJUNO] Applied migration: {VERSION}", flush=True)
