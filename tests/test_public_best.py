"""Public BEST counts/order, taxonomy names, semantic links and visibility."""
import re
import unittest
from html.parser import HTMLParser

from auth_security import hash_password
from test_best_editor import BestEditorTests


class Cards(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.card = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            if self.card is not None:
                raise AssertionError('Nested anchor inside collection card')
            if 'best-collection-card' in attrs.get('class', '').split():
                self.card = attrs
                self.links.append(attrs)

    def handle_endtag(self, tag):
        if tag == 'a': self.card = None


class PublicBestTests(unittest.TestCase):
    setUp = BestEditorTests.setUp
    tearDown = BestEditorTests.tearDown
    rows = BestEditorTests.rows
    execute = BestEditorTests.execute
    csrf = BestEditorTests.csrf
    post = BestEditorTests.post
    place = BestEditorTests.place

    @classmethod
    def setUpClass(cls): cls.password_hash = hash_password('community-test-secret')

    def attach(self, ids):
        for order, pid in enumerate(ids):
            self.execute('INSERT INTO best_collection_places VALUES (?,?,?)', (self.cid,pid,order*10))

    def test_public_counts_and_mixed_order_exclude_private_deleted_merged_examples(self):
        food1 = self.place('뒤 이름 맛집')
        cafe = self.place('첫 카페', self.cafe)
        food2 = self.place('앞 이름 맛집')
        trip = self.place('산책 장소', self.category['attraction'])
        hidden = self.place('비공개 비밀 장소', public=0)
        removed = self.place('삭제 비밀 장소')
        merged = self.place('병합 비밀 장소')
        example = self.place('예시 비밀 장소')
        self.execute("UPDATE places SET deleted_at='2026-01-01' WHERE id=?", (removed,))
        self.execute('UPDATE places SET merged_into_id=? WHERE id=?', (food1,merged))
        self.execute('UPDATE places SET is_example=1 WHERE id=?', (example,))
        self.attach([food1,cafe,hidden,food2,removed,merged,example,trip])
        before = self.rows('SELECT * FROM best_collection_places')
        page = self.visitor.get('/best/qa-best')
        self.assertEqual(page.status_code,200)
        self.assertEqual([p['id'] for p in page.context['places']], [food1,cafe,food2,trip])
        self.assertEqual({c['id']:c['count'] for c in page.context['best_categories']},
                         {self.food:2,self.cafe:1,self.category['attraction']:1})
        self.assertIn('전체 <span>4</span>',page.text)
        for name in ('비공개 비밀 장소','삭제 비밀 장소','병합 비밀 장소','예시 비밀 장소'):
            self.assertNotIn(name,page.text)
        self.assertEqual(self.rows('SELECT * FROM best_collection_places'),before)

    def test_active_db_names_zero_categories_and_existing_inactive_relations(self):
        self.attach([self.place('명칭 검증 카페',self.cafe)])
        self.execute("UPDATE categories SET name='커피와 쉼' WHERE id=?", (self.cafe,))
        page = self.visitor.get('/best/qa-best')
        self.assertEqual(page.context['best_categories'],[dict(id=self.cafe,name='커피와 쉼',count=1)])
        self.assertEqual(page.context['places'][0]['category'],'커피와 쉼')
        self.assertNotIn(f'data-best-filter="{self.food}"',page.text)
        self.execute('UPDATE categories SET is_active=0 WHERE id=?',(self.cafe,))
        page = self.visitor.get('/best/qa-best')
        self.assertEqual(page.context['best_categories'],[])
        self.assertEqual(len(page.context['places']),1)  # Never change existing public visibility.

    def test_main_semantic_card_and_private_collection_protection(self):
        self.execute("UPDATE best_collections SET image_url='/static/images/icons/icon-best.webp' WHERE id=?",(self.cid,))
        page = self.visitor.get('/best')
        cards = Cards(page.text)
        self.assertIn('/best/qa-best',[c['href'] for c in cards.links])
        self.assertTrue(all(c.get('aria-labelledby') for c in cards.links))
        self.execute('UPDATE best_collections SET published=0 WHERE id=?',(self.cid,))
        self.assertNotIn('/best/qa-best',self.visitor.get('/best').text)
        self.assertEqual(self.visitor.get('/best/qa-best').status_code,404)

    def test_empty_seo_share_and_link_destinations(self):
        empty = self.visitor.get('/best/qa-best')
        self.assertIn('전체 <span>0</span>',empty.text)
        self.assertIn('이 모음에 공개된 장소를 준비 중입니다.',empty.text)
        pid=self.place('연결 검증',terms=self.subtypes)
        self.attach([pid])
        page=self.visitor.get('/best/qa-best?category=cafe')
        self.assertIn('<title>격리 BEST | JEJU_KNOW</title>',page.text)
        self.assertIn('name="description" content="소개 보존"',page.text)
        self.assertIn('rel="canonical" href="https://www.jeju-know.com/best/qa-best"',page.text)
        self.assertIn('property="og:url" content="https://www.jeju-know.com/best/qa-best"',page.text)
        self.assertIn('data-url="https://www.jeju-know.com/best/qa-best"',page.text)
        self.assertIn('data-share>',page.text); self.assertIn('data-copy-link>',page.text)
        slug=page.context['places'][0]['slug']
        self.assertIn(f'href="/place/{slug}"',page.text)
        self.assertIn(f'href="/map?place={slug}"',page.text)
        self.assertIn('href="/best">← 전체 BEST</a>',page.text)
        self.assertEqual(len(re.findall('class="tag"',page.text)),3)

    def test_public_requests_preserve_data_schema_and_shared_pages(self):
        self.attach([self.place('연결 유지 카페',self.cafe)])
        tables=['places','categories','place_category_links','best_collections','best_collection_places','schema_migrations']
        before={table:self.rows('SELECT * FROM '+table) for table in tables}
        schema=self.rows('SELECT name,sql FROM sqlite_master ORDER BY name')
        for path in ('/','/find','/new','/news','/map','/best','/best/qa-best','/login','/signup',f'/admin/best/{self.cid}/edit'):
            client=self.admin if path.startswith('/admin') else self.visitor
            page=client.get(path)
            self.assertEqual(page.status_code,200,path)
            if path not in ('/best','/best/qa-best'):
                self.assertNotIn('/static/best.css',page.text)
        for table in tables: self.assertEqual(self.rows('SELECT * FROM '+table),before[table],table)
        self.assertEqual(self.rows('SELECT name,sql FROM sqlite_master ORDER BY name'),schema)
