"""BEST search, atomic membership/order, permissions and data preservation."""
import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

from auth_security import hash_password
from best_places import membership
import test_community as base


class BestEditorTests(unittest.TestCase):
    tearDown = base.CommunityTest.tearDown
    rows = base.CommunityTest.rows
    execute = base.CommunityTest.execute
    csrf = base.CommunityTest.csrf
    post = base.CommunityTest.post

    @classmethod
    def setUpClass(cls): cls.password_hash = hash_password('community-test-secret')

    def setUp(self):
        base.CommunityTest.setUp(self)
        self.cid = self.execute("INSERT INTO best_collections(slug,title,description,published) VALUES('qa-best','격리 BEST','소개 보존',1)")
        self.url = f'/admin/best/{self.cid}/places'
        self.food = self.category['food']; self.cafe = self.category['cafe']
        self.major = self.rows("SELECT id FROM categories WHERE slug='food-japanese'")[0]['id']
        self.subtypes = [r['id'] for r in self.rows('SELECT id FROM categories WHERE parent_id=? AND is_active=1 AND is_legacy=0 ORDER BY id', (self.major,))][:3]
        self.feature = self.rows("SELECT id FROM categories WHERE type='cafe_feature' AND is_active=1 AND is_legacy=0 ORDER BY id LIMIT 1")[0]['id']

    def place(self, name, category=None, public=1, region='구좌', terms=()):
        pid = self.execute('INSERT INTO places(slug,name,region,category,category_id,published) VALUES (?,?,?,?,?,?)',
                           ('qa-'+str(len(self.rows('SELECT id FROM places'))), name, region, '장소', category or self.food, public))
        for term in terms: self.execute('INSERT INTO place_category_links VALUES (?,?)', (pid,term))
        return pid

    def state(self):
        with closing(sqlite3.connect(self.database)) as conn:
            return membership(conn, self.cid)

    def change(self, action, ids, **values):
        return self.post(self.admin, self.url+'/manage', action=action, ids=json.dumps(ids),
                         **{'version': self.state()['version'], **values})

    def search(self, **params):
        response = self.admin.get(self.url+'/search', params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        return response.json()

    def test_search_hierarchy_multiple_subtypes_region_and_terms(self):
        first = self.place('천일 검증', terms=self.subtypes)
        second = self.place('구좌카페', self.cafe, terms=[self.feature])
        self.place('비활성 장소', public=0, region='애월')
        self.assertEqual([p['id'] for p in self.search(category=self.food,q='천일')['items']], [first])
        for term in self.subtypes:
            self.assertEqual([p['id'] for p in self.search(category=self.food,major=self.major,term=term)['items']], [first])
        self.assertEqual([p['id'] for p in self.search(category=self.food,major=self.major)['items']], [first])
        label = self.rows('SELECT name FROM categories WHERE id=?', (self.feature,))[0]['name']
        self.assertEqual([p['id'] for p in self.search(q='구좌 '+label,category=self.cafe,term=self.feature,region='구좌')['items']], [second])
        self.assertEqual(len(self.search(region='애월')['items']),1)
        self.assertEqual(self.search(q="' OR 1=1 --")['total'],0)
        self.assertEqual(self.admin.get(self.url+'/search',params={'category':self.cafe,'major':self.major}).status_code,400)

    def test_active_taxonomy_is_db_defined_and_gets_do_not_write(self):
        self.execute("UPDATE categories SET name='새 카페 특징',sort_order=1 WHERE id=?", (self.feature,))
        hidden=self.subtypes[0]
        self.execute('UPDATE categories SET is_active=0 WHERE id=?',(hidden,))
        self.execute("INSERT INTO site_settings(key,value) VALUES('taxonomy:region',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps([
            dict(value='구좌',label='구좌 표시',is_active=True,sort_order=1),
            dict(value='애월',label='애월',is_active=False,sort_order=2)]),))
        page = self.admin.get(f'/admin/best/{self.cid}/edit')
        self.assertEqual(page.status_code,200)
        options=page.context['best_filters']
        self.assertTrue(any(t['name']=='새 카페 특징' for t in options['terms']))
        self.assertNotIn(hidden,[t['id'] for t in options['terms']])
        self.assertNotIn('애월',[r['value'] for r in options['regions']])
        self.assertEqual(self.admin.get(self.url+'/search',params={'region':'애월'}).status_code,400)
        self.assertEqual(self.admin.get(self.url+'/search',params={'category':self.food,'major':self.major,'term':hidden}).status_code,400)
        self.execute('UPDATE categories SET is_active=0 WHERE id=?',(self.food,))
        self.assertNotIn(self.food,[c['id'] for c in self.admin.get(f'/admin/best/{self.cid}/edit').context['best_filters']['categories']])

    def test_page_size_thumbnail_private_badge_and_minimal_payload(self):
        ids=[self.place(f'카페 {i:02}',self.cafe,public=i%2) for i in range(25)]
        self.execute("UPDATE places SET image_url='https://example.com/photo.jpg' WHERE id=?",(ids[0],))
        first,second = self.search(),self.search(page=2)
        self.assertEqual((first['total'],first['page_size'],len(first['items']),len(second['items'])),(25,20,20,5))
        self.assertEqual(first['items'][0]['thumbnail'],'https://example.com/photo.jpg')
        self.assertEqual(first['items'][0]['is_public'],0)
        self.assertEqual(set(first['items'][0]), {'id','name','region','category_id','category','category_slug','is_public','unavailable','summary','thumbnail','included'})

    def test_batch_appends_selection_order_and_rejects_duplicates_atomically(self):
        a,b,c=[self.place(n) for n in ('A','B','C')]
        self.execute('INSERT INTO best_collection_places VALUES(?,?,?)',(self.cid,a,90))
        before=self.rows('SELECT * FROM best_collection_places')
        self.admin.get(f'/admin/best/{self.cid}/edit');self.search()
        self.assertEqual(self.rows('SELECT * FROM best_collection_places'),before)
        result=self.change('add',[c,b]); self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.state()['ids'],[a,c,b])
        self.assertEqual([r['sort_order'] for r in self.rows('SELECT * FROM best_collection_places ORDER BY sort_order')],[1,2,3])
        self.assertTrue(all(p['included'] for p in self.search()['items']))
        before=self.rows('SELECT * FROM best_collection_places')
        d=self.place('D')
        for ids in ([d,a],[d,d],[d,999999],[True]):
            self.assertIn(self.change('add',ids).status_code,(400,409))
            self.assertEqual(self.rows('SELECT * FROM best_collection_places'),before)
        self.execute('UPDATE places SET deleted_at=CURRENT_TIMESTAMP WHERE id=?',(d,))
        self.assertEqual(self.change('add',[d]).status_code,400)

    def test_reorder_conflicts_removal_and_public_cross_category_order(self):
        a=self.place('첫 맛집'); b=self.place('둘째 카페',self.cafe); c=self.place('비공개',public=0)
        self.assertEqual(self.change('add',[a,b,c]).status_code,200)
        version=self.state()['version']
        self.assertEqual(self.change('reorder',[b,c,a]).status_code,200)
        self.assertEqual(self.change('reorder',[a,b,c],version=version).status_code,409)
        for ids in ([b,a],[b,b,a],[b,c,a,999]):
            self.assertEqual(self.change('reorder',ids).status_code,400)
            self.assertEqual(self.state()['ids'],[b,c,a])
        public=self.visitor.get('/best/qa-best')
        self.assertEqual([p['id'] for p in public.context['places']],[b,a])
        self.assertNotIn('비공개',public.text)
        before=self.rows('SELECT * FROM places')
        self.assertEqual(self.change('remove',[c]).status_code,200)
        self.assertEqual(self.rows('SELECT * FROM places'),before)
        self.assertEqual([r['sort_order'] for r in self.rows('SELECT * FROM best_collection_places ORDER BY sort_order')],[1,2])

    def test_permissions_csrf_subadmin_and_audit(self):
        pid=self.place('권한 검증')
        for client in (self.member,self.visitor):
            self.assertEqual(client.get(self.url+'/search',follow_redirects=False).status_code,303)
            self.assertEqual(client.post(self.url+'/manage',data={},follow_redirects=False).status_code,303)
        self.assertEqual(self.admin.post(self.url+'/manage',data={'action':'add','ids':json.dumps([pid])}).status_code,400)
        self.execute("INSERT INTO admins(username,password_hash,role) VALUES('spare-super',?,'super_admin')",(self.password_hash,))
        self.execute("UPDATE admins SET role='sub_admin' WHERE username='admin'")
        self.assertEqual(self.search()['total'],1)
        self.assertEqual(self.change('add',[pid]).status_code,200)
        self.assertEqual(self.rows("SELECT action FROM admin_activity_logs WHERE target_type='BEST'")[-1]['action'],'BEST 장소 추가')
        self.execute("UPDATE admins SET is_active=0 WHERE username='admin'")
        self.assertEqual(self.admin.post(self.url+'/manage',data={},follow_redirects=False).status_code,303)

    def test_storage_failure_rolls_back_entire_batch(self):
        a,b=[self.place(n) for n in ('A','B')]
        self.execute('INSERT INTO best_collection_places VALUES(?,?,?)',(self.cid,a,22))
        original=self.rows('SELECT * FROM best_collection_places')
        with patch('best_places.audit',side_effect=sqlite3.OperationalError('test failure')):
            self.assertEqual(self.change('add',[b]).status_code,503)
        self.assertEqual(self.rows('SELECT * FROM best_collection_places'),original)

    def test_deleted_relations_are_preserved_and_can_be_explicitly_excluded(self):
        a,b=[self.place(n) for n in ('정상','삭제된 장소')]
        self.change('add',[a,b])
        self.execute('UPDATE places SET deleted_at=CURRENT_TIMESTAMP WHERE id=?',(b,))
        page=self.admin.get(f'/admin/best/{self.cid}/edit')
        self.assertEqual([p['id'] for p in page.context['best_state']['items']],[a,b])
        self.assertEqual(page.context['best_state']['items'][1]['unavailable'],1)
        self.assertEqual(self.search()['total'],1)
        self.assertEqual(self.change('reorder',[b,a]).status_code,200)
        self.assertEqual(self.change('remove',[b]).status_code,200)
        self.assertEqual(len(self.rows('SELECT id FROM places')),2)

    def test_collection_fields_routes_and_schema_remain_unchanged(self):
        pid=self.place('보존'); self.change('add',[pid])
        before=self.rows('SELECT * FROM best_collections WHERE id=?',(self.cid,))[0]
        schema=self.rows('SELECT name,sql FROM sqlite_master ORDER BY name')
        migrations=self.rows('SELECT * FROM schema_migrations')
        for path in ('/','/best','/best/qa-best','/find','/map','/new','/news','/admin/best',f'/admin/best/{self.cid}/edit','/admin/classifications/place'):
            client=self.admin if path.startswith('/admin') else self.visitor
            self.assertEqual(client.get(path).status_code,200,path)
        self.assertEqual(self.rows('SELECT name,sql FROM sqlite_master ORDER BY name'),schema)
        self.assertEqual(self.rows('SELECT * FROM schema_migrations'),migrations)
        self.assertEqual(self.rows('SELECT * FROM best_collections WHERE id=?',(self.cid,))[0],before)
        page=self.admin.get(f'/admin/best/{self.cid}/edit')
        self.assertNotIn('name="place_id"',page.text)
        self.assertEqual(page.text.count('name="sort_order"'),1) # Only the collection's own position.

    def test_legacy_add_and_collection_crud_preserve_place_and_identity(self):
        pid=self.place('기존 등록 경로')
        self.assertEqual(self.post(self.admin,self.url,place_id=pid,action='add',sort_order=999).status_code,303)
        original=self.state()
        self.assertEqual(self.post(self.admin,self.url,place_id=pid,action='add',sort_order=0).status_code,409)
        self.assertEqual(self.state(),original)
        self.assertEqual(self.post(self.admin,f'/admin/best/{self.cid}/edit',title='수정한 모음',description='소개',
                                  image_url='',sort_order=2,published='1').status_code,303)
        self.assertEqual(self.rows('SELECT slug FROM best_collections WHERE id=?',(self.cid,))[0]['slug'],'qa-best')
        self.assertEqual(self.post(self.admin,f'/admin/best/{self.cid}/copy').status_code,303)
        copied=self.rows('SELECT * FROM best_collections ORDER BY id DESC')[0]
        self.assertEqual(copied['published'],0)
        self.assertEqual(self.rows('SELECT place_id FROM best_collection_places WHERE collection_id=?',(copied['id'],)),[{'place_id':pid}])
        self.assertEqual(self.post(self.admin,f'/admin/best/{copied["id"]}/delete').status_code,303)
        self.assertEqual(self.post(self.admin,f'/admin/best/{self.cid}/toggle').status_code,303)
        self.assertEqual(self.visitor.get('/best/qa-best').status_code,404)
        self.assertEqual(self.rows('SELECT id FROM places'),[{'id':pid}])


if __name__ == '__main__': unittest.main()
