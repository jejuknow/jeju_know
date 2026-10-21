import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import create_app
from auth_security import hash_password
from restaurant_migrations import TREE, VERSION, backup_before_restaurants, migrate_restaurants
import test_community as helpers


class RestaurantTest(unittest.TestCase):
    setUp = helpers.CommunityTest.setUp
    tearDown = helpers.CommunityTest.tearDown
    rows = helpers.CommunityTest.rows
    execute = helpers.CommunityTest.execute
    csrf = helpers.CommunityTest.csrf
    post = helpers.CommunityTest.post
    form = helpers.CommunityTest.form
    api = helpers.CommunityTest.api

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('community-test-secret')

    def term(self, slug):
        return self.rows('SELECT * FROM categories WHERE slug=?', (slug,))[0]

    def food(self, main='food-korean', child='', **extra):
        return self.form(category_id=str(self.category['food']), restaurant_taxonomy_present='1',
                         restaurant_main_id=str(self.term(main)['id']) if main else '',
                         restaurant_subtype_id=str(self.term(child)['id']) if child else '', **extra)

    def create_food(self, **extra):
        response = self.post(self.member, '/map/register', **self.food(**extra))
        self.assertEqual(response.status_code,303,response.text)
        return self.rows('SELECT * FROM places ORDER BY id DESC')[0]

    def test_tree_and_cafe_unchanged(self):
        data = self.visitor.get('/api/place-taxonomy').json()
        majors = [r for r in data['terms'] if r['parent_id']==self.category['food'] and not r['is_legacy']]
        self.assertEqual([r['name'] for r in majors], [r[1] for r in TREE])
        for major, (_,_,children) in zip(majors,TREE):
            self.assertEqual([r['name'] for r in data['terms'] if r['parent_id']==major['id']], [r[1] for r in children])
        self.assertEqual(len([r for r in data['terms'] if r['type']=='cafe_feature']),15)
        self.assertEqual(sum(len(r[2]) for r in TREE),53)

    def test_member_major_required_child_optional_and_validation(self):
        self.assertEqual(self.post(self.member,'/map/register',**self.food(main='')).status_code,400)
        forged = self.food(); forged.pop('restaurant_taxonomy_present')
        self.assertEqual(self.post(self.member,'/map/register',**forged).status_code,400)
        self.assertEqual(self.post(self.member,'/map/register',**self.food(child='food-ramen')).status_code,400)
        self.create_food()
        self.assertEqual(len(self.api('?category=food&cuisine=food-korean')),1)
        self.assertEqual(self.api('?category=food&cuisine=food-japanese'),[])

    def test_subtype_search_menu_path_and_member_edit(self):
        p=self.create_food(child='food-noodles', signature_menu='고기국수\n보말칼국수')
        for query in ('?category=food','?category=food&cuisine=food-korean','?category=food&cuisine=food-korean&detail=food-noodles','?category=food&detail=food-korean','?category=food&detail=food-noodles','?q=고기국수'):
            self.assertEqual([r['id'] for r in self.api(query)],[p['id']],query)
        self.assertEqual(self.api('?category=food&cuisine=food-japanese&detail=food-noodles'),[])
        self.assertEqual(self.api('?category=cafe&cuisine=food-korean'),[])
        self.assertEqual(self.api('?category=food&cuisine=missing'),[])
        self.assertEqual(self.api()[0]['restaurant_label'],'한식 · 국수·면')
        self.assertIn('맛집 · 한식 · 국수·면',self.visitor.get('/place/'+p['slug']).text)
        self.assertIn('고기국수',self.visitor.get('/place/'+p['slug']).text)
        self.assertEqual(self.rows("SELECT * FROM categories WHERE name='고기국수'"),[])
        page=self.member.get(f"/places/{p['id']}/edit").text
        self.assertIn(f'value="{self.term("food-noodles")["id"]}" selected',page)
        self.assertEqual(self.post(self.other,f"/places/{p['id']}/edit",**self.food()).status_code,403)
        self.assertEqual(self.post(self.member,f"/places/{p['id']}/edit",**self.food(main='food-japanese',child='food-pork-cutlet')).status_code,303)
        self.assertEqual(self.api()[0]['restaurant_label'],'일식 · 돈가스')
        self.assertEqual(self.api('?category=food&cuisine=food-korean'),[])

    def test_register_query_prefills_new_and_legacy_links(self):
        for query in ('cuisine=food-japanese&detail=food-ramen','detail=food-ramen'):
            page=self.member.get('/map/register?category=food&'+query).text
            self.assertIn(f'value="{self.term("food-japanese")["id"]}" selected',page)
            self.assertIn(f'value="{self.term("food-ramen")["id"]}" selected',page)

    def test_admin_parent_management_counts_deletion_and_active(self):
        p=self.create_food(child='food-noodles')
        korean=self.term('food-korean'); noodle=self.term('food-noodles')
        response=self.post(self.admin,f"/admin/categories/{korean['id']}/delete")
        self.assertEqual(response.status_code,303)
        self.assertTrue(self.term('food-korean'))
        page=self.admin.get('/admin/classifications/restaurant_subcategory').text
        self.assertIn('현재 1개 장소에서 사용 중',page)
        self.assertIn('상위 분류',page)
        payload=dict(name='향토음식',slug='local-cuisine',type='restaurant_subcategory',parent_id=str(korean['id']),sort_order='25',is_active='1')
        self.assertEqual(self.post(self.admin,'/admin/categories/new',**payload).status_code,303)
        child=self.term('local-cuisine');self.assertEqual(child['parent_id'],korean['id'])
        # A descendant cannot become the parent of its ancestor or create a third level.
        self.post(self.admin,f"/admin/categories/{korean['id']}/edit",**{**payload,'name':'한식','slug':'food-korean','parent_id':str(child['id'])})
        self.assertEqual(self.term('food-korean')['parent_id'],self.category['food'])
        self.post(self.admin,'/admin/categories/new',**{**payload,'name':'잘못된 깊이','slug':'bad-depth','parent_id':str(child['id'])})
        self.assertEqual(self.rows("SELECT id FROM categories WHERE slug='bad-depth'"),[])
        self.post(self.admin,f"/admin/categories/{korean['id']}/edit",name='한식',slug='food-korean',sort_order='1')
        self.assertEqual(self.api('?category=food&detail=food-noodles'),[])
        self.assertEqual(len(self.api('?category=food')),1)
        self.assertEqual(self.post(self.member,'/map/register',**self.food(child='food-noodles')).status_code,400)
        self.assertIn('한식 · 국수·면',self.visitor.get('/place/'+p['slug']).text)

    def test_same_name_different_parent_and_parent_move(self):
        korean=self.term('food-korean'); japanese=self.term('food-japanese')
        for parent,slug in ((korean,'duplicate-k'),(japanese,'duplicate-j')):
            self.post(self.admin,'/admin/categories/new',type='restaurant_subcategory',parent_id=str(parent['id']),name='특선요리',slug=slug,sort_order='0',is_active='1')
        self.assertEqual(len(self.rows("SELECT id FROM categories WHERE name='특선요리'")),2)
        self.post(self.admin,'/admin/categories/new',type='restaurant_subcategory',parent_id=str(korean['id']),name='특선요리',slug='duplicate-bad',sort_order='0',is_active='1')
        self.assertEqual(self.rows("SELECT id FROM categories WHERE slug='duplicate-bad'"),[])
        p=self.create_food(child='food-noodles')
        noodle=self.term('food-noodles')
        self.post(self.admin,f"/admin/categories/{noodle['id']}/edit",name=noodle['name'],slug=noodle['slug'],parent_id=str(japanese['id']),sort_order='0',is_active='1')
        self.assertEqual(self.api()[0]['restaurant_label'],'일식 · 국수·면')
        self.assertEqual(len(self.api('?category=food&cuisine=food-japanese')),1)
        self.assertEqual(self.api('?category=food&cuisine=food-korean'),[])

    def test_ambiguous_legacy_and_multiple_links_survive_unrelated_edit(self):
        p=self.create_food(child='food-noodles')
        extra=self.term('food-grill')['id']; legacy=self.term('food-asian')['id']
        for term_id in (extra,legacy):self.execute('INSERT INTO place_category_links VALUES (?,?)',(p['id'],term_id))
        before=self.rows('SELECT * FROM place_category_links ORDER BY category_id')
        self.assertEqual(self.post(self.admin,f"/admin/places/{p['id']}/edit",**self.food(name='이름만 수정',published='1',taxonomy_present='1')).status_code,303)
        self.assertEqual(self.rows('SELECT * FROM place_category_links ORDER BY category_id'),before)
        self.assertEqual(len(self.api('?category=food&detail=food-asian')),1)
        self.assertEqual(self.post(self.admin,f"/admin/places/{p['id']}/edit",**self.food(child='food-noodles',published='1',taxonomy_present='1',restaurant_replace='1')).status_code,303)
        self.assertEqual(self.rows('SELECT category_id FROM place_category_links'),[{'category_id':self.term('food-noodles')['id']}])

    def test_legacy_place_name_contract_ignores_same_named_food_term(self):
        self.post(self.admin,'/admin/categories/new',name='기타',slug='custom-other',sort_order='10',is_active='1')
        response=self.post(self.admin,'/admin/places/new',name='기타 장소',category='기타',region='애월',published='1')
        self.assertEqual(response.status_code,303)
        self.assertEqual(self.api()[0]['category_slug'],'custom-other')


class RestaurantMigrationTest(unittest.TestCase):
    def test_backup_failure_stops_startup_before_any_database_write(self):
        with tempfile.TemporaryDirectory() as folder:
            database=Path(folder)/'original.db'
            with closing(sqlite3.connect(database)) as conn,conn:conn.execute('CREATE TABLE preserved (id)')
            original=database.read_bytes()
            with patch('app.backup_before_restaurants',side_effect=OSError('backup unavailable')):
                with self.assertRaises(OSError):create_app({'TESTING':True,'DATABASE':str(database),'SECRET_KEY':'test'})
            self.assertEqual(database.read_bytes(),original)

    def test_backup_mapping_preservation_ambiguity_and_idempotence(self):
        with tempfile.TemporaryDirectory() as folder:
            database=str(Path(folder)/'jejuno.db')
            with patch('app.migrate_restaurants'), patch('app.migrate_cafe_features'):
                create_app({'TESTING':True,'DATABASE':database,'SECRET_KEY':'test','SKIP_SEED':True})
            with closing(sqlite3.connect(database)) as conn,conn:
                food=conn.execute("SELECT id FROM categories WHERE slug='food'").fetchone()[0]
                terms=conn.execute("SELECT id,slug FROM categories WHERE type='restaurant_subcategory'").fetchall()
                for term_id,slug in terms:
                    pid=conn.execute("INSERT INTO places(name,slug,region,category,category_id,signature_menu) VALUES (?,?,?,'맛집',?,?)",(slug,slug,'애월',food,'고기국수')).lastrowid
                    conn.execute('INSERT INTO place_category_links VALUES (?,?)',(pid,term_id))
                before={t:conn.execute('SELECT * FROM '+t).fetchall() for t in ('places','place_category_links','users','images','place_pick_periods','best_collection_places')}
            backup=backup_before_restaurants(database)
            self.assertTrue(Path(backup).is_file())
            migrate_restaurants(database,backup)
            with closing(sqlite3.connect(database)) as conn:
                for table,rows in before.items():self.assertEqual(conn.execute('SELECT * FROM '+table).fetchall(),rows,table)
                for slug,parent in [('food-korean','food'),('food-western','food'),('food-vietnamese','food-asia'),('food-pork-cutlet','food-japanese'),('food-ramen','food-japanese'),('food-noodles','food-korean'),('food-grill','food-korean'),('food-seafood','food-korean')]:
                    self.assertEqual(conn.execute('SELECT p.slug FROM categories c JOIN categories p ON p.id=c.parent_id WHERE c.slug=?',(slug,)).fetchone()[0],parent)
                self.assertEqual(conn.execute("SELECT name,is_legacy FROM categories WHERE slug='food-asian'").fetchone(),('기타 아시아',1))
                self.assertEqual(conn.execute("SELECT count(*) FROM categories WHERE name='기타'").fetchone()[0],2)
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
                after=conn.execute('SELECT * FROM categories').fetchall()
            self.assertIsNone(backup_before_restaurants(database))
            migrate_restaurants(database,None)
            with closing(sqlite3.connect(database)) as conn:self.assertEqual(conn.execute('SELECT * FROM categories').fetchall(),after)
            with closing(sqlite3.connect(backup)) as conn:
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertFalse(conn.execute('SELECT 1 FROM schema_migrations WHERE version=?',(VERSION,)).fetchone())
