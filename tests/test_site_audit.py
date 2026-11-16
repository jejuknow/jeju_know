"""Urgent audit: public boundaries and policy checks, always isolated SQLite."""
import json
import re
import unittest
from datetime import date
from unittest.mock import patch
from contextlib import closing
import sqlite3

from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
import test_community as base
from public_places import CARD_FIELDS, MAP_FIELDS
from new_visibility import new_clause


class SiteAuditTest(unittest.TestCase):
    setUp = base.CommunityTest.setUp
    tearDown = base.CommunityTest.tearDown
    setUpClass = classmethod(base.CommunityTest.setUpClass.__func__)
    rows = base.CommunityTest.rows
    execute = base.CommunityTest.execute
    csrf = base.CommunityTest.csrf
    post = base.CommunityTest.post
    form = base.CommunityTest.form
    create = base.CommunityTest.create

    def fixture(self, n=0, **values):
        fields = dict(slug=f'audit-{n}', name=f'격리 장소 {n}', region='애월', category='카페',
                      category_id=self.category['cafe'], published=1, latitude=33.4, longitude=126.4,
                      one_line='소개', note='PRIVATE ADMIN NOTE', **values)
        return self.execute('INSERT INTO places('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',tuple(fields.values()))

    def test_legal_public_routes_and_form_links(self):
        for path in ['/privacy','/terms','/signup','/contact']:
            r=self.visitor.get(path)
            self.assertEqual(r.status_code,200,path)
            self.assertIn('href="/privacy"',r.text)
            self.assertIn('href="/terms"',r.text)
            self.assertNotIn('>관리자</a>',r.text)
        self.assertNotIn('바로 공개',self.visitor.get('/signup').text)
        self.assertIn('자동 파기 기간은 정해져 있지 않습니다',self.visitor.get('/contact').text)

    def test_member_waits_even_with_old_auto_publish_setting_then_admin_approves(self):
        self.assertEqual(self.create().status_code,303)
        p=self.rows('SELECT * FROM places')[0]
        self.assertEqual((p['published'],p['verification_status']),(0,'unverified'))
        self.assertIn(p['name'],self.admin.get('/admin/community').text)
        for path in ['/','/find','/map','/new','/places','/best','/sitemap.xml']:
            self.assertNotIn(p['slug'],self.visitor.get(path).text,path)
        self.assertEqual(self.visitor.get('/place/'+p['slug']).status_code,404)
        self.assertEqual(self.visitor.get('/api/places?view=map').json(),[])
        self.assertEqual(self.post(self.admin,f"/admin/places/{p['id']}/toggle-published").status_code,303)
        self.assertEqual(self.visitor.get('/api/places').json()[0]['id'],p['id'])
        self.assertEqual(self.visitor.get('/place/'+p['slug']).status_code,200)
        # Subsequent member changes must be reviewed again, including legacy rows.
        self.execute('UPDATE places SET auto_check_status=NULL')
        self.assertEqual(self.post(self.member,f"/places/{p['id']}/edit",**self.form(name='수정 대기')).status_code,303)
        self.assertEqual(self.rows('SELECT published FROM places')[0]['published'],0)
        self.assertEqual(self.visitor.get('/api/places').json(),[])

    def test_members_cannot_publish_and_csrf_guards_approval(self):
        self.assertEqual(self.create(published='1').status_code,403)
        self.create();pid=self.rows('SELECT id FROM places')[0]['id']
        self.assertEqual(self.post(self.member,f'/admin/places/{pid}/toggle-published').status_code,303)
        self.assertEqual(self.admin.post(f'/admin/places/{pid}/toggle-published').status_code,400)
        self.assertEqual(self.rows('SELECT published FROM places')[0]['published'],0)

    def test_example_excluded_across_search_collections_rankings_and_detail(self):
        pid=self.fixture(is_example=1)
        collection=self.execute("INSERT INTO best_collections(slug,title,published) VALUES ('audit','Audit',1)")
        self.execute('INSERT INTO best_collection_places VALUES (?,?,1)',(collection,pid))
        for path in ['/','/find','/map','/new','/places','/best/audit','/sitemap.xml']:
            self.assertNotIn('audit-0',self.visitor.get(path).text,path)
        self.assertEqual(self.visitor.get('/place/audit-0').status_code,404)
        self.assertEqual(self.visitor.get('/api/places').json(),[])
        self.assertEqual(len(self.rows('SELECT * FROM places')),1)

    def test_api_allowlist_pagination_total_and_map_contract(self):
        for i in range(7):self.fixture(i)
        first=self.visitor.get('/api/places?limit=2&page=1')
        second=self.visitor.get('/api/places?limit=2&page=2')
        self.assertEqual(first.headers['x-total-count'],'7')
        self.assertEqual(len(first.json()),2)
        self.assertFalse({p['id'] for p in first.json()} & {p['id'] for p in second.json()})
        self.assertEqual(set(first.json()[0]),set(CARD_FIELDS))
        mapped=self.visitor.get('/api/places?view=map').json()
        self.assertEqual(len(mapped),7);self.assertEqual(len({p['id'] for p in mapped}),7)
        self.assertEqual(set(mapped[0]),set(MAP_FIELDS)|{'classification_terms'})
        self.assertNotIn('PRIVATE ADMIN NOTE',json.dumps(mapped))
        for name in ['images','legacy_image_url','note','verified_by','submitted_by_user_id','auto_check_reason']:
            self.assertNotIn(name,first.json()[0]);self.assertNotIn(name,mapped[0])
        for query in ['limit=0','limit=101','page=0','view=internal','page=no']:
            self.assertEqual(self.visitor.get('/api/places?'+query).status_code,422)
        self.assertEqual(self.visitor.get('/api/places?limit=2&page=9').json(),[])

    def test_new_calendar_boundary_known_manual_override_and_unknown(self):
        self.fixture()
        cases=[(2026,11,'auto',True),(2026,5,'auto',True),(2026,4,'auto',False),
               (2025,8,'auto',False),(2025,8,'show',False),(2026,12,'show',False),
               (None,None,'auto',False),(None,None,'show',True),(2025,None,'show',True),
               (2026,11,'hide',False)]
        with closing(sqlite3.connect(self.database)) as c:
            for year,month,override,expected in cases:
                c.execute('UPDATE places SET opened_year=?,opened_month=?,new_override=?',(year,month,override))
                sql,args=new_clause(date(2026,11,16))
                self.assertEqual(bool(c.execute('SELECT 1 FROM places p JOIN categories c ON c.id=p.category_id WHERE '+sql,args).fetchone()),expected,(year,month,override))
            c.execute("UPDATE places SET opened_year=2026,opened_month=7,new_override='auto'")
            for now,want in [(date(2027,1,1),True),(date(2027,2,1),False)]:
                sql,args=new_clause(now)
                self.assertEqual(bool(c.execute('SELECT 1 FROM places p JOIN categories c ON c.id=p.category_id WHERE '+sql,args).fetchone()),want)

    def test_headers_https_only_and_analytics_nonce_distinct_from_csrf(self):
        response=self.visitor.get('/')
        self.assertNotIn('strict-transport-security',response.headers)
        self.assertEqual(response.headers['permissions-policy'],'geolocation=(self), camera=(), microphone=()')
        self.assertIn('frame-ancestors',response.headers['content-security-policy'])
        self.assertIn('https://dapi.kakao.com',response.headers['content-security-policy-report-only'])
        self.assertNotIn('default-src',response.headers['content-security-policy'])
        token=re.search('name="analytics-token" content="([^"]+)"',response.text)[1]
        payload=URLSafeTimedSerializer('community-test-only',salt='jejuno-analytics-page-v1').loads(token)
        self.assertIn('nonce',payload);self.assertNotIn('csrf',payload)
        session=json.loads(self.rows("SELECT data FROM auth_sessions WHERE user_id IS NULL AND admin_id IS NULL")[-1]['data'])
        self.assertNotEqual(session['csrf_token'],payload['nonce'])
        # The HTTPS production middleware configuration is exercised without a real DB write.
        from auth_security import ServerSessionMiddleware
        import asyncio
        async def app(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b''})
        headers=[]
        async def receive():return {'type':'http.request','body':b''}
        async def send(message):
            if message['type']=='http.response.start':headers.extend(message['headers'])
        middleware=ServerSessionMiddleware(app,self.database,secure=True)
        asyncio.run(middleware({'type':'http','scheme':'https','method':'GET','path':'/','headers':[(b'host',b'www.jeju-know.com')],'server':('www.jeju-know.com',443),'query_string':b''},receive,send))
        self.assertIn((b'strict-transport-security',b'max-age=31536000'),headers)
