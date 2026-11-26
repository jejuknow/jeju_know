"""EVENT extension: existing identities, publication rules and nullable facts."""
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import test_community as fixtures
from community_migrations import today
from event_details import FIELDS, VERSION, migrate, parse_fields
from issue_discovery import LABELS, PUBLIC_CATEGORIES, browse, sections
from test_images import picture


class EventMigrationTest(unittest.TestCase):
    def test_backup_preserves_all_old_columns_and_idempotent_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            database=Path(folder)/'jejuno.db'
            with closing(sqlite3.connect(database)) as conn, conn:
                conn.executescript("""CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,backup_path TEXT);
                  CREATE TABLE news_posts(id INTEGER PRIMARY KEY,slug TEXT,category TEXT,event_end TEXT,
                    published INTEGER,deleted_at TEXT,approval_status TEXT,author_id INTEGER);
                  INSERT INTO news_posts VALUES (17,'legacy','지역소식','2026-12-31',1,NULL,'approved',3),
                    (19,'pending','체험•교육','2026-12-31',0,NULL,'pending',5);""")
                before=conn.execute('SELECT * FROM news_posts ORDER BY id').fetchall()
            migrate(database);migrate(database)
            with closing(sqlite3.connect(database)) as conn:
                self.assertEqual([r[:8] for r in conn.execute('SELECT * FROM news_posts ORDER BY id')],before)
                self.assertTrue(all(v is None for r in conn.execute('SELECT * FROM news_posts') for v in r[8:]))
                receipts=conn.execute('SELECT backup_path FROM schema_migrations WHERE version=?',(VERSION,)).fetchall()
                self.assertEqual(len(receipts),1)
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertTrue(Path(receipts[0][0]).name.startswith('jejuno-before-event-expansion-'))
            with closing(sqlite3.connect(receipts[0][0])) as backup:
                self.assertEqual(backup.execute('SELECT * FROM news_posts ORDER BY id').fetchall(),before)
                self.assertNotIn('fee_status',[r[1] for r in backup.execute('PRAGMA table_info(news_posts)')])

    def test_omitted_fields_retain_zero_and_explicit_empty_clears(self):
        self.assertEqual(parse_fields({}, {'benefit_price':0})['benefit_price'],'0')
        self.assertIsNone(parse_fields({'benefit_price':''},{'benefit_price':100})['benefit_price'])


class EventFlowTest(unittest.TestCase):
    setUp=fixtures.CommunityTest.setUp
    tearDown=fixtures.CommunityTest.tearDown
    setUpClass=classmethod(fixtures.CommunityTest.setUpClass.__func__)
    csrf=fixtures.CommunityTest.csrf
    rows=fixtures.CommunityTest.rows
    execute=fixtures.CommunityTest.execute
    post=fixtures.CommunityTest.post

    def data(self,**changes):
        day=today().isoformat()
        return {'title':'제주 공연 점검','summary':'일정과 혜택을 소개합니다.','category':'공연',
                'event_start':day,'event_end':day,'time_mode':'all_day','region':'애월','venue':'제주 공연장',
                'address':'제주시 애월읍','latitude':'33.45','longitude':'126.4','source_name':'주최 측',**changes}

    def save(self,**changes):
        response=self.post(self.admin,'/admin/news/new',**self.data(published='1',show_on_map='1',**changes))
        self.assertEqual(response.status_code,303,response.text[:500])
        return self.rows('SELECT * FROM news_posts ORDER BY id DESC')[0]

    def test_five_categories_navigation_and_legacy_preservation(self):
        self.assertEqual(list(PUBLIC_CATEGORIES),['events','exhibitions','performance','activities','benefit-discount'])
        self.assertNotIn('문화생활',LABELS.values())
        for client,path in ((self.member,'/issues/new'),(self.admin,'/admin/news/new')):
            page=client.get(path)
            select=re.search(r'<select name="category".*?</select>',page.text,re.S)[0]
            self.assertEqual(select.count('<option'),5)
            self.assertNotIn('지역소식',select)
        row=self.save()
        self.execute("UPDATE news_posts SET category='지역소식' WHERE id=?",(row['id'],))
        before=self.rows('SELECT * FROM news_posts')[0]
        for path in ('/','/news','/news/'+row['slug']):
            self.assertEqual(self.visitor.get(path).status_code,200)
        edit=f'/admin/news/{row["id"]}/edit'
        self.assertIn('지역소식 (기존 분류)',self.admin.get(edit).text)
        self.assertIn('기존 지역소식 1건',self.admin.get('/admin/issues').text)
        self.assertEqual(self.rows('SELECT * FROM news_posts')[0],before)
        self.assertEqual(self.post(self.admin,edit,**self.data(category='지역소식',slug=row['slug'],published='1')).status_code,303)
        self.assertEqual(self.rows('SELECT category FROM news_posts')[0]['category'],'지역소식')
        self.assertEqual(self.post(self.admin,'/admin/news/new',**self.data(category='지역소식')).status_code,400)
        for path in ('/','/find','/new','/news','/map','/best','/login','/signup'):
            page=self.visitor.get(path)
            self.assertEqual(page.status_code,200,path)
            nav=re.search(r'<nav class="primary-nav".*?</nav>',page.text,re.S)[0]
            self.assertIn('>EVENT</a>',nav)
            self.assertNotIn('>ISSUE</a>',nav)

    def test_benefit_fields_prices_validation_and_disclosure(self):
        row=self.save(category=LABELS['benefit-discount'],title='도민 혜택',benefit_summary='입장료 30% 할인',
                      benefit_audience='제주도민',benefit_conditions='신분증 제시',regular_price='10000',
                      benefit_price='0',promo_code='<script>x</script>',benefit_howto='현장에서 신청',
                      benefit_quantity='선착순 50명',is_sponsored='1',is_promoted='1')
        self.assertEqual((row['regular_price'],row['benefit_price'],row['fee_status']),(10000,0,None))
        listing=self.visitor.get('/news?category=benefit-discount')
        self.assertEqual([x['id'] for x in listing.context['news_items']],[row['id']])
        self.assertIn('혜택 확인',listing.text)
        for html in (listing.text,self.visitor.get('/news/'+row['slug']).text,self.visitor.get('/').text):
            self.assertIn('광고·협찬',html)
        html=self.visitor.get('/news/'+row['slug']).text
        for text in ('10,000원','0원','제주도민','현장에서 신청','선착순 50명','&lt;script&gt;'):self.assertIn(text,html)
        self.assertNotIn('<script>x</script>',html)
        self.assertNotIn('"@type": "Event"',html)
        # 0 price is a fact, not proof of free admission.
        self.assertEqual(self.visitor.get('/news?free=1').context['news_items'],[])
        edit=f'/admin/news/{row["id"]}/edit'
        response=self.post(self.admin,edit,**self.data(category=LABELS['benefit-discount'],slug=row['slug'],published='1'))
        self.assertEqual(response.status_code,303)
        self.assertEqual(self.rows('SELECT benefit_price FROM news_posts')[0]['benefit_price'],0)
        self.assertEqual(self.post(self.admin,edit,**self.data(category=LABELS['benefit-discount'],benefit_price='',regular_price='')).status_code,303)
        saved=self.rows('SELECT * FROM news_posts')[0]
        self.assertIsNone(saved['regular_price']);self.assertIsNone(saved['benefit_price'])
        for values in ({'source_name':''},{'summary':''},{'event_start':''},{'benefit_price':'-1'},
                       {'regular_price':'1.5'},{'benefit_price':'1000000001'},{'fee_status':'unknown'},
                       {'external_url':'javascript:alert(1)'},{'promo_code':'x'*81}):
            self.assertEqual(self.post(self.admin,'/admin/news/new',**self.data(category=LABELS['benefit-discount'],**values)).status_code,400,values)

    def test_period_category_region_free_closing_and_sections(self):
        day=date(2026,11,26)
        def insert(slug,category,a,b,fee=None,public=1,approval='approved',coords=True):
            self.execute('''INSERT INTO news_posts(slug,title,category,event_start,event_end,region,published,approval_status,
              fee_status,show_on_map,latitude,longitude) VALUES (?,?,?,?,?,'애월',?,?,?,1,?,?)''',
              (slug,slug,category,(day+timedelta(days=a)).isoformat(),(day+timedelta(days=b)).isoformat(),public,approval,fee,
               33.45 if coords else None,126.4 if coords else None))
        insert('today-free','공연',-1,0,'free')
        insert('weekend-free','공연',2,3,'free')
        insert('paid','공연',-1,10,'paid')
        insert('unknown','공연',0,1)
        insert('benefit',LABELS['benefit-discount'],-1,2)
        insert('future-benefit',LABELS['benefit-discount'],2,4)
        insert('legacy','지역소식',0,4,coords=False)
        insert('ended','공연',-2,-1,'free')
        insert('private','공연',0,1,'free',public=0)
        insert('pending','공연',0,1,'free',approval='pending')
        with patch('app.issue_now',return_value=day):
            params={'category':'performance','period':'weekend','region':'애월','q':'free','free':'1','closing':'1'}
            page=self.visitor.get('/news',params=params)
            self.assertEqual([x['slug'] for x in page.context['news_items']],['weekend-free'])
            self.assertEqual(page.context['map_count'],1)
            link=page.context['issue_link'](period='all')
            for part in ('free=1','closing=1','q=free','category='):self.assertIn(part,link)
            self.assertEqual(self.visitor.get('/news?free=1').context['map_count'],2)
            self.assertEqual([x['slug'] for x in self.visitor.get('/news?closing=1').context['news_items']],
                             ['today-free','unknown','benefit','weekend-free'])
            all_page=self.visitor.get('/news')
            self.assertEqual([x['slug'] for x in all_page.context['ending_items']],
                             ['today-free','unknown','benefit','weekend-free'])
            self.assertIn('legacy',[x['slug'] for x in all_page.context['news_items']])
            for slug in ('ended','private','pending'):self.assertEqual(self.visitor.get('/news/'+slug).status_code,404)
            self.assertEqual(self.visitor.get('/news?period=weekend&category=activities').context['news_items'],[])
            self.assertIn('기간 전체 보기',self.visitor.get('/news?period=weekend&category=activities').text)
        self.assertEqual(len(self.rows('SELECT * FROM news_posts')),10)

    def test_member_benefit_stays_pending_until_approval_and_performance_seo(self):
        data=self.data(category=LABELS['benefit-discount'],benefit_summary='도민 할인',fee_status='free',
                       is_sponsored='1',published='1',approval_status='approved')
        result=self.member.post('/issues/new',data={**data,'csrf_token':self.csrf(self.member)},
             files={'image_0':('poster.png',picture(),'image/png')},follow_redirects=False)
        self.assertEqual(result.status_code,303)
        row=self.rows('SELECT * FROM news_posts')[0]
        self.assertEqual((row['published'],row['approval_status'],row['is_sponsored']),(0,'pending',0))
        self.assertEqual(row['source_name'],'주최 측')
        url='/news/'+row['slug']
        self.assertEqual(self.visitor.get(url).status_code,404)
        self.assertEqual(self.other.get(f'/issues/{row["id"]}/edit').status_code,403)
        response=self.post(self.admin,f'/admin/news/{row["id"]}/edit',**{**data,'slug':row['slug'],
             'review_version':row['review_version'],'review_action':'approve','show_on_map':'1'})
        self.assertEqual(response.status_code,303,response.text[:500])
        self.assertEqual(self.visitor.get(url).status_code,200)
        performance=self.save(title='저녁 공연',time_mode='specified',start_time='23:00',end_time='23:30',fee_status='paid')
        html=self.visitor.get('/news/'+performance['slug']).text
        self.assertIn('"@type": "Event"',html)
        self.assertIn('"isAccessibleForFree": false',html)
        self.assertIn('23:00 ~ 23:30',html)
        self.assertIn('eventDetailMap',html)
        for slug in (row['slug'],performance['slug']):self.assertIn('/news/'+slug,self.visitor.get('/sitemap.xml').text)
        self.assertIn('rel="canonical" href="https://www.jeju-know.com/news"',self.visitor.get('/news?free=1').text)
