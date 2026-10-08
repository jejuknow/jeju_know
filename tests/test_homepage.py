import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import create_app
from auth_security import hash_password
from home_migrations import VERSION, backup_before_home_migration, migrate_home


class HomepageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['JEJUNO_SKIP_SEED'] = '1'
        self.database = str(Path(self.tmp.name) / 'home.db')
        self.app = create_app({'TESTING':True, 'DATABASE':self.database, 'SECRET_KEY':'home-test-only'})
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def execute(self, query, values=()):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            return conn.execute(query, values).lastrowid

    def token(self, path):
        return self.client.get(path).context['csrf_token']

    def insert_news(self, slug, created='2026-10-01 00:00:00', promoted=0, published=1, featured=0, deleted=None):
        return self.execute("INSERT INTO news_posts(slug,title,category,created_at,event_start,is_promoted,published,is_featured,deleted_at) VALUES (?,?,'지역소식',?,'2099-12-31',?,?,?,?)", (slug,slug,created,promoted,published,featured,deleted))

    def login_admin(self):
        self.execute('INSERT INTO admins(username,password_hash) VALUES (?,?)',('admin',hash_password('test-admin-password')))
        self.client.post('/admin/login',data={'csrf_token':self.token('/admin/login'),'username':'admin','password':'test-admin-password'})

    def test_promoted_first_latest_creation_order_limit_and_visibility(self):
        self.insert_news('old-featured',featured=1)
        for day in range(2,8):
            self.insert_news(f'normal-{day}',created=f'2026-10-{day:02d}T01:00:00+00:00')
        self.insert_news('promotion',created='2026-01-01 00:00:00',promoted=1)
        self.insert_news('hidden-promotion',promoted=1,published=0)
        self.insert_news('deleted-promotion',promoted=1,deleted='2026-10-08')
        page=self.client.get('/')
        self.assertEqual([item['slug'] for item in page.context['news_items']],['promotion','normal-7','normal-6','normal-5','normal-4'])
        self.assertEqual(page.text.count('class="news-card '),5)
        self.assertIn('홍보·제휴',page.text)
        self.assertNotIn('hidden-promotion',page.text)
        self.assertNotIn('deleted-promotion',page.text)
        self.assertIn('홍보·제휴',self.client.get('/news/promotion').text)
        self.assertIn('홍보·제휴',self.client.get('/news').text)
        self.execute("UPDATE news_posts SET updated_at='2099-01-01' WHERE slug='normal-2'")
        self.assertEqual(self.client.get('/').context['news_items'][1]['slug'],'normal-7')

    def test_admin_promotion_switch_immediate_and_other_flags_preserved(self):
        self.insert_news('newest',created='2099-01-01 00:00:00')
        self.login_admin()
        token=self.token('/admin/news/new')
        payload={'csrf_token':token,'title':'홍보 테스트','slug':'promoted','category':'행사•축제','published':'1','is_promoted':'1','is_featured':'1','is_sponsored':'1'}
        self.client.post('/admin/news/new',data=payload)
        promoted=self.client.get('/').context['news_items'][0]
        self.assertEqual(promoted['slug'],'promoted')
        self.assertTrue(promoted['is_featured'] and promoted['is_sponsored'] and promoted['is_promoted'])
        edit=self.client.get(f"/admin/news/{promoted['id']}/edit")
        self.assertIn('name="is_promoted" value="1" checked',edit.text)
        payload.pop('is_promoted')
        self.client.post(f"/admin/news/{promoted['id']}/edit",data=payload)
        self.assertEqual(self.client.get('/').context['news_items'][0]['slug'],'newest')
        edited=self.client.get('/news/promoted').context['item']
        self.assertFalse(edited['is_promoted'])
        self.assertTrue(edited['is_featured'] and edited['is_sponsored'])
        self.assertEqual(self.client.post('/admin/news/new',data={'is_promoted':'1'}).status_code,400)

    def test_member_cannot_set_or_clear_admin_promotion(self):
        token=self.token('/signup')
        self.client.post('/signup',data={'csrf_token':token,'username':'member','nickname':'회원','password':'Member-secret-2026','password_confirm':'Member-secret-2026'})
        self.client.post('/login',data={'csrf_token':self.token('/login'),'username':'member','password':'Member-secret-2026'})
        payload={'csrf_token':self.token('/issues/new'),'title':'회원 글','category':'지역소식','is_promoted':'1'}
        self.client.post('/issues/new',data=payload)
        post=self.client.get('/').context['news_items'][0]
        self.assertFalse(post['is_promoted'])
        self.execute('UPDATE news_posts SET is_promoted=1 WHERE id=?',(post['id'],))
        payload.pop('is_promoted')
        self.client.post(f"/issues/{post['id']}/edit",data=payload)
        self.assertTrue(self.client.get('/').context['news_items'][0]['is_promoted'])
        self.assertEqual(self.client.post(f"/admin/news/{post['id']}/edit",data=payload,follow_redirects=False).status_code,303)

    def test_home_structure_hero_links_and_optional_preferences(self):
        page=self.client.get('/')
        positions=[page.text.index(f'id="{identifier}"') for identifier in ('finder','today','places','pick','collections')]
        self.assertEqual(positions,sorted(positions))
        self.assertIn('<h1>내가 다시 가고 싶은<br><em>제주 기록</em></h1>',page.text)
        self.assertIn('<span>맛집·카페·가볼 곳부터 제주 소식까지.</span><span>직접 기록한 추천 이유와 확인한 정보를 모았습니다.</span>',page.text)
        self.assertIn('href="#pick">제주노 PICK 보기',page.text)
        self.assertIn('선택사항',page.text)
        self.assertIn('id="allResultsDialog"',page.text)
        self.assertIn('href="http://testserver/news">제주 이슈</a>',page.text)
        for path in ('/news','/partnership'):
            html=self.client.get(path).text
            self.assertIn('>제주 이슈</a>',html)
            self.assertIn('aria-controls="siteNav"',html)
        self.assertEqual(self.client.get('/map').status_code,200)


class HomepageMigrationTest(unittest.TestCase):
    def test_backup_preservation_default_zero_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.db'
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,backup_path TEXT); CREATE TABLE news_posts(id INTEGER PRIMARY KEY,body TEXT,is_featured INTEGER,is_sponsored INTEGER); INSERT INTO news_posts VALUES(1,'기존 내용',1,0); CREATE TABLE places(id INTEGER PRIMARY KEY,is_pick INTEGER); INSERT INTO places VALUES(1,1); CREATE TABLE users(id INTEGER PRIMARY KEY,nickname TEXT); INSERT INTO users VALUES(1,'보존');")
            backup=backup_before_home_migration(path)
            migrate_home(path,backup)
            with closing(sqlite3.connect(path)) as current, closing(sqlite3.connect(backup)) as before:
                for table in ('news_posts','places','users'):
                    columns=','.join(r[1] for r in before.execute('PRAGMA table_info('+table+')'))
                    self.assertEqual(current.execute('SELECT '+columns+' FROM '+table).fetchall(),before.execute('SELECT * FROM '+table).fetchall())
                self.assertEqual(current.execute('SELECT is_promoted FROM news_posts').fetchone()[0],0)
                self.assertEqual(current.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(current.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertIsNone(backup_before_home_migration(path))
            migrate_home(path,None)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations WHERE version=?',(VERSION,)).fetchone()[0],1)

    def test_backup_failure_stops_all_startup_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.db'
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('CREATE TABLE original(id INTEGER)')
            before=path.read_bytes()
            with patch('app.backup_before_home_migration',side_effect=OSError('backup unavailable')):
                with self.assertRaises(OSError):
                    create_app({'TESTING':True,'DATABASE':str(path),'SECRET_KEY':'home-tests'})
            self.assertEqual(path.read_bytes(),before)
