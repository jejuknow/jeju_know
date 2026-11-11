"""Flat taxonomy stores share controls without changing stable content identifiers."""
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from fastapi.testclient import TestClient

from auth_security import hash_password
from admin_settings import catalog
from taxonomy_order import state, flat_parent, catalog_id, standardize, STANDARD_VERSION
import test_community as helpers


class TaxonomyStandardizationTest(unittest.TestCase):
    setUp = helpers.CommunityTest.setUp
    tearDown = helpers.CommunityTest.tearDown
    rows = helpers.CommunityTest.rows
    execute = helpers.CommunityTest.execute
    csrf = helpers.CommunityTest.csrf
    post = helpers.CommunityTest.post
    form = helpers.CommunityTest.form
    create = helpers.CommunityTest.create
    place = helpers.CommunityTest.place
    api = helpers.CommunityTest.api

    @classmethod
    def setUpClass(cls): cls.password_hash = hash_password('community-test-secret')

    def current(self, kind):
        with closing(sqlite3.connect(self.database)) as c:
            c.row_factory = sqlite3.Row
            return state(c, flat_parent(c, kind), kind)

    def ids(self, kind):
        items = self.current(kind)['items']
        self.assertEqual([r['sort_order'] for r in items], list(range(1, len(items)+1)))
        return [r['id'] for r in items]

    def reorder(self, kind, ids, version=None):
        return self.post(self.admin, f'/admin/classifications/{kind}/reorder',
                         ordered_ids=json.dumps(ids), version=version or self.current(kind)['version'])

    def toggle(self, kind, item_id, enabled):
        return self.post(self.admin, f'/admin/classifications/{kind}/{item_id}/active',
                         is_active=str(enabled), version=self.current(kind)['version'])

    def add(self, kind, name, **extra):
        payload = dict(name=name, is_active='1', **extra)
        if kind in ('region','companion'):
            response = self.post(self.admin, '/admin/classifications/'+kind, action='new', **payload)
            item_id = catalog_id(kind, name)
        else:
            response = self.post(self.admin, '/admin/categories/new', type=kind, **payload)
            rows = self.rows('SELECT * FROM categories WHERE name=?', (name,))
            self.assertTrue(rows, response.text)
            item_id = rows[0]['id']
        self.assertEqual(response.status_code, 303)
        self.assertIn('#taxonomy-'+str(item_id), response.headers['location'])
        return item_id

    def test_all_flat_menus_render_complete_searchable_list(self):
        for kind in ('region','place','cafe_feature','companion'):
            response = self.admin.get('/admin/classifications/'+kind+'?per_page=1&page=999')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text.count('data-order-item='), len(self.ids(kind)))
            self.assertIn('data-taxonomy-admin', response.text)
            self.assertNotIn('data-taxonomy-group=', response.text)
            self.assertNotIn('class="admin-pagination"', response.text)
            search = self.admin.get('/admin/classifications/'+kind, params={'q':'없는 분류'})
            self.assertEqual(search.text.count('data-order-item='), 0)
            self.assertIn('순서 변경은 검색을 초기화한 후', search.text)

    def test_insert_move_delete_and_independent_stores(self):
        for kind in ('region','place','cafe_feature','companion'):
            with self.subTest(kind=kind):
                before = self.ids(kind)
                other = self.current('companion' if kind!='companion' else 'cafe_feature')
                first = self.add(kind, kind+' first', sort_order='1')
                middle = self.add(kind, kind+' middle', sort_order='2')
                last = self.add(kind, kind+' last')
                expected = [first, middle, *before, last]
                self.assertEqual(self.ids(kind), expected)
                self.assertEqual(self.reorder(kind, expected[::-1]).status_code, 200)
                self.assertEqual(self.ids(kind), expected[::-1])
                if kind in ('region','companion'):
                    result = self.post(self.admin, '/admin/classifications/'+kind, action='delete', value=kind+' middle')
                else:
                    result = self.post(self.admin, f'/admin/categories/{middle}/delete')
                self.assertEqual(result.status_code,303)
                self.assertIn('#taxonomy-',result.headers['location'])
                self.assertEqual(self.ids(kind), [i for i in expected[::-1] if i!=middle])
                self.assertEqual(self.current('companion' if kind!='companion' else 'cafe_feature'), other)

    def test_catalog_rename_keeps_value_and_place_data(self):
        place = self.place()
        self.execute('UPDATE places SET companions=? WHERE id=?', (json.dumps(['친구']), place['id']))
        before = self.rows('SELECT * FROM places')
        for kind, value in (('region','애월'),('companion','친구')):
            item_id = catalog_id(kind,value)
            response=self.post(self.admin, '/admin/classifications/'+kind, action='edit',value=value,
                name=value+' 표시',sort_order='2',is_active='1')
            self.assertEqual(response.status_code,303)
            self.assertEqual(self.ids(kind)[1],item_id)
            stored=json.loads(self.rows('SELECT value FROM site_settings WHERE key=?',('taxonomy:'+kind,))[0]['value'])
            row=next(r for r in stored if r['value']==value)
            self.assertEqual(row['label'],value+' 표시')
            self.assertNotIn('id',row)
            self.assertEqual(self.post(self.admin,'/admin/classifications/'+kind,action='delete',value=value).status_code,409)
        self.assertEqual(self.rows('SELECT * FROM places'), before)

    def test_category_name_preserves_auto_slug_and_explicit_change_is_guarded(self):
        for kind in ('place','cafe_feature'):
            item_id=self.add(kind,kind+' 자동 분류')
            old=self.rows('SELECT * FROM categories WHERE id=?',(item_id,))[0]
            self.assertTrue(old['slug'].startswith('place-' if kind=='place' else 'cafe-feature-'))
            data=dict(type=kind,name=kind+' 이름 수정',is_active='1',sort_order='2',slug='ignored')
            self.post(self.admin,f'/admin/categories/{item_id}/edit',**data)
            self.assertEqual(self.rows('SELECT slug FROM categories WHERE id=?',(item_id,))[0]['slug'],old['slug'])
            data.update(slug_edit='1',slug=kind.replace('_','-')+'-manual',original_slug=old['slug'])
            self.post(self.admin,f'/admin/categories/{item_id}/edit',**data)
            self.assertEqual(self.rows('SELECT slug FROM categories WHERE id=?',(item_id,))[0]['slug'],old['slug'])
            self.post(self.admin,f'/admin/categories/{item_id}/edit',**data,slug_change_confirm='1')
            self.assertEqual(self.rows('SELECT slug FROM categories WHERE id=?',(item_id,))[0]['slug'],data['slug'])

    def test_core_delete_slug_protected_and_unused_category_toggle_available(self):
        before=self.rows('SELECT * FROM categories ORDER BY id')
        for slug in ('food','cafe','attraction'):
            item_id=self.category[slug]
            self.post(self.admin,f'/admin/categories/{item_id}/delete')
            self.post(self.admin,f'/admin/categories/{item_id}/edit',name='changed',type='place',is_active='1',
                slug_edit='1',slug='changed-'+slug,original_slug=slug,slug_change_confirm='1')
        self.assertEqual(self.rows('SELECT * FROM categories ORDER BY id'),before)
        custom=self.add('place','새 장소 카테고리')
        self.assertEqual(self.toggle('place',custom,0).status_code,200)

    def test_toggle_retains_relations_and_excludes_public_choices(self):
        cafe=self.rows("SELECT * FROM categories WHERE type='cafe_feature' AND is_legacy=0 ORDER BY sort_order,id")[0]
        p=self.place()
        self.execute('INSERT INTO place_category_links(place_id,category_id) VALUES (?,?)',(p['id'],cafe['id']))
        self.execute('UPDATE places SET companions=? WHERE id=?',(json.dumps(['친구']),p['id']))
        links=self.rows('SELECT * FROM place_category_links ORDER BY 1,2');places=self.rows('SELECT * FROM places')
        for kind,item in (('region',catalog_id('region','애월')),('companion',catalog_id('companion','친구')),('cafe_feature',cafe['id'])):
            self.assertEqual(self.toggle(kind,item,0).status_code,200)
        public=self.visitor.get('/api/place-taxonomy').json()
        self.assertNotIn('애월',public['regions']);self.assertNotIn('친구',public['companions'])
        self.assertNotIn(cafe['id'],[t['id'] for t in public['terms']])
        self.assertEqual(self.rows('SELECT * FROM places'),places)
        self.assertEqual(self.rows('SELECT * FROM place_category_links ORDER BY 1,2'),links)
        self.assertEqual(self.visitor.get('/place/'+p['slug']).status_code,200)

    def test_order_reaches_public_taxonomy_and_member_has_no_companion_input(self):
        for kind in ('region','place','cafe_feature','companion'):
            self.assertEqual(self.reorder(kind,self.ids(kind)[::-1]).status_code,200)
        public=self.visitor.get('/api/place-taxonomy').json()
        for kind,key in (('place','categories'),('cafe_feature','terms')):
            ids=[r['id'] for r in public[key] if r['type']==kind]
            expected=[i for i in self.ids(kind) if i in ids]
            self.assertEqual(ids,expected)
        for kind,key in (('region','regions'),('companion','companions')):
            self.assertEqual([catalog_id(kind,value) for value in public[key]],self.ids(kind))
        self.assertNotIn('name="companions"',self.member.get('/map/register?category=cafe').text)

    def test_invalid_scopes_duplicate_partial_stale_and_csrf(self):
        for kind in ('region','place','cafe_feature','companion'):
            ids=self.ids(kind);before=self.current(kind)
            wrong=self.ids('companion' if kind!='companion' else 'region')[0]
            for invalid in (ids[:-1],ids+[ids[0]],ids[:-1]+[wrong],['not-an-id'],[True]):
                self.assertEqual(self.reorder(kind,invalid).status_code,400)
            self.assertEqual(self.reorder(kind,ids[::-1],version='stale').status_code,409)
            self.assertEqual(self.admin.post('/admin/classifications/'+kind+'/reorder',data={}).status_code,400)
            self.assertEqual(self.current(kind),before)
            self.assertEqual(self.post(self.member,'/admin/classifications/'+kind+'/reorder').status_code,303)
            self.assertEqual(self.post(self.visitor,'/admin/classifications/'+kind+'/reorder').status_code,303)
        self.assertEqual(self.toggle('cafe_feature',self.category['food'],0).status_code,404)

    def test_database_failure_rolls_back_category_and_catalog_mutations(self):
        for kind in ('region','cafe_feature'):
            before=self.current(kind)
            if kind=='region':
                self.execute("CREATE TRIGGER fail_standard BEFORE INSERT ON site_settings BEGIN SELECT RAISE(ABORT,'isolated failure'); END")
            else:
                self.execute("CREATE TRIGGER fail_standard BEFORE UPDATE ON categories BEGIN SELECT RAISE(ABORT,'isolated failure'); END")
            self.assertEqual(self.reorder(kind,self.ids(kind)[::-1]).status_code,503)
            self.assertEqual(self.toggle(kind,self.ids(kind)[0],0).status_code,503)
            self.assertEqual(self.current(kind),before)
            self.execute('DROP TRIGGER fail_standard')

    def test_subadmin_cannot_use_any_new_mutation(self):
        self.execute("INSERT INTO admins(username,password_hash,role) VALUES (?,?,'sub_admin')",('standard-sub',self.password_hash))
        with TestClient(self.app) as sub:
            sub.post('/admin/login',data=dict(csrf_token=self.csrf(sub,'/admin/login'),
                username='standard-sub',password='community-test-secret'))
            for kind in ('region','place','cafe_feature','companion'):
                item_id=self.ids(kind)[0]
                for suffix in ('/reorder','/'+str(item_id)+'/active'):
                    self.assertEqual(sub.post('/admin/classifications/'+kind+suffix,data={}).status_code,403)

    def test_normalization_backup_preserves_non_order_data_and_is_idempotent(self):
        self.execute('DELETE FROM schema_migrations WHERE version=?',(STANDARD_VERSION,))
        self.execute("UPDATE categories SET sort_order=sort_order*3 WHERE type IN ('place','cafe_feature')")
        self.add('region','간격 점검')
        self.execute("UPDATE site_settings SET value=replace(value,'\"sort_order\": 2,','\"sort_order\": 20,') WHERE key='taxonomy:region'")
        columns=[r['name'] for r in self.rows('PRAGMA table_info(categories)') if r['name']!='sort_order']
        select='SELECT '+','.join(columns)+' FROM categories ORDER BY id'
        before=self.rows(select);schema=self.rows('SELECT * FROM sqlite_master ORDER BY name')
        standardize(self.database)
        self.assertEqual(self.rows(select),before);self.assertEqual(self.rows('SELECT * FROM sqlite_master ORDER BY name'),schema)
        for kind in ('region','place','cafe_feature','companion'):self.ids(kind)
        receipt=self.rows('SELECT * FROM schema_migrations WHERE version=?',(STANDARD_VERSION,))[0]
        self.assertTrue(Path(receipt['backup_path']).is_file())
        with closing(sqlite3.connect(receipt['backup_path'])) as c:
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            c.row_factory=sqlite3.Row
            self.assertEqual([dict(r) for r in c.execute(select)],before)
        current=self.rows('SELECT * FROM categories ORDER BY id')
        standardize(self.database)
        self.assertEqual(self.rows('SELECT * FROM categories ORDER BY id'),current)


if __name__=='__main__': unittest.main()
