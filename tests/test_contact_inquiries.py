import os
import secrets
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import create_app
from auth_security import hash_password
from contact_inquiries import migrate


class ContactTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name)/"app.db"
        self.app = create_app({"DATABASE":str(self.db),"TESTING":True,"SECRET_KEY":"contact-tests"})
        self.visitor, self.admin = TestClient(self.app), TestClient(self.app)
        self.addCleanup(self.visitor.close)
        self.addCleanup(self.admin.close)
        env = patch.dict(os.environ, PII_ENCRYPTION_KEY=secrets.token_hex(32))
        env.start()
        self.addCleanup(env.stop)

    def form(self):
        page = self.visitor.get("/contact")
        return dict(csrf_token=page.context["csrf_token"], form_token=page.context["form_token"],
                    inquiry_type="행사·공간 협업", name="점검 별명", email="qa@example.invalid",
                    title="비공개 제목", content="<script>testMarker()</script>", phone="")

    def login(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO admins(username,password_hash) VALUES(?,?)", ("tester", hash_password("test password")))
        token = self.admin.get("/admin/login").context["csrf_token"]
        self.admin.post("/admin/login", data={"csrf_token":token, "username":"tester", "password":"test password"})

    def test_submit_privacy_workflow_and_permissions(self):
        data = self.form()
        self.assertIn("문의가 등록되었습니다.", self.visitor.post("/contact", data=data).text)
        self.assertEqual(self.visitor.post("/contact", data=data, follow_redirects=False).status_code, 303)
        with closing(sqlite3.connect(self.db)) as conn:
            rows = conn.execute("SELECT message,status FROM contact_inquiries").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][1], "unread")
            for k in ("name","email","title","content"):
                self.assertNotIn(data[k].encode(), rows[0][0])
        for path in ("/admin/inquiries","/admin/inquiries/1"):
            self.assertEqual(self.visitor.get(path, follow_redirects=False).status_code,303)
        self.assertEqual(self.visitor.post("/admin/inquiries/1/status", data={"status":"done"}, follow_redirects=False).status_code,303)
        self.login()
        dashboard = self.admin.get("/admin")
        self.assertEqual(dashboard.context["admin_counts"]["unread_inquiries"],1)
        listing = self.admin.get("/admin/inquiries?status=unread")
        self.assertIn(data["email"], listing.text)
        detail = self.admin.get("/admin/inquiries/1")
        self.assertIn("&lt;script&gt;", detail.text)
        self.assertNotIn(data["content"], detail.text)
        token = detail.context["csrf_token"]
        self.assertEqual(self.admin.post("/admin/inquiries/1/status", data={"status":"done"}).status_code,400)
        self.assertEqual(self.admin.post("/admin/inquiries/1/status", data={"status":"other","csrf_token":token}).status_code,400)
        for status in ("read","done"):
            updated=self.admin.post("/admin/inquiries/1/status", data={"status":status, "csrf_token":token})
            self.assertEqual(updated.context["item"]["status"],status)
        self.assertEqual(self.admin.get("/admin/inquiries?status=unread").context["pagination"]["total"],0)
        migrate(self.db)
        with closing(sqlite3.connect(self.db)) as conn:
            backup = conn.execute("SELECT backup_path FROM schema_migrations WHERE version='20261102_contact_inquiries'").fetchone()[0]
            self.assertEqual(conn.execute("SELECT count(*) FROM contact_inquiries").fetchone()[0],1)
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='contact_inquiries'").fetchone())

    def test_validation_rate_limit_and_key_failure(self):
        form=self.form()
        for changes,code in [({"csrf_token":"x"},400),({"form_token":"x"},400),({"email":"bad"},422),
                             ({"title":"x"*161},422),({"inquiry_type":"invalid"},422),({"name":""},422)]:
            self.assertEqual(self.visitor.post("/contact",data={**form, **changes}).status_code,code)
        with patch.dict(os.environ, PII_ENCRYPTION_KEY=""):
            self.assertEqual(self.visitor.post("/contact",data=form).status_code,503)
        for i in range(4):
            new=self.form()
            self.assertEqual(self.visitor.post("/contact",data=new,follow_redirects=False).status_code,303 if i<3 else 429)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM contact_inquiries").fetchone()[0],3)

    def test_links_and_seo(self):
        home=self.visitor.get("/").text
        self.assertEqual(home.count('href="https://open.kakao.com/o/gLuLBdVi" target="_blank" rel="noopener noreferrer"'),4)
        self.assertLess(home.index('id="collections"'),home.index('id="community-title"'))
        self.assertLess(home.index('id="community-title"'),home.index('<footer'))
        self.assertEqual(self.visitor.get("/contact/sent",follow_redirects=False).status_code,303)
        contact=self.visitor.get("/contact").text
        self.assertIn("<title>문의하기 | JEJU_KNOW</title>",contact)
        self.assertIn('name="robots" content="noindex,follow"',contact)
        self.assertNotIn("/contact",self.visitor.get("/sitemap.xml").text)
