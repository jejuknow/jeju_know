import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import create_app
from fastapi.testclient import TestClient
from taxonomy_migrations import VERSION, SEED_PATH, backup_before_taxonomy_migration, migrate_taxonomy
import test_map_categories as legacy


class TaxonomyTest(unittest.TestCase):
    setUp = legacy.MapCategoryTest.setUp
    tearDown = legacy.MapCategoryTest.tearDown
    get_csrf_from_cookie_session = legacy.MapCategoryTest.get_csrf_from_cookie_session
    setup_admin_and_login = legacy.MapCategoryTest.setup_admin_and_login
    post = legacy.MapCategoryTest.post
    category = legacy.MapCategoryTest.category
    place = legacy.MapCategoryTest.place

    def config(self):
        return self.client.get('/api/place-taxonomy').json()

    def term(self, slug):
        return next(t for t in self.config()['terms'] if t['slug'] == slug)

    def test_existing_ten_names_core_order_and_shared_pages(self):
        seed = json.loads(SEED_PATH.read_text(encoding='utf-8'))
        self.assertEqual([t['name'] for t in self.config()['terms'] if t['type']=='cafe_feature'], [t['label'] for t in seed['cafe_feature']])
        self.assertEqual(len([t for t in self.config()['terms'] if t['type']=='restaurant_subcategory']),13)
        self.setup_admin_and_login()
        self.post('/admin/categories/new',name='직접 분류',slug='custom',sort_order='0',is_active='1')
        self.assertEqual([t['slug'] for t in self.config()['categories']][:3],['food','cafe','attraction'])
        for url in ('/','/map','/places','/admin/categories','/admin/places/new'):
            self.assertEqual(self.client.get(url).status_code,200)
        home=self.client.get('/').text
        self.assertIn('id="categoryDetails" hidden',home)
        self.assertNotIn('어떤 곳이 좋아요?',home)

    def test_search_optional_details_multi_or_and_base_constraints(self):
        self.setup_admin_and_login()
        korean=self.term('food-korean'); western=self.term('food-western')
        self.place(slug='korean',category_id=self.category('food')['id'],taxonomy_present='1',term_ids=[str(korean['id'])],companions=['부모님','아이'])
        self.place(slug='western',category_id=self.category('food')['id'],taxonomy_present='1',term_ids=[str(western['id'])],companions=['친구'],region='성산')
        self.place(slug='plain',category_id=self.category('food')['id'],taxonomy_present='1',latitude='',longitude='')
        self.post('/admin/places/new',name='숨긴 장소',slug='hidden',region='애월',category_id=self.category('food')['id'],taxonomy_present='1',term_ids=[str(korean['id'])])
        def results(**params):return [p['slug'] for p in self.client.get('/api/places',params=params).json()]
        self.assertEqual(len(results(category='food')),3)
        self.assertEqual(results(category='food',detail='food-korean'),['korean'])
        self.assertEqual(set(results(category='food',detail='food-korean,food-western')),{'korean','western'})
        self.assertEqual(results(category='food',detail='food-korean,food-western',companion='부모님,아이',region='애월'),['korean'])
        for detail in ('food-korean,bogus','cafe-mood',"' OR 1=1 --"):
            self.assertEqual(results(category='food',detail=detail),[])
        self.assertEqual(results(category='food',detail='food-korean',region='성산'),[])
        self.assertEqual(self.client.get('/place/hidden').status_code,404)

    def test_term_crud_used_delete_inactive_existing_save_and_new_rejection(self):
        self.setup_admin_and_login()
        self.post('/admin/categories/new',type='restaurant_subcategory',name='수제 요리',slug='handmade',sort_order='0',is_active='1')
        term=self.term('handmade')
        self.assertIn('수제 요리',self.client.get('/admin/places/new').text)
        place=self.place(category_id=self.category('food')['id'],taxonomy_present='1',term_ids=[str(term['id'])])
        self.assertEqual(len(self.client.get('/api/places?category=food&detail=handmade').json()),1)
        self.post(f"/admin/categories/{term['id']}/edit",name='수제 한상',slug='ignored-change',sort_order='1',is_active='1')
        self.assertEqual(self.term('handmade')['name'],'수제 한상')
        self.assertIn('현재 1개 장소',self.post(f"/admin/categories/{term['id']}/delete").text)
        self.post(f"/admin/categories/{term['id']}/edit",name='수제 한상',slug='handmade',sort_order='1')
        self.assertFalse(any(t['slug']=='handmade' for t in self.config()['terms']))
        self.assertEqual(self.client.get('/api/places?category=food&detail=handmade').json(),[])
        self.assertNotIn('수제 한상',self.client.get('/admin/places/new').text)
        self.assertIn('수제 한상 (비활성 · 기존 분류)',self.client.get(f"/admin/places/{place['id']}/edit").text)
        payload=dict(name='그대로',slug=place['slug'],region='애월',category_id=self.category('food')['id'],taxonomy_present='1',term_ids=[str(term['id'])],published='1')
        self.assertEqual(self.post(f"/admin/places/{place['id']}/edit",**payload).status_code,200)
        self.assertEqual(self.post('/admin/places/new',**{**payload,'slug':'rejected'}).status_code,400)
        self.post(f"/admin/places/{place['id']}/edit",**{**payload,'term_ids':[]})
        self.post(f"/admin/categories/{term['id']}/delete")
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertIsNone(conn.execute('SELECT id FROM categories WHERE id=?',(term['id'],)).fetchone())
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_cafe_legacy_mapping_toggle_clear_and_category_switch(self):
        self.setup_admin_and_login()
        place=self.place(features=['감성 추천','오션뷰','원래 태그'],companions=['혼자'])
        self.assertEqual(len(self.client.get('/api/places?category=cafe&detail=cafe-mood').json()),1)
        self.assertEqual(len(self.client.get('/api/places?category=cafe&detail=cafe-view').json()),1)
        payload=dict(name=place['name'],slug=place['slug'],region='애월',category_id=self.category('cafe')['id'],published='1',taxonomy_present='1',features=['감성 추천','오션뷰','원래 태그'])
        self.post(f"/admin/places/{place['id']}/edit",**payload)
        self.assertEqual(self.client.get('/api/places?category=cafe&detail=cafe-mood').json(),[])
        self.assertEqual(self.client.get('/api/places').json()[0]['features'],payload['features'])
        invalid={**payload,'category_id':self.category('food')['id'],'term_ids':[str(self.term('cafe-mood')['id'])]}
        self.assertEqual(self.post(f"/admin/places/{place['id']}/edit",**invalid).status_code,400)
        self.post(f"/admin/places/{place['id']}/edit",**{**payload,'category_id':self.category('attraction')['id']})
        self.assertEqual(len(self.client.get('/api/places?category=attraction').json()),1)
        restarted=TestClient(create_app({'TESTING':True,'DATABASE':self.db_path,'SECRET_KEY':'test'}))
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.get('/api/places').json(),self.client.get('/api/places').json())
        self.assertEqual(restarted.get('/place/'+place['slug']).status_code,200)

    def test_auth_csrf_and_top_category_cannot_be_subtype(self):
        self.assertEqual(self.client.post('/admin/categories/new',data={'type':'cafe_feature','name':'금지'},follow_redirects=False).status_code,303)
        self.setup_admin_and_login()
        self.assertEqual(self.client.post('/admin/categories/new',data={'type':'cafe_feature','name':'금지'}).status_code,400)
        response=self.post('/admin/places/new',name='잘못된 분류',region='애월',category_id=self.term('food-korean')['id'])
        self.assertEqual(response.status_code,400)


class TaxonomyMigrationTest(unittest.TestCase):
    def test_backup_preserves_all_rows_maps_tags_and_restart_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.db'
            with closing(sqlite3.connect(path)) as conn,conn:
                conn.executescript("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,backup_path TEXT); CREATE TABLE categories(id INTEGER PRIMARY KEY,name TEXT UNIQUE,slug TEXT UNIQUE,sort_order INTEGER,is_active INTEGER); INSERT INTO categories VALUES(1,'맛집','food',1,1),(2,'카페','cafe',2,1); CREATE TABLE places(id INTEGER PRIMARY KEY,category_id INTEGER,features TEXT,published INTEGER,is_pick INTEGER); CREATE TABLE news_posts(id INTEGER,body TEXT); INSERT INTO news_posts VALUES(1,'보존');")
                conn.executemany('INSERT INTO places VALUES(?,?,?,?,?)',[(1,2,'["감성", "사진"]',1,1),(2,1,'["한식", "원래 태그"]',0,0)])
            backup=backup_before_taxonomy_migration(path)
            migrate_taxonomy(path,backup)
            with closing(sqlite3.connect(path)) as current,closing(sqlite3.connect(backup)) as before:
                for table in ('places','news_posts'):
                    self.assertEqual(current.execute('SELECT * FROM '+table).fetchall(),before.execute('SELECT * FROM '+table).fetchall())
                self.assertEqual(current.execute('SELECT id,name,slug,sort_order,is_active FROM categories WHERE type="place"').fetchall(),before.execute('SELECT * FROM categories').fetchall())
                self.assertEqual(current.execute('SELECT count(*) FROM place_category_links').fetchone()[0],2)
                self.assertEqual(current.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(current.execute('PRAGMA foreign_key_check').fetchall(),[])
                snapshot=current.execute('SELECT * FROM categories').fetchall()
            self.assertIsNone(backup_before_taxonomy_migration(path))
            migrate_taxonomy(path,None)
            with closing(sqlite3.connect(path)) as current:
                self.assertEqual(snapshot,current.execute('SELECT * FROM categories').fetchall())

    def test_backup_failure_blocks_startup_before_schema_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.db'
            with closing(sqlite3.connect(path)) as conn:conn.execute('CREATE TABLE original(id INTEGER)')
            original=path.read_bytes()
            with patch('app.backup_before_taxonomy_migration',side_effect=OSError('full disk')):
                with self.assertRaises(OSError):create_app({'TESTING':True,'DATABASE':str(path),'SECRET_KEY':'test'})
            self.assertEqual(path.read_bytes(),original)
