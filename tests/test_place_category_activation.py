"""Live usage, stable historical IDs and atomic activation policy across entrypoints."""
import re
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from auth_security import hash_password
from place_category_policy import activation_policy
from csv_place_import import normalize_common_fields
import test_taxonomy_admin_standardization as base


class PlaceCategoryActivationTest(unittest.TestCase):
    setUp = base.TaxonomyStandardizationTest.setUp
    tearDown = base.TaxonomyStandardizationTest.tearDown
    rows = base.TaxonomyStandardizationTest.rows
    execute = base.TaxonomyStandardizationTest.execute
    csrf = base.TaxonomyStandardizationTest.csrf
    post = base.TaxonomyStandardizationTest.post
    form = base.TaxonomyStandardizationTest.form
    create = base.TaxonomyStandardizationTest.create
    place = base.TaxonomyStandardizationTest.place
    current = base.TaxonomyStandardizationTest.current
    toggle = base.TaxonomyStandardizationTest.toggle
    add = base.TaxonomyStandardizationTest.add

    @classmethod
    def setUpClass(cls): cls.password_hash = hash_password('community-test-secret')

    def policy(self, category_id):
        with closing(sqlite3.connect(self.database)) as c:
            c.row_factory = sqlite3.Row
            row = c.execute('SELECT * FROM categories WHERE id=?', (category_id,)).fetchone()
            return activation_policy(c, row)

    def test_live_count_and_usage_link_include_private_pending_and_descendant_links_once(self):
        root = self.category['attraction']
        places = [self.place(name='조사 장소 '+str(i),address='제주특별자치도 제주시 조사로 '+str(100+i),latitude=str(33.45+i*.02),category_id=root) for i in range(4)]
        self.execute('UPDATE places SET published=0 WHERE id=?', (places[1]['id'],))
        self.execute('UPDATE places SET deleted_at=CURRENT_TIMESTAMP,published=0 WHERE id=?', (places[2]['id'],))
        self.execute('UPDATE places SET merged_into_id=?,published=0 WHERE id=?', (places[0]['id'],places[3]['id']))
        child = self.execute("INSERT INTO categories(name,slug,type,parent_id) VALUES ('하위','child','cafe_feature',?)", (root,))
        self.execute('INSERT INTO place_category_links VALUES (?,?)', (places[0]['id'],child))
        other = self.place(name='연결만 있는 장소',address='제주특별자치도 제주시 다른로 123',latitude='33.58')
        self.execute('INSERT INTO place_category_links VALUES (?,?)', (other['id'],child))
        self.assertEqual(self.policy(root)['place_count'],3)
        page = self.admin.get('/admin/places?category=attraction').text
        for p in [*places[:2],other]: self.assertIn(p['name'],page)
        for p in places[2:]: self.assertNotIn(p['name'],page)
        page = self.admin.get('/admin/classifications/place').text
        self.assertIn('가볼 곳 사용 장소 3곳 확인',page)
        self.assertIn('🔒 ON ·',page)
        self.assertIn('공개·비공개·검토 중 장소',page)

    def test_each_unused_core_can_off_on_without_identity_or_relation_changes(self):
        before = self.rows('SELECT id,name,slug,parent_id,sort_order FROM categories ORDER BY id')
        links = self.rows('SELECT * FROM place_category_links')
        for slug in ('food','cafe','attraction'):
            category_id = self.category[slug]
            self.assertEqual(self.toggle('place',category_id,0).status_code,200)
            self.assertNotIn(slug,[c['slug'] for c in self.visitor.get('/api/categories').json()])
            self.assertEqual(self.toggle('place',category_id,1).status_code,200)
        self.assertEqual(self.rows('SELECT id,name,slug,parent_id,sort_order FROM categories ORDER BY id'),before)
        self.assertEqual(self.rows('SELECT * FROM place_category_links'),links)

    def test_latest_usage_blocks_stale_toggle_and_edit_including_private_places(self):
        root = self.category['attraction']; old = self.current('place')['version']
        p = self.place(category_id=root)
        self.execute('UPDATE places SET published=0 WHERE id=?',(p['id'],))
        response = self.post(self.admin,f'/admin/classifications/place/{root}/active',is_active='0',version=old)
        self.assertEqual(response.status_code,400)
        self.assertIn('현재 1곳',response.json()['error'])
        self.post(self.admin,f'/admin/categories/{root}/edit',name='바뀌면 안 됨',type='place',is_active='0')
        self.assertEqual(self.rows('SELECT name,is_active FROM categories WHERE id=?',(root,))[0],dict(name='가볼 곳',is_active=1))

    def test_custom_usage_and_explicit_active_dependency_are_guarded(self):
        root = self.add('place','격리 테스트 분류')
        self.place(category_id=root)
        self.assertEqual(self.toggle('place',root,0).status_code,400)
        with patch('place_category_policy.REQUIRED_ACTIVE_CATEGORIES', {'attraction':'격리 시스템 의존성'}):
            self.assertEqual(self.toggle('place',self.category['attraction'],0).status_code,400)
            self.assertIn('시스템 필수',self.admin.get('/admin/classifications/place').text)

    def test_deleted_only_allows_off_and_preserves_historical_links(self):
        root = self.category['attraction']; p = self.place(category_id=root)
        self.execute('INSERT INTO place_category_links VALUES (?,?)',(p['id'],root))
        self.execute('UPDATE places SET deleted_at=CURRENT_TIMESTAMP,published=0 WHERE id=?',(p['id'],))
        before = self.rows('SELECT * FROM places'); links = self.rows('SELECT * FROM place_category_links')
        self.assertEqual(self.policy(root)['place_count'],0)
        self.assertEqual(self.toggle('place',root,0).status_code,200)
        self.assertEqual(self.rows('SELECT * FROM places'),before)
        self.assertEqual(self.rows('SELECT * FROM place_category_links'),links)

    def test_inactive_choices_and_new_registrations_rejected_then_on_restores(self):
        root = self.category['attraction']
        self.assertEqual(self.toggle('place',root,0).status_code,200)
        for url in ('/','/find','/map','/new','/best','/api/places?category=attraction'):
            self.assertEqual(self.visitor.get(url).status_code,200,url)
        self.assertNotIn('attraction',[c['slug'] for c in self.visitor.get('/api/place-taxonomy').json()['categories']])
        self.assertNotIn('data-category="attraction"',self.visitor.get('/map').text)
        for client,url in ((self.member,'/map/register'),(self.admin,'/admin/places/new')):
            self.assertNotRegex(client.get(url).text,r'<option[^>]*data-slug="attraction"')
        self.assertEqual(self.create(category_id=root).status_code,400)
        self.assertEqual(self.post(self.admin,'/admin/places/new',**self.form(category_id=root)).status_code,400)
        self.assertEqual(self.rows('SELECT * FROM places'),[])
        self.assertEqual(self.toggle('place',root,1).status_code,200)
        self.assertIn('data-category="attraction"',self.visitor.get('/map').text)

    def test_name_edit_slug_and_core_delete_policy(self):
        root = self.category['attraction']
        self.post(self.admin,f'/admin/categories/{root}/edit',name='관광·체험',type='place',is_active='1')
        self.assertEqual(self.rows('SELECT name,slug FROM categories WHERE id=?',(root,))[0],dict(name='관광·체험',slug='attraction'))
        self.toggle('place',root,0)
        self.post(self.admin,f'/admin/categories/{root}/delete')
        self.assertEqual(len(self.rows('SELECT id FROM categories WHERE id=?',(root,))),1)
        self.post(self.admin,f'/admin/categories/{root}/edit',name='잘못된 변경',type='place',slug_edit='1',slug='changed',original_slug='attraction',slug_change_confirm='1')
        self.assertEqual(self.rows('SELECT slug FROM categories WHERE id=?',(root,))[0]['slug'],'attraction')

    def test_admin_creation_rechecks_after_image_preparation(self):
        from image_uploads import ImageStore
        original = ImageStore.prepare
        root = self.category['attraction']
        async def prepare(store,*args,**kwargs):
            plan = await original(store,*args,**kwargs)
            self.execute('UPDATE categories SET is_active=0 WHERE id=?',(root,))
            return plan
        with patch.object(ImageStore,'prepare',prepare):
            r = self.post(self.admin,'/admin/places/new',**self.form(category_id=root))
        self.assertEqual(r.status_code,400)
        self.assertEqual(self.rows('SELECT * FROM places'),[])

    def test_permissions_csrf_and_csv_off_validation(self):
        root=self.category['cafe']; path=f'/admin/classifications/place/{root}/active'
        for client in (self.visitor,self.member):
            self.assertIn(client.post(path,data={'is_active':'0'},follow_redirects=False).status_code,(303,403))
        self.assertEqual(self.admin.post(path,data={'is_active':'0'}).status_code,400)
        self.toggle('place',root,0)
        with closing(sqlite3.connect(self.database)) as c:
            c.row_factory=sqlite3.Row
            with self.assertRaisesRegex(ValueError,'활성 장소 카테고리'):
                normalize_common_fields(c,{},'cafe',None,Path(self.tmp.name))

    def test_existing_inactive_records_still_display_and_export(self):
        # Historical inactive records remain readable, even if created before this policy.
        from csv_place_export import export_places
        p=self.place()
        self.execute('UPDATE categories SET is_active=0 WHERE id=?',(self.category['cafe'],))
        self.assertEqual(self.visitor.get('/place/'+p['slug']).status_code,200)
        self.assertIn('비활성 · 기존 분류',self.admin.get(f"/admin/places/{p['id']}/edit").text)
        with closing(sqlite3.connect(self.database)) as c:
            c.row_factory=sqlite3.Row
            self.assertIn(p['name'],export_places(c,'cafe').decode('utf-8-sig'))
