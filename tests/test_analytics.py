import html
import json
import re
import sqlite3
import time
import unittest
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from analytics import report, period, device_type, VISITOR_COOKIE, KST
from analytics_migrations import migrate, VERSION
from auth_security import hash_password
import test_community as helpers


class AnalyticsTest(unittest.TestCase):
    setUp = helpers.CommunityTest.setUp
    tearDown = helpers.CommunityTest.tearDown
    rows = helpers.CommunityTest.rows
    execute = helpers.CommunityTest.execute
    csrf = helpers.CommunityTest.csrf

    @classmethod
    def setUpClass(cls): cls.password_hash = hash_password('community-test-secret')

    def token(self,client=None,path='/'):
        response=(client or self.visitor).get(path)
        match=re.search('name="analytics-token" content="([^"]+)"',response.text)
        self.assertIsNotNone(match,response.text[:200])
        return html.unescape(match[1])

    def event(self,token,kind='page_view',client=None,**changes):
        return (client or self.visitor).post('/api/analytics/events',headers={'Origin':'http://testserver','X-Analytics-Token':token},json={'type':kind,'id':str(uuid.uuid4()),**changes})

    def stats(self,**params):
        with closing(sqlite3.connect(self.database)) as conn: return report(conn,params)

    def test_pages_unique_visitors_refresh_and_feature_counts(self):
        token=self.token()
        self.assertEqual(self.rows('SELECT * FROM analytics_events'),[])
        self.assertEqual(self.event(token).status_code,204)
        self.assertIn('HttpOnly',self.event(token).headers['set-cookie'])
        self.assertEqual(len(self.rows('SELECT * FROM analytics_events')),1)
        for path in ('/','/map','/new','/news','/best'):
            self.assertEqual(self.event(self.token(path=path)).status_code,204)
        stats=self.stats()
        self.assertEqual((stats['views'],stats['visitors'],stats['average']),(6,1,6))
        self.assertEqual([x['count'] for x in stats['features']],[1,1,1,1,0])
        self.assertEqual(len({r['session_id'] for r in self.rows('SELECT session_id FROM analytics_events')}),1)
        self.assertEqual(self.event(self.token(self.other),client=self.other).status_code,204)
        self.assertEqual(self.stats()['visitors'],2)
        self.assertEqual(self.visitor.get('/admin/analytics',follow_redirects=False).status_code,303)
        self.assertEqual(self.member.get('/admin/analytics',follow_redirects=False).status_code,303)
        self.assertEqual(self.admin.get('/admin/analytics').status_code,200)

    def test_find_values_dedup_and_official_taxonomy_only(self):
        token=self.token(path='/find')
        payload={'region':'구좌','category':'cafe','terms':['cafe-view','cafe-bakery','cafe-view','bad-value'],'companions':['연인','친구','연인'],'q':'private text'}
        for i in range(2): self.assertEqual(self.event(token,'find_search',filters=payload).status_code,204)
        events=self.rows("SELECT * FROM analytics_events WHERE event_type='find_search'")
        self.assertEqual(len(events),1)
        values=json.loads(events[0]['filters'])
        self.assertEqual(values['cafe_features'],['cafe-bakery','cafe-view'])
        self.assertEqual(values['companion'],['연인','친구'])
        self.assertNotIn('private',events[0]['filters'])
        ranks=self.stats()['rankings']
        self.assertEqual(ranks['region'],[{'label':'구좌','count':1}])
        self.assertEqual(sorted(r['label'] for r in ranks['cafe_features']),['베이커리','좋은 뷰'])
        self.execute("UPDATE categories SET name='새 뷰 이름' WHERE slug='cafe-view'")
        self.assertIn('새 뷰 이름',[r['label'] for r in self.stats()['rankings']['cafe_features']])

    def test_admin_bot_and_private_paths_excluded(self):
        for path in ('/','/find','/map','/new','/news','/best'):
            self.assertNotIn('name="analytics-token"',self.admin.get(path).text)
        token=self.token()
        self.assertEqual(self.event(token,client=self.admin).status_code,204)
        self.visitor.headers['User-Agent']='Googlebot/2.1'
        self.assertNotIn('name="analytics-token"',self.visitor.get('/').text)
        self.assertEqual(self.event(token).status_code,204)
        self.visitor.headers['User-Agent']='Mozilla/5.0'
        for path in ('/login','/signup','/map/register','/admin/login'):
            self.assertNotIn('name="analytics-token"',self.visitor.get(path).text)
        self.assertEqual(self.rows('SELECT * FROM analytics_events'),[])
        self.execute("UPDATE users SET role='admin' WHERE username='member'")
        self.assertNotIn('name="analytics-token"',self.member.get('/').text)

    def test_validation_csrf_payload_limit_replay_and_rate_limit(self):
        token=self.token()
        payload={'type':'page_view','id':str(uuid.uuid4())}
        for origin in ('https://evil.example','null',''):
            self.assertEqual(self.visitor.post('/api/analytics/events',headers={'Origin':origin,'X-Analytics-Token':token},json=payload).status_code,403)
        self.assertEqual(self.event(token,client=self.other).status_code,403)
        self.assertEqual(self.event(token+'invalid').status_code,400)
        self.assertEqual(self.event(token,'other').status_code,400)
        self.assertEqual(self.event(token,filters={'q':'x'*4500}).status_code,413)
        self.assertEqual(self.event(self.token(path='/map'),'find_search').status_code,400)
        self.event(token)
        self.execute('UPDATE analytics_sessions SET attempts=120')
        self.assertEqual(self.event(self.token()).status_code,429)

    def test_session_timeout_and_visitor_cookie_expiry(self):
        token=self.token();self.event(token)
        first=self.rows('SELECT * FROM analytics_events')[0]
        self.execute('UPDATE analytics_sessions SET last_seen=last_seen-1801')
        self.event(self.token())
        second=self.rows('SELECT * FROM analytics_events ORDER BY id DESC')[0]
        self.assertEqual(first['visitor_id'],second['visitor_id'])
        self.assertNotEqual(first['session_id'],second['session_id'])
        self.visitor.cookies.delete(VISITOR_COOKIE)
        self.event(self.token())
        self.assertEqual(self.stats()['visitors'],2)

    def test_kst_boundary_period_uniques_and_zero_division(self):
        now=int(datetime(2026,10,29,0,0,10,tzinfo=KST).timestamp())
        self.assertEqual(datetime.fromtimestamp(now,timezone.utc).date().isoformat(),'2026-10-28')
        for key,stamp,visitor in [('yesterday',now-11,'a'),('today',now,'a'),('old',now-5*86400,'b')]:
            self.execute('INSERT INTO analytics_events(event_key,event_type,visitor_id,session_id,path,feature,device_type,created_at) VALUES (?,?,?,?,?,?,?,?)',(key,'page_view',visitor,'s','/','home','desktop',stamp))
        with closing(sqlite3.connect(self.database)) as conn:
            stats=report(conn,{'period':'today'},now)
            self.assertEqual(stats['fixed'],{'today':1,'yesterday':1,'7d':2,'30d':2})
            self.assertEqual(stats['views'],1)
            self.assertEqual(stats['days'][0]['count'],1)
            self.assertEqual(report(conn,{'period':'custom','start':'2026-10-01','end':'2026-10-02'},now)['average'],0)
        for params in ({'period':'custom','start':'bad','end':'2026-10-29'},{'period':'unexpected'}):
            with self.assertRaises(ValueError): period(params,now)
        self.assertEqual(self.admin.get('/admin/analytics?period=custom&start=bad&end=bad').status_code,400)

    def test_device_test_events_isolated_and_retention(self):
        self.assertEqual(device_type('Mozilla/5.0 Chrome/123 Windows NT'),'desktop')
        for agent in ('iPhone Safari','Android Chrome','iPad Safari'):
            self.assertEqual(device_type(agent),'mobile')
        self.visitor.headers['User-Agent']='Mozilla Macintosh Safari'
        self.event(self.token(path='/?analytics_test=1'),touch_tablet=True)
        self.event(self.token(path='/map'))
        self.assertEqual(self.stats()['views'],0)
        self.assertEqual(self.stats(mode='test')['views'],2)
        self.assertEqual(self.rows('SELECT device_type FROM analytics_events ORDER BY id LIMIT 1')[0]['device_type'],'mobile')
        self.assertIn('검증 이벤트 전용',self.admin.get('/admin/analytics?mode=test').text)
        self.execute('UPDATE analytics_events SET created_at=0')
        self.execute("UPDATE analytics_meta SET value=0 WHERE key='last_cleanup'")
        self.event(self.token(path='/best'))
        self.assertFalse(self.rows('SELECT * FROM analytics_events WHERE created_at=0'))

    def test_migration_backup_idempotency_empty_dashboard(self):
        receipt=self.rows('SELECT * FROM schema_migrations WHERE version=?',(VERSION,))[0]
        self.assertTrue(Path(receipt['backup_path']).is_file())
        with closing(sqlite3.connect(receipt['backup_path'])) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='analytics_events'").fetchone())
        before={name:self.rows('SELECT * FROM '+name) for name in ('places','users','categories','images')}
        migrate(self.database)
        self.assertEqual(before,{name:self.rows('SELECT * FROM '+name) for name in before})
        page=self.admin.get('/admin/analytics')
        self.assertIn('선택한 기간에 FIND 검색 데이터가 없습니다.',page.text)
        self.assertIn('noindex',page.text)
        self.assertIn('통계 자세히 보기',self.admin.get('/admin').text)
