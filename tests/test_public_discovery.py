import json
import re
import unittest
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

import test_community
from auth_security import hash_password
from public_seo import opening_hours


class PublicDiscoveryTest(unittest.TestCase):
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

    def schema(self, html):
        return json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',html,re.S)[1])

    def test_public_metadata_facts_and_private_boundaries(self):
        for category,kind in [('cafe','CafeOrCoffeeShop'),('food','Restaurant')]:
            p=self.place(category_id=self.category[category])
            self.execute("UPDATE places SET business_hours='09:30 ~ 16:00',closed_days='연중무휴',signature_menu='필터커피',representative_price=7000,note='실제 꿀팁',latitude=33.4,longitude=126.5 WHERE id=?",(p['id'],))
            url='/place/'+p['slug']
            page=self.visitor.get(url+'?tracking=1')
            self.assertEqual(page.status_code,200)
            self.assertIn(f'rel="canonical" href="https://www.jeju-know.com{url}"',page.text)
            self.assertIn('7,000원',page.text)
            self.assertIn('data-copy-link',page.text)
            self.assertIn('/map?place=',page.text)
            data=self.schema(page.text)
            self.assertEqual(data['@type'],kind)
            self.assertEqual(data['openingHoursSpecification'][0]['opens'],'09:30')
            self.assertEqual(data['geo']['latitude'],33.4)
            self.assertNotIn('aggregateRating',data)
            self.assertNotIn('review',data)
            self.assertIn('실제 꿀팁',page.text)
            self.execute('UPDATE places SET published=0 WHERE id=?',(p['id'],))
            hidden=self.visitor.get(url)
            self.assertEqual(hidden.status_code,404)
            self.assertIn('noindex',hidden.headers['x-robots-tag'])
            self.assertNotIn(p['slug'],self.visitor.get('/sitemap.xml').text)

    def test_missing_facts_and_json_injection(self):
        p=self.place(name='공개 </script><script>alert(1)</script>')
        page=self.visitor.get('/place/'+p['slug'])
        data=self.schema(page.text)
        self.assertEqual(data['name'],p['name'])
        self.assertNotIn('<script>alert(1)</script>',page.text)
        self.assertNotIn('openingHoursSpecification',data)
        self.assertNotIn('geo',data)
        self.assertIn('영업시간 미확인',page.text)
        description=re.search('<meta name="description" content="([^"]+)"',page.text)[1]
        self.assertNotIn('꿀팁',description)
        self.assertNotIn('대표메뉴',description)
        self.assertEqual(self.visitor.get('/place/no-such-place').status_code,404)

    def test_sitemap_best_issue_and_index_policy(self):
        p=self.place()
        self.execute("INSERT INTO best_collections(slug,title,description,published) VALUES ('public-best','공개 BEST','실제 모음 설명',1)")
        self.execute("INSERT INTO best_collections(slug,title,published) VALUES ('hidden-best','비공개 BEST',0)")
        self.execute("INSERT INTO news_posts(slug,title,category,summary,published) VALUES ('public-issue','공개 이슈','지역소식','실제 소식',1)")
        self.execute("INSERT INTO news_posts(slug,title,category,published) VALUES ('hidden-issue','비공개 이슈','지역소식',0)")
        page=self.visitor.get('/best/public-best')
        self.assertIn('data-share-content',page.text)
        self.assertIn('og:url" content="https://www.jeju-know.com/best/public-best',page.text)
        self.assertEqual(self.visitor.get('/best/hidden-best').status_code,404)
        xml=self.visitor.get('/sitemap.xml')
        ET.fromstring(xml.content)
        self.assertIn('/place/'+p['slug'],xml.text)
        self.assertIn('/best/public-best',xml.text)
        self.assertNotIn('hidden-best',xml.text)
        self.assertIn('/news/public-issue',xml.text)
        self.assertNotIn('hidden-issue',xml.text)
        self.assertEqual(self.visitor.get('/news/hidden-issue').status_code,404)
        self.assertIn('og:title" content="공개 이슈 | JEJU_KNOW',self.visitor.get('/news/public-issue').text)
        for path in ('/admin','/login','/signup','/account','/map/register',f"/places/{p['id']}/suggest"):
            response=self.visitor.get(path,follow_redirects=False)
            self.assertIn('noindex',response.headers['x-robots-tag'])
        for path in ('/find','/map','/places'):
            html=self.visitor.get(path+'?region=조천&category=cafe').text
            self.assertIn(f'rel="canonical" href="https://www.jeju-know.com{path}"',html)
            self.assertNotIn('noindex',html)
        self.assertIn('https://www.jeju-know.com/sitemap.xml',self.visitor.get('/robots.txt').text)

    def test_relaxation_counts_reuse_exact_search_and_keep_original_empty(self):
        p=self.place(region='조천')
        self.execute("UPDATE places SET companions='[\"혼자\"]' WHERE id=?",(p['id'],))
        bakery=self.rows("SELECT id,slug FROM categories WHERE name='베이커리' AND type='cafe_feature'")[0]
        self.execute('INSERT INTO place_category_links VALUES (?,?)',(p['id'],bakery['id']))
        query={'category':'cafe','region':'조천','detail':bakery['slug'],'companion':'부모님'}
        self.assertEqual(self.visitor.get('/api/places',params=query).json(),[])
        options=self.visitor.get('/api/place-search/suggestions',params=query).json()
        self.assertTrue(any(o['label']=='부모님' and o['count']==1 for o in options))
        for option in options:
            self.assertEqual(option['count'],len(self.visitor.get('/api/places',params=option['filters']).json()))
        self.assertEqual(self.visitor.get('/api/places',params=query).json(),[])
        wider = {**query, 'companion': ''}
        # An unchanged count is not presented as "more places".
        self.assertEqual(self.visitor.get('/api/place-search/suggestions',params=wider).json(),[])
        self.execute('UPDATE places SET published=0 WHERE id=?',(p['id'],))
        self.assertEqual(self.visitor.get('/api/place-search/suggestions',params=query).json(),[])

    def test_hours_only_from_parseable_stored_facts(self):
        self.assertIsNone(opening_hours({'business_hours':'별도 문의'}))
        self.assertIsNone(opening_hours({'business_hours':'29:00 ~ 30:00'}))
        hours=opening_hours({'business_hours':'09:30 ~ 18:00','closed_days':'화요일 · 수요일'})
        self.assertNotIn('Tuesday',hours['dayOfWeek'])
        self.assertEqual(hours['opens'],'09:30')
