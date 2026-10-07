import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing

from fastapi.testclient import TestClient
from app import create_app


class JejunoAppTest(unittest.TestCase):
    news_categories = ["행사•축제", "전시•팝업", "체험•교육", "지역소식", "공모•지원"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        os.environ["JEJUNO_SKIP_SEED"] = "1"
        self.app = create_app({"TESTING": True, "DATABASE": self.db_path, "SECRET_KEY": "test-secret"})
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        os.environ.pop("JEJUNO_SKIP_SEED", None)
        self.tmp.cleanup()

    def get_csrf_from_cookie_session(self, path):
        self.client.get(path)
        # Instead of decoding signed session cookie, read token from rendered HTML.
        html = self.client.get(path).text
        marker = 'name="csrf_token" value="'
        start = html.find(marker)
        self.assertNotEqual(start, -1)
        start += len(marker)
        end = html.find('"', start)
        return html[start:end]

    def setup_admin_and_login(self):
        token = self.get_csrf_from_cookie_session("/admin/setup")
        res = self.client.post("/admin/setup", data={
            "csrf_token": token,
            "username": "admin",
            "password": "password123",
            "password_confirm": "password123",
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        token = self.get_csrf_from_cookie_session("/admin/login")
        res = self.client.post("/admin/login", data={
            "csrf_token": token,
            "username": "admin",
            "password": "password123",
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn("장소 관리", res.text)

    def test_full_admin_crud_and_publish_flow(self):
        self.setup_admin_and_login()
        token = self.get_csrf_from_cookie_session("/admin/places/new")
        res = self.client.post("/admin/places/new", data={
            "csrf_token": token,
            "name": "테스트 카페",
            "slug": "test-cafe",
            "region": "애월",
            "category": "카페",
            "emoji": "☕",
            "companions": ["혼자", "친구"],
            "features": ["감성", "커피", "오션뷰", "기존 사용자 태그"],
            "parking_status": "가능",
            "pet_status": "미확인",
            "child_status": "미확인",
            "accessible_status": "미확인",
            "one_line": "테스트 한줄 추천",
            "reason": "테스트 추천 이유",
            "note": "테스트 참고",
            "published": "1",
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        api = self.client.get("/api/places").json()
        self.assertEqual(len(api), 1)
        self.assertEqual(api[0]["name"], "테스트 카페")
        self.assertIn("주차", api[0]["features_public"])
        self.assertEqual(api[0]["features"], ["감성", "커피", "오션뷰", "기존 사용자 태그"])

        conn = sqlite3.connect(self.db_path)
        place_id = conn.execute("SELECT id FROM places WHERE slug='test-cafe'").fetchone()[0]
        conn.close()

        token = self.get_csrf_from_cookie_session(f"/admin/places/{place_id}/edit")
        edit_html = self.client.get(f"/admin/places/{place_id}/edit").text
        self.assertIn('value="커피" checked', edit_html)
        self.assertIn('value="기존 사용자 태그" checked', edit_html)
        self.assertIn('/static/admin-place-filters.js', edit_html)
        res = self.client.post(f"/admin/places/{place_id}/edit", data={
            "csrf_token": token,
            "name": "수정 카페",
            "slug": "test-cafe",
            "region": "한림",
            "category": "카페",
            "emoji": "🫘",
            "companions": ["혼자"],
            "features": ["힐링"],
            "parking_status": "미확인",
            "pet_status": "미확인",
            "child_status": "미확인",
            "accessible_status": "미확인",
            "one_line": "수정된 문구",
            "reason": "수정된 이유",
            "published": "1",
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        api = self.client.get("/api/places").json()
        self.assertEqual(api[0]["name"], "수정 카페")

        token = self.get_csrf_from_cookie_session("/admin")
        self.client.post(f"/admin/places/{place_id}/toggle-published", data={"csrf_token": token}, follow_redirects=True)
        self.assertEqual(self.client.get("/api/places").json(), [])

        token = self.get_csrf_from_cookie_session("/admin")
        self.client.post(f"/admin/places/{place_id}/toggle-published", data={"csrf_token": token}, follow_redirects=True)
        self.assertEqual(len(self.client.get("/api/places").json()), 1)

        token = self.get_csrf_from_cookie_session("/admin")
        self.client.post(f"/admin/places/{place_id}/delete", data={"csrf_token": token}, follow_redirects=True)
        self.assertEqual(self.client.get("/api/places").json(), [])

    def test_news_crud_and_public_pages(self):
        self.setup_admin_and_login()
        token = self.get_csrf_from_cookie_session("/admin/news/new")
        res = self.client.post("/admin/news/new", data={
            "csrf_token": token,
            "title": "테스트 제주 행사",
            "slug": "test-event",
            "category": "행사•축제",
            "summary": "테스트 행사 한줄 요약",
            "body": "행사 상세 내용",
            "venue": "제주시 테스트 장소",
            "event_start": "2026-10-10",
            "event_end": "2026-10-11",
            "source_name": "공식 주최기관",
            "checked_at": "2026-10-05",
            "external_url": "https://example.com/event",
            "is_featured": "1",
            "is_sponsored": "1",
            "sponsor_name": "테스트 협업처",
            "published": "1",
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn("테스트 제주 행사", res.text)

        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("테스트 제주 행사", home.text)

        news = self.client.get("/news")
        self.assertEqual(news.status_code, 200)
        self.assertIn("광고·협찬", news.text)

        detail = self.client.get("/news/test-event")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("공식 주최기관", detail.text)

        conn = sqlite3.connect(self.db_path)
        news_id = conn.execute("SELECT id FROM news_posts WHERE slug='test-event'").fetchone()[0]
        conn.close()

        token = self.get_csrf_from_cookie_session("/admin")
        self.client.post(f"/admin/news/{news_id}/toggle-published", data={"csrf_token": token}, follow_redirects=True)
        self.assertEqual(self.client.get("/news/test-event").status_code, 404)

    def insert_news(self, slug, category, published=1):
        with closing(sqlite3.connect(self.db_path)) as conn:
            news_id = conn.execute(
                "INSERT INTO news_posts (slug,title,category,body,published) VALUES (?,?,?,?,?)",
                (slug, slug, category, "기존 내용 보존", published),
            ).lastrowid
            conn.commit()
            return news_id

    def stored_news(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute("SELECT * FROM news_posts ORDER BY id").fetchall()

    def category_options(self, html):
        select = re.search(r'<select name="category" required>(.*?)</select>', html, re.S).group(1)
        return [value for value in re.findall(r'<option value="([^"]*)"', select) if value]

    def test_news_categories_and_home_issue_title(self):
        self.setup_admin_and_login()
        home = self.client.get("/")
        self.assertIn('<h2>제주 이슈</h2>', home.text)
        self.assertNotIn("이번 주 제주에서 뭐 하지?", home.text)
        self.assertIn('id="finder"', home.text)
        self.assertIn('id="pick"', home.text)
        self.assertIn('id="collections"', home.text)
        for path in ["/", "/news", "/admin", "/admin/news/new"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["news_categories"], self.news_categories)
        self.assertEqual(self.category_options(self.client.get('/admin/news/new').text), self.news_categories)
        tabs = re.search(r'<div class="category-tabs">(.*?)</div>', self.client.get('/news').text).group(1)
        self.assertEqual(re.findall(r'>([^<>]+)</a>', tabs), ['전체'] + self.news_categories)

    def test_legacy_news_reads_filters_and_restart_do_not_rewrite_data(self):
        self.setup_admin_and_login()
        aliases = {"행사·축제":"행사•축제", "전시":"전시•팝업", "체험·교육":"체험•교육", "공모·지원":"공모•지원"}
        for index, category in enumerate(list(aliases) + ["마켓", "지역소식"] + self.news_categories):
            self.insert_news(f"legacy-{index}", category)
        self.insert_news("hidden-event", "행사·축제", published=0)
        before = self.stored_news()
        with closing(sqlite3.connect(self.db_path)) as conn:
            raw = conn.execute("SELECT slug,category FROM news_posts WHERE published=1").fetchall()
        for category in self.news_categories:
            expected = {slug for slug, stored in raw if aliases.get(stored, stored) == category}
            for query_category in [category] + [old for old, new in aliases.items() if new == category]:
                response = self.client.get('/news', params={'category':query_category})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['active_category'], category)
                self.assertEqual({item['slug'] for item in response.context['news_items']}, expected)
        for slug, category in raw:
            detail = self.client.get(f'/news/{slug}')
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.context['item']['category'], aliases.get(category, category))
        market = self.client.get('/news', params={'category':'마켓'})
        self.assertEqual([item['slug'] for item in market.context['news_items']], ['legacy-4'])
        search = self.client.get('/news', params={'category':'행사•축제','q':'legacy-0'})
        self.assertEqual([item['slug'] for item in search.context['news_items']], ['legacy-0'])
        self.assertEqual(self.client.get('/news', params={'category':"' OR 1=1 --"}).context['news_items'], [])
        self.client.get('/admin')
        create_app({'TESTING':True, 'DATABASE':self.db_path, 'SECRET_KEY':'test-secret'})
        self.assertEqual(self.stored_news(), before)

    def test_legacy_edit_requires_valid_category_and_preserves_content(self):
        self.setup_admin_and_login()
        news_id = self.insert_news('market-record', '마켓')
        edit = self.client.get(f'/admin/news/{news_id}/edit')
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(self.category_options(edit.text), self.news_categories)
        self.assertIn('<option value="" disabled selected>', edit.text)
        self.assertIn('기존 분류: 마켓', edit.text)
        before = self.stored_news()
        for invalid in ['', '마켓', '전시', '행사·축제']:
            token = self.get_csrf_from_cookie_session(f'/admin/news/{news_id}/edit')
            payload = {'csrf_token':token, 'title':'market-record', 'slug':'market-record', 'category':invalid}
            self.client.post(f'/admin/news/{news_id}/edit', data=payload)
            self.client.post('/admin/news/new', data=payload)
            self.assertEqual(self.stored_news(), before)
        token = self.get_csrf_from_cookie_session(f'/admin/news/{news_id}/edit')
        self.client.post(f'/admin/news/{news_id}/edit', data={
            'csrf_token':token, 'title':'market-record', 'slug':'market-record',
            'category':'전시•팝업', 'body':'기존 내용 보존', 'published':'1',
        })
        detail = self.client.get('/news/market-record')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context['item']['category'], '전시•팝업')
        self.assertEqual(detail.context['item']['body'], '기존 내용 보존')
        self.assertEqual(detail.context['item']['id'], news_id)
        alias_id = self.insert_news('old-exhibition', '전시')
        alias_edit = self.client.get(f'/admin/news/{alias_id}/edit')
        self.assertIn('value="전시•팝업" selected', alias_edit.text)
        self.assertIn('기존 분류 ‘전시’', alias_edit.text)

    def test_each_new_news_category_create_edit_publish_and_delete(self):
        self.setup_admin_and_login()
        for index, category in enumerate(self.news_categories):
            with self.subTest(category=category):
                slug = f'new-category-{index}'
                token = self.get_csrf_from_cookie_session('/admin/news/new')
                payload = {'csrf_token':token, 'title':slug, 'slug':slug, 'category':category, 'published':'1'}
                saved = self.client.post('/admin/news/new', data=payload)
                self.assertEqual(saved.status_code, 200)
                detail = self.client.get(f'/news/{slug}')
                self.assertEqual(detail.context['item']['category'], category)
                news_id = detail.context['item']['id']
                edit = self.client.get(f'/admin/news/{news_id}/edit')
                self.assertEqual(self.category_options(edit.text), self.news_categories)
                payload['category'] = self.news_categories[(index+1) % 5]
                payload['title'] = f'{slug} 수정'
                self.client.post(f'/admin/news/{news_id}/edit', data=payload)
                updated = self.client.get(f'/news/{slug}')
                self.assertEqual(updated.context['item']['category'], payload['category'])
                self.assertIn(f'{slug} 수정', updated.text)
                token = self.get_csrf_from_cookie_session('/admin')
                for status in [404, 200]:
                    self.client.post(f'/admin/news/{news_id}/toggle-published', data={'csrf_token':token})
                    self.assertEqual(self.client.get(f'/news/{slug}').status_code, status)
                self.client.post(f'/admin/news/{news_id}/delete', data={'csrf_token':token})
                self.assertEqual(self.client.get(f'/news/{slug}').status_code, 404)

    def test_news_admin_auth_and_csrf_still_required(self):
        for path in ['/admin', '/admin/news/new']:
            self.assertEqual(self.client.get(path, follow_redirects=False).status_code, 303)
        self.setup_admin_and_login()
        rejected = self.client.post('/admin/news/new', data={'title':'blocked', 'category':'행사•축제'})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.stored_news(), [])


if __name__ == "__main__":
    unittest.main()
