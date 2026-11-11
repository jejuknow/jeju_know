"""Restaurant tree administration: counts, returns, guards and preservation."""
import sqlite3
import unittest
from contextlib import closing
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from admin_restaurant_taxonomy import PATH, usage_counts, view
from restaurant_taxonomy import usage_count
from auth_security import hash_password
import test_community as helpers


class RestaurantAdminTest(unittest.TestCase):
    setUp = helpers.CommunityTest.setUp
    tearDown = helpers.CommunityTest.tearDown
    rows = helpers.CommunityTest.rows
    execute = helpers.CommunityTest.execute
    csrf = helpers.CommunityTest.csrf
    post = helpers.CommunityTest.post
    form = helpers.CommunityTest.form
    create = helpers.CommunityTest.create
    place = helpers.CommunityTest.place

    @classmethod
    def setUpClass(cls): cls.password_hash = hash_password('community-test-secret')

    def term(self, slug):
        return self.rows('SELECT * FROM categories WHERE slug=?', (slug,))[0]

    def test_all_rows_no_pagination_preserve_ids_order_and_inactive(self):
        item = self.term('food-udon')
        self.execute('UPDATE categories SET is_active=0 WHERE id=?', (item['id'],))
        before = self.rows('SELECT * FROM categories ORDER BY id')
        response = self.admin.get(PATH+'?page=7&per_page=10')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('admin-pagination', response.text)
        self.assertNotIn('name="per_page"', response.text)
        groups = response.context['groups']
        shown = [g['parent'] for g in groups] + [c for g in groups for c in g['children']]
        expected = [r for r in before if r['type']=='restaurant_subcategory']
        self.assertEqual({r['id'] for r in shown}, {r['id'] for r in expected})
        self.assertEqual([g['parent']['sort_order'] for g in groups], sorted(g['parent']['sort_order'] for g in groups))
        for group in groups:
            self.assertEqual(group['children'], sorted(group['children'], key=lambda r: (r['sort_order'],r['id'])))
        self.assertEqual([g['opened'] for g in groups], [True]+[False]*(len(groups)-1))
        self.assertEqual(self.rows('SELECT * FROM categories ORDER BY id'), before)
        self.assertTrue(any(r['id']==item['id'] and not r['is_active'] for r in shown))

    def test_search_parent_child_empty_and_query_state(self):
        groups = self.admin.get(PATH+'?q=우동').context['groups']
        self.assertEqual([g['parent']['name'] for g in groups], ['일식'])
        self.assertEqual([r['name'] for r in groups[0]['children']], ['우동'])
        self.assertTrue(groups[0]['opened'])
        full = self.admin.get(PATH+'?q=한식').context['groups']
        self.assertEqual(full[0]['total'], len(full[0]['children']))
        self.assertIn('일치하는 맛집 유형이 없습니다.',self.admin.get(PATH+'?q=없는분류이름').text)
        self.assertIn('등록된 세부유형이 없습니다.',self.admin.get(PATH+'?q=기타').text)
        ids = [self.term(s)['id'] for s in ('food-japanese','food-chinese')]
        opened = self.admin.get(PATH+'?open='+','.join(map(str,ids))).context['groups']
        self.assertEqual([g['parent']['id'] for g in opened if g['opened']], ids)
        self.assertFalse(any(g['opened'] for g in self.admin.get(PATH+'?open=').context['groups']))
        self.assertFalse(any(g['opened'] for g in self.admin.get(PATH+'?q=우동&open=').context['groups']))

    def test_prefill_create_edit_toggle_and_parent_move_return(self):
        japanese = self.term('food-japanese')['id']
        korean = self.term('food-korean')['id']
        page = self.admin.get(PATH).text
        add = page.split(f'id="add-child-{japanese}"')[1].split('</form>')[0]
        self.assertIn(f'value="{japanese}" selected',add)
        payload = dict(name='점검 유형',slug='qa-subtype',type='restaurant_subcategory',
                       parent_id=japanese,sort_order=72,is_active='1',
                       next=PATH+f'?q=우동&open={korean},{japanese}')
        response = self.post(self.admin,'/admin/categories/new',**payload)
        row = self.term('qa-subtype')
        self.assertEqual(row['parent_id'],japanese)
        self.assertEqual(urlsplit(response.headers['location']).fragment, f"taxonomy-{row['id']}")
        self.assertNotIn('q=',response.headers['location'])
        self.assertIn(str(japanese),parse_qs(urlsplit(response.headers['location']).query)['open'][0])
        for active in ('', '1'):
            response = self.post(self.admin,f"/admin/categories/{row['id']}/edit",**{**payload,'name':'수정된 유형','is_active':active})
            self.assertEqual(self.term('qa-subtype')['is_active'],bool(active))
            groups=self.admin.get(response.headers['location']).context['groups']
            self.assertTrue(next(g for g in groups if g['parent']['id']==japanese)['opened'])
        response=self.post(self.admin,f"/admin/categories/{row['id']}/edit",**{**payload,'parent_id':korean})
        self.assertTrue(next(g for g in self.admin.get(response.headers['location']).context['groups'] if g['parent']['id']==korean)['opened'])
        parent=self.post(self.admin,'/admin/categories/new',**{**payload,'name':'새 대분류','slug':'qa-major','parent_id':self.category['food']})
        self.assertIn(str(self.term('qa-major')['id']),parse_qs(urlsplit(parent.headers['location']).query)['open'][0])

    def test_parent_validation_and_server_guards(self):
        payload=dict(name='검증',slug='qa-invalid',type='restaurant_subcategory',sort_order=2,next=PATH)
        before=self.rows('SELECT * FROM categories ORDER BY id')
        for parent in (999999,self.category['cafe'],self.term('food-udon')['id'],'bad'):
            self.post(self.admin,'/admin/categories/new',**payload,parent_id=parent)
        self.assertEqual(self.rows('SELECT * FROM categories ORDER BY id'),before)
        self.assertEqual(self.admin.post('/admin/categories/new',data=payload).status_code,400)
        for client in (self.member,self.visitor):
            self.assertEqual(client.get(PATH,follow_redirects=False).status_code,303)
            self.assertEqual(self.post(client,'/admin/categories/new',**payload).status_code,303)
        self.execute("INSERT INTO admins(username,password_hash,role) VALUES (?,?,'sub_admin')",('subqa',self.password_hash))
        with TestClient(self.app) as sub:
            sub.post('/admin/login',data=dict(csrf_token=self.csrf(sub,'/admin/login'),username='subqa',password='community-test-secret'))
            self.assertEqual(sub.get(PATH).status_code,403)
            for route in ('new',f"{self.term('food-udon')['id']}/edit",f"{self.term('food-udon')['id']}/delete"):
                self.assertEqual(sub.post('/admin/categories/'+route,data=payload).status_code,403)

    def test_batched_counts_match_existing_and_delete_safety(self):
        parent=self.term('food-japanese')['id']
        children=[self.term(s)['id'] for s in ('food-pork-cutlet','food-udon','food-soba')]
        place=self.place(category_id=self.category['food'],restaurant_main_id=parent,restaurant_subtype_ids=list(map(str,children)))
        self.execute('INSERT INTO place_category_links(place_id,category_id) VALUES (?,?)',(place['id'],parent))
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory=sqlite3.Row
            statements=[];conn.set_trace_callback(statements.append)
            counts=usage_counts(conn)
            self.assertEqual(len(statements),1)
            self.assertEqual(counts[parent],1)
            for child in children: self.assertEqual(counts[child],1)
            for row in conn.execute("SELECT id FROM categories WHERE type='restaurant_subcategory'").fetchall():
                self.assertEqual(counts.get(row[0],0),usage_count(conn,row[0]))
        for item in [parent,*children]:
            self.post(self.admin,f'/admin/categories/{item}/delete',next=PATH)
            self.assertTrue(self.rows('SELECT id FROM categories WHERE id=?',(item,)))
        empty_parent=self.term('food-chinese')['id']
        self.post(self.admin,f'/admin/categories/{empty_parent}/delete',next=PATH)
        self.assertTrue(self.rows('SELECT id FROM categories WHERE id=?',(empty_parent,)))
        unused=self.term('food-ramen')['id']
        self.post(self.admin,f'/admin/categories/{unused}/delete',next=PATH)
        self.assertFalse(self.rows('SELECT id FROM categories WHERE id=?',(unused,)))
        self.assertEqual(len(self.rows('SELECT * FROM place_category_links WHERE place_id=?',(place['id'],))),4)

    def test_other_classifications_share_flat_controls_and_keep_editor(self):
        for kind in ('region','place','cafe_feature','companion'):
            response=self.admin.get('/admin/classifications/'+kind)
            self.assertEqual(response.status_code,200)
            self.assertNotIn('admin-pagination',response.text)
            self.assertIn('data-taxonomy-admin',response.text)
            self.assertIn('admin-inline-editor',response.text)
            self.assertNotIn('restaurantTaxonomy',response.text)
        alias=self.admin.get('/admin/categories?type=restaurant_subcategory')
        self.assertIn('restaurantTaxonomy',alias.text)
