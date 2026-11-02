import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import test_images as images
from ad_banners import migrate
from app import create_app
from auth_security import hash_password


class AdBannerTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.database = Path(folder.name)/"app.db"
        self.app = create_app({"DATABASE": str(self.database), "TESTING":True, "SECRET_KEY":"ad-tests"})
        self.admin, self.visitor = TestClient(self.app), TestClient(self.app)
        self.addCleanup(self.admin.close)
        self.addCleanup(self.visitor.close)
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("INSERT INTO admins(username,password_hash) VALUES (?,?)", ("editor", hash_password("banner-test-secret")))
        token = self.admin.get("/admin/login").context["csrf_token"]
        self.admin.post("/admin/login",data={"csrf_token":token,"username":"editor","password":"banner-test-secret"})
        self.token = self.admin.get("/admin/ads/new").context["csrf_token"]
        self.day = date(2026,11,2)

    def save(self, *, path="/admin/ads/new", photo=True, **changes):
        values = {"name":"배너 점검","advertiser_name":"제주노","ad_type":"ad","slot":"SIDE-01",
                  "target_url":"https://example.org/campaign","start_date":self.day.isoformat(),
                  "end_date":self.day.isoformat(),"priority":"2","is_public":"1","csrf_token":self.token, **changes}
        return self.admin.post(path,data=values,files={"image_0":("test.png",images.picture(),"image/png")} if photo else None,
                               follow_redirects=False)

    def test_selection_labels_rollover_and_preservation(self):
        empty = self.visitor.get("/")
        self.assertNotIn('class="sponsor-rail"',empty.text)
        self.assertNotIn('class="wrap sponsor-inline"',empty.text)
        self.save(name="순위 2")
        self.save(name="순위 1",priority="1")
        self.save(name="동률",priority="1")
        self.save(name="제휴 행사",slot="HOME-01",ad_type="partnership",description="가로 배너")
        self.save(name="비공개",priority="1",is_public="")
        self.save(name="다음 달",start_date="2026-12-01",end_date="2026-12-10")
        self.save(name="종료됨",start_date="2026-10-01",end_date="2026-11-01")
        with patch("app.community_today",return_value=self.day), patch("ad_banners.today",return_value=self.day):
            page = self.visitor.get("/")
            self.assertEqual({slot:ad["name"] for slot,ad in page.context["banners"].items()},
                             {"SIDE-01":"순위 1","HOME-01":"제휴 행사"})
            self.assertEqual(page.text.count('rel="noopener noreferrer sponsored"'),2)
            self.assertIn('alt="제주노 · 순위 1"',page.text)
            self.assertIn('class="sponsor-label">제휴',page.text)
            self.assertIn('class="sponsor-label">광고',page.text)
            url="/uploads/"+page.context["banners"]["SIDE-01"]["image_path"]
            self.assertEqual(self.visitor.get(url).headers["content-type"],"image/webp")
            reports=self.admin.get("/admin/ads").context["ads"]
            self.assertEqual(len(reports),7)
            self.assertEqual({r["status"] for r in reports},{"예정","진행중","종료"})
        tomorrow=self.day+timedelta(days=1)
        with patch("app.community_today",return_value=tomorrow), patch("ad_banners.today",return_value=tomorrow):
            self.assertFalse(self.visitor.get("/").context["banners"])
            self.assertEqual(self.visitor.get(url).status_code,404)
            self.assertEqual(self.admin.get(url).status_code,200)
            self.assertEqual(len(self.admin.get("/admin/ads").context["ads"]),7)

    def test_image_replacement_and_invalid_edits(self):
        self.assertEqual(self.save().status_code,303)
        ad=dict(self.admin.get("/admin/ads/1/edit").context["item"])
        path=self.app.state.image_store.path(ad["image_path"])
        for values in ({"target_url":"javascript:alert(1)"},{"target_url":"data:text/plain,hello"},
                       {"end_date":"2026-11-01"},{"start_date":"2026-02-31"},{"slot":"LEFT-01"},{"priority":"0"}):
            self.assertEqual(self.save(path="/admin/ads/1/edit",revision="1",**values).status_code,400)
        edit=dict(path="/admin/ads/1/edit",revision="1")
        self.assertEqual(self.save(**edit,is_public="").status_code,303)
        self.assertFalse(path.exists())
        self.assertEqual(self.save(**edit).status_code,409)
        edited=self.admin.get("/admin/ads/1/edit").context["item"]
        self.assertEqual(edited["is_public"],0)
        self.assertEqual(self.save(path=edit["path"],revision="2",photo=False).status_code,303)
        self.assertEqual(self.admin.get("/admin/ads/1/edit").context["item"]["image_path"],edited["image_path"])

    def test_auth_image_and_csrf_required(self):
        for path in ("/admin/ads","/admin/ads/new","/admin/ads/1/edit"):
            self.assertEqual(self.visitor.get(path,follow_redirects=False).status_code,303)
        self.assertEqual(self.visitor.post("/admin/ads/new",follow_redirects=False).status_code,303)
        self.assertEqual(self.save(csrf_token="invalid").status_code,400)
        self.assertEqual(self.save(photo=False).status_code,400)
        self.assertEqual(self.admin.get("/admin/ads").context["ads"],[])

    def test_migration_has_verified_pre_change_copy_and_is_idempotent(self):
        self.save()
        migrate(self.database)
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM ads").fetchone(),(1,))
            backup=conn.execute("SELECT backup_path FROM schema_migrations WHERE version='20261102_home_ads'").fetchone()[0]
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone(),("ok",))
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='ads'").fetchone())
