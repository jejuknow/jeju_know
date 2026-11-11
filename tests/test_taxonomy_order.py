"""Transactional ordering, optimistic writes, data preservation and access control."""
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from auth_security import hash_password
from taxonomy_order import state, migrate, VERSION
from admin_restaurant_taxonomy import PATH
import test_community as helpers


class TaxonomyOrderTest(unittest.TestCase):
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

    def term(self, slug): return self.rows('SELECT * FROM categories WHERE slug=?', (slug,))[0]

    def add(self, name, parent, order=None):
        data=dict(name=name, parent_id=parent, type='restaurant_subcategory', is_active='1', next=PATH)
        if order is not None: data['sort_order']=order
        self.assertEqual(self.post(self.admin,'/admin/categories/new',**data).status_code,303)
        return self.rows('SELECT * FROM categories WHERE name=?',(name,))[0]

    def branch(self):
        parent=self.add('순서 점검 그룹',self.category['food'])['id']
        return parent,[self.add(name,parent)['id'] for name in ('A','B','C','D','E')]

    def current(self, parent):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory=sqlite3.Row
            return state(conn,parent)

    def ids(self, parent):
        current=self.current(parent)
        self.assertEqual([r['sort_order'] for r in current['items']],list(range(1,len(current['items'])+1)))
        return [r['id'] for r in current['items']]

    def reorder(self,parent,ids,version=None,client=None):
        return self.post(client or self.admin,'/admin/categories/reorder',parent_id=parent,
                         ordered_ids=json.dumps(ids),version=version or self.current(parent)['version'])

    def edit(self, item, **changes):
        return self.post(self.admin,f"/admin/categories/{item['id']}/edit",**dict(
            name=item['name'],parent_id=item['parent_id'],type='restaurant_subcategory',
            is_active='1',next=PATH,**changes))

    def test_insert_first_middle_append_clamp_and_group_independence(self):
        parent,ids=self.branch(); other=self.current(self.term('food-japanese')['id'])
        first=self.add('First',parent,1)['id']; ids.insert(0,first)
        middle=self.add('Middle',parent,3)['id']; ids.insert(2,middle)
        last=self.add('Last',parent,999)['id']; ids.append(last)
        default=self.add('Default',parent)['id'];ids.append(default)
        self.assertEqual(self.ids(parent),ids)
        self.assertEqual(self.current(other['parent_id']),other)

    def test_edit_forward_backwards_and_cross_parent_compacts_both(self):
        parent,ids=self.branch(); original=self.rows('SELECT * FROM categories WHERE id=?',(ids[3],))[0]
        self.edit(original,sort_order=2);expected=[ids[0],ids[3],ids[1],ids[2],ids[4]]
        self.assertEqual(self.ids(parent),expected)
        self.edit(original,sort_order=5);self.assertEqual(self.ids(parent),[ids[0],ids[1],ids[2],ids[4],ids[3]])
        destination=self.add('다른 점검 그룹',self.category['food'])['id']
        self.post(self.admin,f"/admin/categories/{original['id']}/edit",name='Moved',parent_id=destination,
                  type='restaurant_subcategory',is_active='1',next=PATH)
        self.assertEqual(self.ids(parent),[ids[0],ids[1],ids[2],ids[4]])
        self.assertEqual(self.ids(destination),[ids[3]])
        self.assertEqual(self.rows('SELECT slug FROM categories WHERE id=?',(ids[3],))[0]['slug'],original['slug'])

    def test_delete_compacts_and_used_delete_does_not_mutate_order(self):
        parent,ids=self.branch()
        response=self.post(self.admin,f'/admin/categories/{ids[1]}/delete',next=PATH+f'?open={parent}#restaurant-{parent}')
        self.assertEqual(self.ids(parent),[ids[0],*ids[2:]])
        self.assertIn(f'#taxonomy-{parent}',response.headers['location'])
        self.place(category_id=self.category['food'],restaurant_main_id=parent,restaurant_subtype_ids=[str(ids[2])])
        before=self.rows('SELECT * FROM categories ORDER BY id')
        for item in (ids[2],parent): self.post(self.admin,f'/admin/categories/{item}/delete',next=PATH)
        self.assertEqual(self.rows('SELECT * FROM categories ORDER BY id'),before)

    def test_reorder_and_major_order_preserve_identity_relations_and_legacy(self):
        parent,ids=self.branch()
        self.place(category_id=self.category['food'],restaurant_main_id=parent,restaurant_subtype_ids=list(map(str,ids[:3])))
        before=self.rows('SELECT id,name,slug,parent_id,is_active FROM categories ORDER BY id')
        links=self.rows('SELECT * FROM place_category_links ORDER BY 1,2')
        for order in ([ids[0],ids[4],ids[1],ids[2],ids[3]], list(reversed(ids)), ids):
            self.assertEqual(self.reorder(parent,order).status_code,200)
            self.assertEqual(self.ids(parent),order)
        majors=self.ids(self.category['food']);self.assertEqual(self.reorder(self.category['food'],majors[::-1]).status_code,200)
        self.assertEqual(self.ids(self.category['food']),majors[::-1])
        self.assertEqual(self.rows('SELECT id,name,slug,parent_id,is_active FROM categories ORDER BY id'),before)
        self.assertEqual(self.rows('SELECT * FROM place_category_links ORDER BY 1,2'),links)

    def test_invalid_id_sets_parent_scope_and_stale_version_rollback(self):
        parent,ids=self.branch();before=self.rows('SELECT * FROM categories ORDER BY id')
        for order in (ids[:-1],ids+[ids[0]],ids[:-1]+[999999],ids[:-1]+[self.term('food-udon')['id']],['1'],[True]):
            self.assertEqual(self.reorder(parent,order).status_code,400)
        self.assertEqual(self.reorder(self.category['cafe'],ids).status_code,400)
        old=self.current(parent)['version']
        self.assertEqual(self.reorder(parent,ids[::-1],version='outdated').status_code,409)
        self.assertEqual(self.rows('SELECT * FROM categories ORDER BY id'),before)
        self.add('New concurrent item',parent)
        self.assertEqual(self.reorder(parent,self.ids(parent)[::-1],version=old).status_code,409)

    def test_parallel_reorders_only_one_revision_wins(self):
        parent,ids=self.branch();version=self.current(parent)['version'];token=self.csrf(self.admin,'/admin')
        clients=[TestClient(self.app) for _ in range(2)]
        for client in clients: client.cookies.update(self.admin.cookies)
        def save(args):
            client,order=args
            return client.post('/admin/categories/reorder',data=dict(csrf_token=token,parent_id=parent,
                ordered_ids=json.dumps(order),version=version)).status_code
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                results=list(executor.map(save,zip(clients,[ids[::-1],ids[1:]+ids[:1]])))
            self.assertEqual(sorted(results),[200,409]);self.ids(parent)
        finally:
            for client in clients:client.close()

    def test_sql_failure_rolls_back_all_reorder_updates_and_toggle(self):
        parent,ids=self.branch();before=self.current(parent)
        self.execute(f"CREATE TRIGGER fail_reorder BEFORE UPDATE ON categories WHEN OLD.id={ids[2]} BEGIN SELECT RAISE(ABORT,'isolated failure'); END")
        self.assertEqual(self.reorder(parent,ids[1:]+ids[:1]).status_code,503)
        self.assertEqual(self.current(parent),before)
        response=self.post(self.admin,f'/admin/categories/{ids[2]}/active',is_active='0',version=before['version'])
        self.assertEqual(response.status_code,503);self.assertEqual(self.current(parent),before)

    def test_toggle_keeps_links_slug_order_and_filters_new_choices(self):
        parent,ids=self.branch();p=self.place(category_id=self.category['food'],restaurant_main_id=parent,
                                            restaurant_subtype_ids=list(map(str,ids[:3])))
        links=self.rows('SELECT * FROM place_category_links ORDER BY 1,2')
        identity=self.rows('SELECT id,slug,parent_id,sort_order FROM categories ORDER BY id')
        for active in ('0','1'):
            response=self.post(self.admin,f'/admin/categories/{ids[1]}/active',is_active=active,version=self.current(parent)['version'])
            self.assertEqual(response.status_code,200)
            terms=self.visitor.get('/api/place-taxonomy').json()['terms']
            self.assertEqual(any(t['id']==ids[1] for t in terms),active=='1')
            self.assertEqual(len(self.api('?category=food')),1)
        self.assertEqual(self.rows('SELECT * FROM place_category_links ORDER BY 1,2'),links)
        self.assertEqual(self.rows('SELECT id,slug,parent_id,sort_order FROM categories ORDER BY id'),identity)
        self.assertEqual(self.post(self.admin,f'/admin/categories/{ids[0]}/active',is_active='yes',version=self.current(parent)['version']).status_code,400)
        self.assertEqual(self.post(self.admin,f'/admin/categories/{ids[0]}/active',is_active='0',version='stale').status_code,409)
        self.assertEqual(self.post(self.admin,'/admin/categories/999999/active',is_active='0',version='x').status_code,404)

    def test_authorization_csrf_and_search_restrictions(self):
        parent,ids=self.branch();payload=dict(parent_id=parent,ordered_ids=json.dumps(ids[::-1]),version=self.current(parent)['version'],is_active='0')
        routes=['/admin/categories/reorder',f'/admin/categories/{ids[0]}/active']
        for route in routes:
            self.assertEqual(self.admin.post(route,data=payload).status_code,400)
            for client in (self.visitor,self.member):self.assertEqual(self.post(client,route,**payload).status_code,303)
        self.execute("INSERT INTO admins(username,password_hash,role) VALUES (?,?,'sub_admin')",('order-sub',self.password_hash))
        with TestClient(self.app) as sub:
            sub.post('/admin/login',data=dict(csrf_token=self.csrf(sub,'/admin/login'),username='order-sub',password='community-test-secret'))
            for route in routes:self.assertEqual(sub.post(route,data=payload).status_code,403)
        page=self.admin.get(PATH+'?q=우동')
        self.assertIn('data-searching="true"',page.text)
        self.assertIn('순서 변경은 검색을 초기화한 후 사용할 수 있습니다.',page.text)

    def test_normalization_backup_idempotence_and_failure_rollback(self):
        parent,ids=self.branch()
        self.execute('UPDATE categories SET sort_order=20 WHERE id=?',(ids[3],))
        self.execute('DELETE FROM schema_migrations WHERE version=?',(VERSION,))
        before=self.rows('SELECT * FROM categories ORDER BY id')
        schema=self.rows("SELECT name,sql FROM sqlite_master WHERE type IN ('table','index') ORDER BY name")
        original_order=self.current(parent)['items']
        migrate(self.database)
        self.assertEqual(self.ids(parent),[r['id'] for r in original_order])
        after=self.rows('SELECT * FROM categories ORDER BY id')
        self.assertEqual([{k:v for k,v in r.items() if k!='sort_order'} for r in before],
                         [{k:v for k,v in r.items() if k!='sort_order'} for r in after])
        self.assertEqual(self.rows("SELECT name,sql FROM sqlite_master WHERE type IN ('table','index') ORDER BY name"),schema)
        backup=self.rows('SELECT backup_path FROM schema_migrations WHERE version=?',(VERSION,))[0]['backup_path']
        self.assertTrue(Path(backup).is_file())
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('SELECT sort_order FROM categories WHERE id=?',(ids[3],)).fetchone()[0],20)
        migrate(self.database);self.assertEqual(self.rows('SELECT * FROM categories ORDER BY id'),after)
        self.execute('DELETE FROM schema_migrations WHERE version=?',(VERSION,))
        with patch('taxonomy_order.compact',side_effect=RuntimeError('test rollback')):
            with self.assertRaises(RuntimeError):migrate(self.database)
        self.assertEqual(self.rows('SELECT * FROM categories ORDER BY id'),after)
        self.assertFalse(self.rows('SELECT * FROM schema_migrations WHERE version=?',(VERSION,)))
