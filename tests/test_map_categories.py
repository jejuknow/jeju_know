import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import create_app
from place_migrations import backup_before_migration, migrate_place_categories
import test_app


class MapCategoryTest(unittest.TestCase):
    setUp = test_app.JejunoAppTest.setUp
    tearDown = test_app.JejunoAppTest.tearDown
    get_csrf_from_cookie_session = test_app.JejunoAppTest.get_csrf_from_cookie_session
    setup_admin_and_login = test_app.JejunoAppTest.setup_admin_and_login

    def post(self, url, **data):
        data['csrf_token'] = self.get_csrf_from_cookie_session('/admin')
        return self.client.post(url, data=data, follow_redirects=True)

    def category(self, slug):
        return next(c for c in self.client.get('/api/categories').json() if c['slug'] == slug)

    def place(self, **overrides):
        data = dict(name='애월 테스트 카페', slug='map-cafe', region='애월', category_id=self.category('cafe')['id'],
                    one_line='직접 확인한 커피와 바다', published='1', latitude='33.4', longitude='126.3', is_pick='1')
        data.update(overrides)
        response = self.post('/admin/places/new', **data)
        self.assertEqual(response.status_code, 200, response.text[:300])
        return next(p for p in self.client.get('/api/places').json() if p['slug'] == data['slug'])

    def test_public_map_search_and_private_place_protection(self):
        self.setup_admin_and_login()
        cafe = self.place()
        food = self.place(name='식당', slug='food-map', category_id=self.category('food')['id'], one_line='든든한 밥', latitude='', longitude='')
        self.post('/admin/places/new', name='절대 노출하면 안 되는 비공개', slug='secret-place', region='애월', category_id=self.category('cafe')['id'], latitude='33.5', longitude='126.4')
        public = TestClient(self.app)
        self.addCleanup(public.close)
        self.assertEqual(public.get('/map').status_code, 200)
        self.assertEqual(len(public.get('/api/places').json()), 2)
        for url in ['/map', '/api/places', '/api/places?category=cafe', '/api/places?q=비공개']:
            self.assertNotIn('절대 노출하면 안 되는 비공개', public.get(url).text)
        self.assertEqual(public.get('/place/secret-place').status_code, 404)
        self.assertEqual([p['id'] for p in public.get('/api/places', params={'category':'cafe', 'q':'애월'}).json()], [cafe['id']])
        self.assertEqual(len(public.get('/api/places', params={'q':'커피'}).json()), 1)
        self.assertEqual(public.get('/api/places', params={'category':'food', 'q':'커피'}).json(), [])
        self.assertEqual(public.get('/api/places', params={'category':"' OR 1=1 --"}).json(), [])
        self.assertEqual(public.get('/api/places', params={'q':'%'}).json(), [])
        self.assertFalse(food['has_coordinates'])
        self.assertTrue(cafe['has_coordinates'])
        self.assertTrue(cafe['is_pick'])
        self.assertIn('지도 위치 미등록', self.client.get('/admin').text)
        self.assertEqual(public.get('/place/map-cafe').status_code, 200)

    def test_category_create_rename_order_disable_delete_and_restart(self):
        self.setup_admin_and_login()
        self.post('/admin/categories/new', name='베이커리', slug='bakery', sort_order='0', is_active='1')
        category = self.category('bakery')
        self.assertEqual(self.client.get('/api/categories').json()[0]['name'], '베이커리')
        self.assertIn('베이커리', self.client.get('/admin/places/new').text)
        self.assertIn('data-category="bakery"', self.client.get('/map').text)
        self.assertEqual(self.client.get('/api/places?category=bakery').json(), [])
        place = self.place(category_id=category['id'])
        self.post(f"/admin/categories/{category['id']}/edit", name='제주 빵집', slug='bakery', sort_order='4', is_active='1')
        self.assertEqual(self.category('bakery')['name'], '제주 빵집')
        self.assertEqual(self.client.get('/api/places?category=bakery').json()[0]['category'], '제주 빵집')
        self.assertIn('제주 빵집', self.client.get('/place/map-cafe').text)
        blocked = self.post(f"/admin/categories/{category['id']}/delete")
        self.assertIn('현재 1개 장소에서 사용 중인 카테고리입니다.', blocked.text)
        self.post(f"/admin/categories/{category['id']}/edit", name='제주 빵집', slug='bakery', sort_order='4')
        self.assertNotIn('bakery', self.client.get('/api/categories').text)
        self.assertNotIn('제주 빵집', self.client.get('/admin/places/new').text)
        self.assertNotIn('data-category="bakery"', self.client.get('/map').text)
        self.assertEqual(self.client.get('/api/places?category=bakery').json(), [])
        self.assertEqual(len(self.client.get('/api/places').json()), 1)
        self.assertIn('비활성 · 기존 분류', self.client.get(f"/admin/places/{place['id']}/edit").text)
        invalid = self.post('/admin/places/new', name='신규', region='애월', category_id=category['id'])
        self.assertEqual(invalid.status_code, 400)
        saved = self.post(f"/admin/places/{place['id']}/edit", name='유지된 장소', slug=place['slug'], region='애월', category_id=category['id'], published='1', is_pick='1', latitude='33.42', longitude='126.31')
        self.assertEqual(saved.status_code, 200)
        restarted = TestClient(create_app({'DATABASE':self.db_path, 'SECRET_KEY':'test-secret'}))
        self.addCleanup(restarted.close)
        result = restarted.get('/api/places').json()[0]
        self.assertEqual((result['category_id'],result['category'],result['latitude'],result['longitude']), (category['id'],'제주 빵집',33.42,126.31))
        self.assertTrue(result['is_pick'])
        self.post(f"/admin/places/{place['id']}/toggle-published")
        self.assertIn('현재 1개 장소', self.post(f"/admin/categories/{category['id']}/delete").text)
        self.post(f"/admin/places/{place['id']}/delete")
        self.post(f"/admin/categories/{category['id']}/delete")
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM categories WHERE id=?',(category['id'],)).fetchone()[0],0)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_coordinate_validation_and_clear(self):
        self.setup_admin_and_login()
        category_id = self.category('cafe')['id']
        for lat,lng in [('33.4',''), ('','126.4'), ('nan','126'), ('inf','126'), ('91','126'), ('33','181'), ('abc','126')]:
            response = self.post('/admin/places/new', name='좌표 오류', region='애월', category_id=category_id, latitude=lat, longitude=lng)
            self.assertEqual(response.status_code,400)
        place = self.place(latitude='0',longitude='0')
        # Legacy form posts without coordinate fields must preserve stored coordinates.
        self.post(f"/admin/places/{place['id']}/edit", name=place['name'], slug=place['slug'], region='애월', category='카페', published='1')
        self.assertEqual(self.client.get('/api/places').json()[0]['latitude'],0)
        self.post(f"/admin/places/{place['id']}/edit", name=place['name'], slug=place['slug'], region='애월', category_id=category_id, published='1', latitude='', longitude='')
        result=self.client.get('/api/places').json()[0]
        self.assertIsNone(result['latitude'])
        self.assertIsNone(result['longitude'])
        self.assertFalse(result['has_coordinates'])

    def test_category_validation_auth_and_foreign_key(self):
        self.assertEqual(self.client.get('/admin/categories',follow_redirects=False).status_code,303)
        self.assertEqual(self.client.post('/admin/categories/new',data={'name':'금지'},follow_redirects=False).status_code,303)
        self.setup_admin_and_login()
        self.assertEqual(self.client.post('/admin/categories/new',data={'name':'금지'}).status_code,400)
        original=self.client.get('/api/categories').json()
        for name,slug,order in [('전체','all','0'), ('새 분류','bad slug','0'), ('새 분류','new','-1'), ('카페','duplicate','0'), ('다른 이름','cafe','0')]:
            self.post('/admin/categories/new',name=name,slug=slug,sort_order=order,is_active='1')
            self.assertEqual(self.client.get('/api/categories').json(),original)
        place=self.place()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('DELETE FROM categories WHERE id=?',(place['category_id'],))


class MigrationTest(unittest.TestCase):
    def test_legacy_backup_preservation_and_idempotence(self):
        with tempfile.TemporaryDirectory() as folder:
            database=Path(folder)/'jejuno.db'
            with closing(sqlite3.connect(database)) as conn:
                conn.executescript('CREATE TABLE places(id INTEGER PRIMARY KEY, slug TEXT, category TEXT, published INTEGER, is_pick INTEGER, note TEXT); CREATE TABLE admins(id INTEGER, password_hash TEXT); CREATE TABLE news_posts(id INTEGER, body TEXT);')
                conn.executemany('INSERT INTO places VALUES(?,?,?,?,?,?)',[(1,'old-cafe','카페',1,1,'기존 본문'),(2,'private','직접 만든 분류',0,0,'보존')])
                conn.execute("INSERT INTO admins VALUES (3,'hashed-secret')")
                conn.execute("INSERT INTO news_posts VALUES (4,'이슈 보존')")
                conn.commit()
                before=conn.execute('SELECT * FROM places').fetchall()
            backup=backup_before_migration(database)
            self.assertTrue(Path(backup).exists())
            with closing(sqlite3.connect(backup)) as conn:
                self.assertEqual(conn.execute('SELECT * FROM places').fetchall(),before)
                self.assertNotIn('category_id',[r[1] for r in conn.execute('PRAGMA table_info(places)')])
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            migrate_place_categories(database,backup)
            with closing(sqlite3.connect(database)) as conn:
                self.assertEqual(conn.execute('SELECT id,slug,category,published,is_pick,note FROM places').fetchall(),before)
                self.assertEqual(set(r[0] for r in conn.execute('SELECT name FROM categories')),{'카페','직접 만든 분류'})
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM places p JOIN categories c ON p.category_id=c.id AND p.category=c.name').fetchone()[0],2)
                self.assertEqual(conn.execute('SELECT latitude,longitude FROM places').fetchall(),[(None,None),(None,None)])
                self.assertEqual(conn.execute('SELECT * FROM admins').fetchall(),[(3,'hashed-secret')])
                self.assertEqual(conn.execute('SELECT * FROM news_posts').fetchall(),[(4,'이슈 보존')])
                after=conn.execute('SELECT * FROM places').fetchall()
            self.assertIsNone(backup_before_migration(database))
            migrate_place_categories(database)
            with closing(sqlite3.connect(database)) as conn:
                self.assertEqual(conn.execute('SELECT * FROM places').fetchall(),after)
                self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0],1)

    def test_backup_failure_prevents_any_schema_change(self):
        with tempfile.TemporaryDirectory() as folder:
            database=Path(folder)/'jejuno.db'
            with closing(sqlite3.connect(database)) as conn:
                conn.execute('CREATE TABLE places(id INTEGER,category TEXT)')
                conn.execute("INSERT INTO places VALUES(1,'카페')")
                conn.commit()
            before=database.read_bytes()
            with patch('app.backup_before_migration',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    create_app({'DATABASE':str(database),'SECRET_KEY':'test'})
            self.assertEqual(database.read_bytes(),before)
