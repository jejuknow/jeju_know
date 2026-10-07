import os
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient
from app import create_app


class JejunoAppTest(unittest.TestCase):
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
            "features": ["감성"],
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

        conn = sqlite3.connect(self.db_path)
        place_id = conn.execute("SELECT id FROM places WHERE slug='test-cafe'").fetchone()[0]
        conn.close()

        token = self.get_csrf_from_cookie_session(f"/admin/places/{place_id}/edit")
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
            "category": "행사·축제",
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


if __name__ == "__main__":
    unittest.main()
