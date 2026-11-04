"""SEO integration checks against an isolated DB, including raw HTML before JavaScript."""
import json
import re
import unittest
from html import unescape
from unittest.mock import patch
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import test_community
from auth_security import hash_password
from public_seo import ORIGIN, PAGES, issue_metadata


class SeoTest(unittest.TestCase):
    setUp = test_community.CommunityTest.setUp
    tearDown = test_community.CommunityTest.tearDown
    rows = test_community.CommunityTest.rows
    execute = test_community.CommunityTest.execute
    csrf = test_community.CommunityTest.csrf
    post = test_community.CommunityTest.post
    form = test_community.CommunityTest.form
    create = test_community.CommunityTest.create
    place = test_community.CommunityTest.place

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('community-test-secret')

    def test_public_metadata_and_raw_html(self):
        places = [self.place(name='SEO 장소 ' + category, category_id=self.category[category])
                  for category in ('cafe', 'food', 'attraction')]
        self.execute("UPDATE places SET one_line=name || '의 실제 소개', new_override='show'")
        titles, descriptions = set(), set()
        for path in ('/', '/find', '/map', '/new', '/news', '/best'):
            response = self.visitor.get(path)
            self.assertEqual(response.status_code, 200, path)
            html = response.text
            title = unescape(re.search(r'<title>(.*?)</title>', html, re.S)[1])
            description = unescape(re.search(r'<meta name="description" content="([^"]*)"', html)[1])
            self.assertEqual((title, description), PAGES[path])
            self.assertNotIn(title, titles)
            self.assertNotIn(description, descriptions)
            titles.add(title); descriptions.add(description)
            self.assertEqual(len(re.findall(r'<h1\b', html)), 1, path)
            self.assertIn(f'rel="canonical" href="{ORIGIN}{path}"', html)
            self.assertIn('name="robots" content="index,follow"', html)
            if path in ('/', '/find', '/map', '/new'):
                self.assertIn('/place/' + places[0]['slug'], html)
                self.assertIn('SEO 장소 cafe', html)
        for p in places:
            html = self.visitor.get('/place/' + p['slug']).text
            self.assertIn(p['name'] + ' - 제주', html)
            self.assertIn(p['name'] + '의 실제 소개', html)
            schemas = [json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
            self.assertEqual(schemas[1]['@type'], 'BreadcrumbList')
            self.assertEqual(schemas[1]['itemListElement'][-1]['item'], ORIGIN + '/place/' + p['slug'])
            self.assertNotIn('aggregateRating', schemas[0])
            self.assertNotIn('priceRange', schemas[0])
        home = self.visitor.get('/').text
        site = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', home, re.S)[1])
        self.assertEqual(site['publisher']['logo'], ORIGIN + '/static/images/brand/jeju_know-logo.png')
        self.assertEqual(site['publisher']['sameAs'], ['https://www.instagram.com/jeju_know/'])

    def test_filtered_ssr_matches_existing_api_and_private_policy(self):
        visible = self.place(name='조천 공개 카페', region='조천')
        hidden = self.place(name='비공개 정보', region='조천')
        self.execute('UPDATE places SET published=0 WHERE id=?', (hidden['id'],))
        other = self.place(name='애월 공개 카페', region='애월')
        for path in ('/find', '/map', '/places'):
            response = self.visitor.get(path, params={'category': 'cafe', 'region': '조천'})
            self.assertIn('/place/' + visible['slug'], response.text)
            for excluded in (hidden, other):
                self.assertNotIn('/place/' + excluded['slug'], response.text)
            self.assertEqual(response.headers['x-robots-tag'], 'noindex,follow')
            self.assertIn('name="robots" content="noindex,follow"', response.text)
            self.assertIn(f'rel="canonical" href="{ORIGIN}{path}"', response.text)
        self.assertEqual([p['id'] for p in self.visitor.get('/api/places?category=cafe&region=조천').json()], [visible['id']])
        for path in ('/login', '/signup', '/account', '/admin', '/map/register', '/issues/new', '/contact',
                     '/admin/analytics', '/admin/places/import', '/find?preview=1', '/place/' + visible['slug'] + '?preview=1'):
            response = self.visitor.get(path, follow_redirects=False)
            self.assertIn('noindex', response.headers['x-robots-tag'], path)
        self.assertEqual(self.visitor.get('/place/' + hidden['slug']).status_code, 404)
        self.assertEqual(self.visitor.get('/news/does-not-exist').status_code, 404)
        self.assertEqual(self.visitor.get('/best/does-not-exist').status_code, 404)

    def test_verification_is_optional_and_escaped(self):
        with patch.dict('os.environ', {'GOOGLE_SITE_VERIFICATION': '', 'NAVER_SITE_VERIFICATION': ' '}):
            html = self.visitor.get('/').text
            self.assertNotIn('name="google-site-verification"', html)
            self.assertNotIn('name="naver-site-verification"', html)
        with patch.dict('os.environ', {'GOOGLE_SITE_VERIFICATION': 'test-google', 'NAVER_SITE_VERIFICATION': '"/><script>bad()</script>'}):
            html = self.visitor.get('/').text
            self.assertIn('name="google-site-verification" content="test-google"', html)
            self.assertIn('name="naver-site-verification" content="&#34;', html)
            self.assertNotIn('<script>bad()</script>', html)

    def test_approved_event_in_html_and_sitemap(self):
        title = '제주 행사 </script><script>alert(1)</script>'
        self.execute("""INSERT INTO news_posts(slug,title,category,summary,published,approval_status,
                     event_start,event_end,start_time,end_time,venue,address,image_url)
                     VALUES ('seo-event',?,'전시•팝업','실제 전시 소개',1,'approved',
                     '2099-01-01','2099-01-02','10:00','18:00','제주 전시장','제주특별자치도 제주시 전시로 1',
                     'https://www.jeju-know.com/static/images/brand/default-og.png')""", (title,))
        html = self.visitor.get('/news/seo-event').text
        schemas = [json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
        self.assertEqual(schemas[0]['@type'], 'Event')
        self.assertEqual(schemas[0]['name'], title)
        self.assertEqual(schemas[1]['@type'], 'BreadcrumbList')
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('/news/seo-event', self.visitor.get('/sitemap.xml').text)
        self.execute("UPDATE news_posts SET approval_status='rejected' WHERE slug='seo-event'")
        self.assertEqual(self.visitor.get('/news/seo-event').status_code, 404)
        self.assertNotIn('/news/seo-event', self.visitor.get('/sitemap.xml').text)

    def test_sitemap_only_live_content_and_stored_lastmod(self):
        public = self.place(name='사이트맵 공개')
        example = self.place(name='사이트맵 예시')
        self.execute("UPDATE places SET is_example=1 WHERE id=?", (example['id'],))
        self.execute("UPDATE places SET updated_at='2026-10-01 12:30:00' WHERE id=?", (public['id'],))
        self.execute("INSERT INTO best_collections(slug,title,published,updated_at) VALUES ('seo-best','추천 모음',1,'invalid')")
        self.execute("INSERT INTO news_posts(slug,title,category,published,approval_status,event_start,event_end) VALUES ('seo-pending','대기 이슈','행사•축제',1,'pending','2099-01-01','2099-01-02')")
        response = self.visitor.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        self.assertIn('application/xml', response.headers['content-type'])
        root = ET.fromstring(response.content)
        ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        entries = {node.find('s:loc', ns).text: node.findtext('s:lastmod', namespaces=ns) for node in root}
        self.assertEqual(entries[ORIGIN + '/place/' + public['slug']], '2026-10-01')
        self.assertIsNone(entries[ORIGIN + '/best/seo-best'])
        self.assertNotIn('/place/' + example['slug'], response.text)
        self.assertNotIn('seo-pending', response.text)
        self.assertIn('content="noindex,follow"', self.visitor.get('/place/' + example['slug']).text)
        for url in entries:
            self.assertTrue(url.startswith(ORIGIN + '/'))
            self.assertFalse(urlsplit(url).query)
            self.assertEqual(self.visitor.get(urlsplit(url).path).status_code, 200, url)
        robots = self.visitor.get('/robots.txt')
        self.assertIn('text/plain', robots.headers['content-type'])
        self.assertIn('Sitemap: ' + ORIGIN + '/sitemap.xml', robots.text)
        for public_path in ('/static', '/uploads', '/find', '/map', '/login'):
            self.assertNotIn('Disallow: ' + public_path, robots.text)

    def test_only_known_apex_host_redirects(self):
        response = self.visitor.get('/find?category=cafe', headers={'host': 'jeju-know.com'}, follow_redirects=False)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers['location'], ORIGIN + '/find?category=cafe')
        response = self.visitor.get('/find', headers={'host': 'testserver', 'x-forwarded-host': 'evil.example'}, follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn('rel="canonical" href="' + ORIGIN + '/find"', response.text)


class EventSchemaTest(unittest.TestCase):
    def test_only_actual_event_facts_are_used(self):
        item = {'slug': 'real-event', 'title': '실제 행사 </script>', 'category': '행사•축제',
                'summary': '등록된 행사 소개', 'event_start': '2099-01-01', 'event_end': '2099-01-02',
                'venue': '행사장', 'address': '제주특별자치도 제주시 실제로 1',
                'start_time': '10:30', 'end_time': '18:00', 'image_url': '/uploads/issues/actual.webp'}
        data = issue_metadata(item)['json_ld']
        self.assertEqual(data['@type'], 'Event')
        self.assertEqual(data['startDate'], '2099-01-01T10:30:00+09:00')
        self.assertEqual(data['location']['name'], item['venue'])
        self.assertEqual(data['image'], ORIGIN + item['image_url'])
        for key in ('organizer', 'offers', 'review', 'aggregateRating', 'performer'):
            self.assertNotIn(key, data)
        self.assertEqual(issue_metadata({**item, 'start_time': ''})['json_ld']['startDate'], '2099-01-01')
        for change in ({'category': '지역소식'}, {'category': '공모•지원'}, {'venue': ''}, {'address': ''},
                       {'event_start': '2099-02-30'}, {'event_end': '2000-01-01'},
                       {'event_end': '2099-01-01', 'end_time': '09:00'}):
            self.assertIsNone(issue_metadata({**item, **change})['json_ld'], change)

