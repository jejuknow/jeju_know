import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlite_backups import AUTO, BackupBusy, BackupManager, KST


class BackupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "live.db"
        self.manager = BackupManager(self.db)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            for table in ("places", "users", "news_posts"):
                conn.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, name TEXT)")
                conn.execute(f"INSERT INTO {table} VALUES (1,'preserved')")

    def test_live_wal_backup_and_isolated_restore(self):
        with closing(sqlite3.connect(self.db)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("INSERT INTO places VALUES(2,'committed WAL')")
            writer.commit()
            writer.execute("INSERT INTO places VALUES(3,'uncommitted')")
            result = self.manager.create()
            self.assertGreater(result["size"], 0)
            self.assertEqual(result["counts"], dict(places=2, users=1, news_posts=1))
            self.assertEqual(result["journal_mode"], "delete")
            self.assertEqual(self.manager.verify_restore()["counts"], result["counts"])
            self.assertEqual(writer.execute("SELECT count(*) FROM places").fetchone()[0], 3)
            writer.rollback()
        self.assertEqual(self.manager.inspect(self.db)["counts"]["places"], 2)

    def test_daily_due_lock_and_retention_only_automatic(self):
        now = datetime.now(KST).replace(hour=3, minute=0, second=0, microsecond=0)
        manual = self.manager.create()
        migration = self.manager.root / "jejuno-before-migration-20260101-000000.db"
        migration.write_bytes(b"must not purge")
        for index in range(17):
            day = now - timedelta(days=20-index)
            result = self.manager.create("auto", now=day)
            os.utime(self.manager.path(result["name"]), (day.timestamp(), day.timestamp()))
        result = self.manager.create("auto", now=now)
        os.utime(self.manager.path(result["name"]), (now.timestamp(), now.timestamp()))
        self.assertIsNone(self.manager.create("auto", now=now + timedelta(hours=1)))
        self.assertEqual(len([f for f in self.manager.files() if AUTO.fullmatch(f["name"])]), 14)
        self.assertTrue(migration.exists())
        self.assertTrue(self.manager.path(manual["name"]).exists())
        with self.manager.lock(), self.assertRaises(BackupBusy):
            self.manager.create()

    def test_failure_staleness_and_filename_validation(self):
        self.assertTrue(self.manager.status()["warning"])
        with patch.object(self.manager, "copy", side_effect=OSError("PRIVATE-EXCEPTION")):
            with self.assertRaises(OSError):
                self.manager.create("auto")
        self.assertNotIn("PRIVATE-EXCEPTION", str(self.manager.metadata()))
        result = self.manager.create("auto")
        self.assertFalse(self.manager.status()["warning"])
        old = time.time() - 37 * 3600
        os.utime(self.manager.path(result["name"]), (old, old))
        self.assertTrue(self.manager.status()["warning"])
        for name in ("../live.db", "/data/jejuno.db", "secret_key.txt", "jejuno-missing.db"):
            with self.assertRaises(ValueError):
                self.manager.path(name)
        self.manager.path(result["name"]).write_bytes(b"not a database")
        with self.assertRaises(sqlite3.DatabaseError):
            self.manager.verify_restore()
        self.assertTrue(self.manager.status()["restore_error"])
