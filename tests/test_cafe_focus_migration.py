import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from app import create_app
from cafe_focus_migrations import backup_before_cafe_focus, migrate_cafe_focus


class CafeFocusMigrationTest(unittest.TestCase):
    def test_backup_additive_preservation_no_inference_and_idempotence(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.db'
            with closing(sqlite3.connect(path)) as conn,conn:
                conn.executescript("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,backup_path TEXT); CREATE TABLE places(id INTEGER PRIMARY KEY,name TEXT,features TEXT); INSERT INTO places VALUES(1,'기존 카페','[\"맛 중심\",\"이색 경험\"]'),(2,'두번째 카페','[]'); CREATE TABLE categories(id INTEGER,name TEXT); INSERT INTO categories VALUES(3,'맛 중심'); CREATE TABLE news_posts(id INTEGER,body TEXT); INSERT INTO news_posts VALUES(1,'원문');")
            backup=backup_before_cafe_focus(path)
            migrate_cafe_focus(path,backup)
            with closing(sqlite3.connect(path)) as current, closing(sqlite3.connect(backup)) as before:
                self.assertEqual(current.execute('SELECT id,name,features FROM places').fetchall(),before.execute('SELECT * FROM places').fetchall())
                for table in ('categories','news_posts'):
                    self.assertEqual(current.execute('SELECT * FROM '+table).fetchall(),before.execute('SELECT * FROM '+table).fetchall())
                self.assertEqual(current.execute('SELECT cafe_focus FROM places').fetchall(),[('[]',),('[]',)])
                self.assertEqual(current.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertIsNone(backup_before_cafe_focus(path))
            migrate_cafe_focus(path,None)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(len(conn.execute('PRAGMA table_info(places)').fetchall()),4)

    def test_backup_failure_blocks_startup_before_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.db'
            with closing(sqlite3.connect(path)) as conn:conn.execute('CREATE TABLE original(id INTEGER)')
            before=path.read_bytes()
            with patch('app.backup_before_cafe_focus',side_effect=OSError('full disk')):
                with self.assertRaises(OSError):create_app({'TESTING':True,'DATABASE':str(path),'SECRET_KEY':'test'})
            self.assertEqual(path.read_bytes(),before)
