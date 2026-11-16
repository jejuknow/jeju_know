"""Exact subtype authorization, editorial order and public privacy with isolated SQLite."""
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
from auth_security import hash_password
import test_community as base
from restaurant_ranking_migration import migrate, VERSION
from restaurant_rankings import order_state, subtypes


class RestaurantRankingTest(unittest.TestCase):
    tearDown = base.CommunityTest.tearDown
    rows = base.CommunityTest.rows
    execute = base.CommunityTest.execute
    csrf = base.CommunityTest.csrf
    post = base.CommunityTest.post

    @classmethod
    def setUpClass(cls): cls.password_hash = hash_password('community-test-secret')

    def setUp(self):
        base.CommunityTest.setUp(self)
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory = sqlite3.Row
            self.terms = subtypes(conn)
        self.term = self.terms[0]
        self.second = self.terms[1]

    def fixture_place(self, name='격리 비교 장소', terms=None, public=1, category='food'):
        n = len(self.rows('SELECT id FROM places'))+1
        place_id = self.execute('INSERT INTO places(slug,name,region,category,category_id,published) VALUES(?,?,?,?,?,?)',
                               (f'ranking-place-{n}',name,'애월','맛집' if category=='food' else '카페',self.category[category],public))
        for term in terms if terms is not None else [self.term['id']]:
            self.execute('INSERT INTO place_category_links VALUES (?,?)',(place_id,term))
        return place_id

    def new_ranking(self, term=None, **changes):
        response = self.post(self.admin,'/admin/best/rankings/new',subtype_taxonomy_id=term or self.term['id'],title='격리 검증 BEST',**changes)
        self.assertEqual(response.status_code,303,response.text[-2000:])
        return self.rows('SELECT * FROM restaurant_rankings ORDER BY id DESC')[0]

    def state(self, ranking_id):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory=sqlite3.Row
            return order_state(conn,conn.execute('SELECT * FROM restaurant_rankings WHERE id=?',(ranking_id,)).fetchone())

    def change(self, ranking, action, **values):
        values.setdefault('version',self.state(ranking['id'])['version'])
        return self.post(self.admin,f"/admin/best/rankings/{ranking['id']}/{action}",**values)

    def visible(self, ranking): return self.visitor.get('/best/ranking/'+ranking['slug'])

    def test_migration_backup_idempotency_and_preservation(self):
        receipt=self.rows('SELECT backup_path FROM schema_migrations WHERE version=?',(VERSION,))[0]
        backup=Path(receipt['backup_path'])
        self.assertTrue(backup.name.startswith('jejuno-before-restaurant-ranking-'))
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='restaurant_rankings'").fetchone())
        before={t:self.rows('SELECT * FROM '+t) for t in ['places','categories','place_category_links','best_collections','users','admins']}
        migrate(self.database); migrate(self.database)
        for table, rows in before.items(): self.assertEqual(self.rows('SELECT * FROM '+table),rows)
        self.assertEqual(len(self.rows('SELECT * FROM schema_migrations WHERE version=?',(VERSION,))),1)

    def test_only_active_official_leaf_and_one_representative(self):
        for term in [self.category['food'],self.term['parent_id'],self.category['cafe'],999999]:
            self.assertEqual(self.post(self.admin,'/admin/best/rankings/new',subtype_taxonomy_id=term,title='invalid').status_code,400)
        self.execute('UPDATE categories SET is_active=0 WHERE id=?',(self.term['id'],))
        self.assertEqual(self.post(self.admin,'/admin/best/rankings/new',subtype_taxonomy_id=self.term['id'],title='invalid').status_code,400)
        self.execute('UPDATE categories SET is_active=1 WHERE id=?',(self.term['id'],))
        r=self.new_ranking()
        self.assertEqual(self.post(self.admin,'/admin/best/rankings/new',subtype_taxonomy_id=self.term['id'],title='duplicate').status_code,400)
        self.assertEqual(self.change(r,'delete').status_code,303)
        self.assertEqual(self.post(self.admin,'/admin/best/rankings/new',subtype_taxonomy_id=self.term['id'],title='duplicate').status_code,400)
        self.assertEqual(self.change(r,'restore').status_code,303)
        self.assertEqual(self.rows('SELECT id,slug FROM restaurant_rankings'),[dict(id=r['id'],slug=r['slug'])])

    def test_exact_subtype_multiple_membership_wrong_place_and_duplicate(self):
        r=self.new_ranking();s=self.new_ranking(self.second['id'])
        p=self.fixture_place(terms=[self.term['id'],self.second['id']])
        for ranking in (r,s): self.assertEqual(self.change(ranking,'add',place_id=p).status_code,303)
        self.assertEqual(self.change(r,'add',place_id=p).status_code,400)
        for p in [self.fixture_place(terms=[self.second['id']]),self.fixture_place(category='cafe'),999999]:
            self.assertEqual(self.change(r,'add',place_id=p).status_code,400)
        p=self.fixture_place();self.execute('UPDATE places SET deleted_at=CURRENT_TIMESTAMP WHERE id=?',(p,))
        self.assertEqual(self.change(r,'add',place_id=p).status_code,400)

    def test_publish_empty_and_private_only_blocked(self):
        r=self.new_ranking()
        self.assertEqual(self.change(r,'toggle').status_code,400)
        p=self.fixture_place(public=0);self.change(r,'add',place_id=p)
        self.assertEqual(self.change(r,'toggle').status_code,400)
        self.assertEqual(self.visible(r).status_code,404)
        self.assertNotIn('JEJU_KNOW RANKING',self.visitor.get('/best').text)

    def test_public_private_filter_seo_share_and_blank_reason(self):
        r=self.new_ranking(criteria_note='격리 평가 기준',reference_date='2026-11-16')
        for name,public in [('숨겨진 장소 비밀',0),('공개 비교 장소',1)]: self.change(r,'add',place_id=self.fixture_place(name,public=public))
        self.assertEqual(self.change(r,'toggle').status_code,303)
        for page in [self.visible(r),self.visitor.get('/best')]:
            self.assertEqual(page.status_code,200)
            self.assertIn('공개 비교 장소',page.text);self.assertNotIn('숨겨진 장소 비밀',page.text)
        page=self.visible(r).text
        for text in ['현재 제주노가 직접 비교한 장소','격리 평가 기준','2026-11-16','rel="canonical"','property="og:image"','data-share-content','data-copy-link']:
            self.assertIn(text,page)
        self.assertNotIn('이 순위에 선정한 이유',page)
        self.assertIn('/best/ranking/'+r['slug'],self.visitor.get('/sitemap.xml').text)
        self.change(r,'toggle')
        self.assertNotIn('/best/ranking/'+r['slug'],self.visitor.get('/sitemap.xml').text)
        self.assertEqual(self.visible(r).status_code,404)

    def test_relation_change_inactive_merged_and_deleted_are_excluded(self):
        r=self.new_ranking();ids=[self.fixture_place(f'장소 {i}') for i in range(4)]
        for p in ids:self.change(r,'add',place_id=p)
        self.change(r,'toggle')
        self.execute('DELETE FROM place_category_links WHERE place_id=?',(ids[0],))
        self.execute('UPDATE places SET merged_into_id=? WHERE id=?',(ids[3],ids[1]))
        self.execute('UPDATE places SET deleted_at=CURRENT_TIMESTAMP WHERE id=?',(ids[2],))
        page=self.visible(r).text
        for name in ['장소 0','장소 1','장소 2']:self.assertNotIn(name,page)
        self.assertIn('장소 3',page)
        self.change(r,'toggle');self.assertEqual(self.change(r,'toggle').status_code,400)
        self.execute('UPDATE categories SET is_active=0 WHERE id=?',(self.term['id'],))
        self.assertEqual(self.visible(r).status_code,404)
        self.assertIn('기준 세부유형이 비활성',self.admin.get(f"/admin/best/rankings/{r['id']}/edit").text)

    def test_order_is_atomic_consecutive_and_stale_safe_with_stable_ids(self):
        r=self.new_ranking()
        for i in range(4):self.change(r,'add',place_id=self.fixture_place(f'장소 {i}'))
        state=self.state(r['id']);ids=[x['id'] for x in state['items']]
        response=self.change(r,'reorder',ordered_ids=json.dumps(ids[::-1]))
        self.assertEqual(response.status_code,200)
        self.assertEqual([x['id'] for x in response.json()['items']],ids[::-1])
        self.assertEqual(self.change(r,'reorder',ordered_ids=json.dumps(ids),version=state['version']).status_code,409)
        for invalid in [ids[:2],ids+[ids[0]],ids[:-1]+[999],['1'],True]:
            self.assertEqual(self.change(r,'reorder',ordered_ids=json.dumps(invalid)).status_code,400)
        self.change(r,'remove',item_id=ids[2])
        current=self.state(r['id'])['items']
        self.assertEqual([x['id'] for x in current],[ids[3],ids[1],ids[0]])
        self.assertEqual([x['sort_order'] for x in current],[1,2,3])
        self.assertEqual(len(self.rows('SELECT id FROM places')),4)
        self.assertTrue(self.rows("SELECT * FROM admin_activity_logs WHERE action='랭킹 순위 변경'"))

    def test_edit_slug_reason_escaping_limits_and_subtype_tamper(self):
        r=self.new_ranking();self.change(r,'add',place_id=self.fixture_place())
        item=self.state(r['id'])['items'][0]['id']
        self.assertEqual(self.change(r,'reason',item_id=item,reason='<script>alert(1)</script>').status_code,303)
        self.assertEqual(self.change(r,'reason',item_id=item,reason='x'*2001).status_code,400)
        self.assertEqual(self.change(r,'edit',title='바뀐 제목',subtype_taxonomy_id=r['subtype_taxonomy_id'],is_public='1').status_code,303)
        self.assertEqual(self.rows('SELECT slug FROM restaurant_rankings')[0]['slug'],r['slug'])
        page=self.visible(r).text
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',page)
        self.assertNotIn('<script>alert(1)</script>',page)
        self.assertEqual(self.change(r,'edit',title='변조',subtype_taxonomy_id=self.second['id']).status_code,400)
        self.assertEqual(self.change(r,'edit',title='x'*121,subtype_taxonomy_id=r['subtype_taxonomy_id']).status_code,400)
        self.assertEqual(self.change(r,'edit',title='날짜',subtype_taxonomy_id=r['subtype_taxonomy_id'],reference_date='2026-99-99').status_code,400)

    def test_authentication_csrf_sub_admin_and_cross_ranking_ids(self):
        r=self.new_ranking();s=self.new_ranking(self.second['id'])
        for client in (self.member,self.visitor):
            self.assertEqual(client.get('/admin/best/rankings',follow_redirects=False).status_code,303)
            self.assertEqual(client.post(f"/admin/best/rankings/{r['id']}/toggle",follow_redirects=False).status_code,303)
        self.assertEqual(self.admin.post(f"/admin/best/rankings/{r['id']}/toggle",data={}).status_code,400)
        self.change(r,'add',place_id=self.fixture_place());item=self.state(r['id'])['items'][0]['id']
        self.assertEqual(self.change(s,'remove',item_id=item).status_code,400)
        self.execute("INSERT INTO admins(username,password_hash,role) VALUES('sub',?,'sub_admin')",(self.password_hash,))
        sub=TestClient(self.app);self.clients.append(sub)
        sub.post('/admin/login',data={'username':'sub','password':'community-test-secret','csrf_token':self.csrf(sub,'/admin/login')})
        self.assertEqual(sub.get('/admin/best/rankings/new').status_code,200)
        result=sub.post(f"/admin/best/rankings/{r['id']}/reason",data={'csrf_token':self.csrf(sub,'/admin'),'version':self.state(r['id'])['version'],'item_id':item,'reason':'서브관리자 입력'},follow_redirects=False)
        self.assertEqual(result.status_code,303)
        self.execute("UPDATE admins SET is_active=0 WHERE username='sub'")
        self.assertEqual(sub.get('/admin/best/rankings',follow_redirects=False).status_code,303)

    def test_existing_best_and_entrypoints_and_reference_deletion_guards(self):
        p=self.fixture_place();r=self.new_ranking();self.change(r,'add',place_id=p);self.change(r,'toggle')
        c=self.execute("INSERT INTO best_collections(slug,title,description,published) VALUES('existing-best','기존 상황별 모음','유지할 소개',1)")
        self.execute('INSERT INTO best_collection_places VALUES(?,?,?)',(c,p,1))
        self.assertIn('기존 상황별 모음',self.visitor.get('/best').text)
        self.assertIn('유지할 소개',self.visitor.get('/best/existing-best').text)
        for path in ['/','/find','/map','/new','/news','/best','/admin/best','/admin/best/rankings','/admin/places','/admin/classifications/restaurant_subcategory','/admin/best/rankings/new']:
            client=self.admin if path.startswith('/admin') else self.visitor
            self.assertEqual(client.get(path).status_code,200,path)
        self.assertEqual(self.post(self.admin,f'/admin/places/{p}/delete').status_code,303)
        self.assertEqual(self.visible(r).status_code,404)
        self.assertTrue(self.rows('SELECT deleted_at FROM places WHERE id=?',(p,))[0]['deleted_at'])
        empty=self.new_ranking(self.second['id'])
        self.post(self.admin,f"/admin/categories/{self.second['id']}/delete")
        self.assertTrue(self.rows('SELECT id FROM categories WHERE id=?',(empty['subtype_taxonomy_id'],)))
