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
from contact_inquiries import CONTENT_REQUEST, migrate, migrate_member_inquiries


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
        self.assertIn("문의가 접수되었습니다.", self.visitor.post("/contact", data=data).text)
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

    def member(self, username):
        client = TestClient(self.app)
        self.addCleanup(client.close)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            uid = conn.execute("INSERT INTO users(username,password_hash,nickname) VALUES(?,?,?)",
                               (username, hash_password("member-test-password"), username)).lastrowid
        token = client.get("/login").context["csrf_token"]
        client.post("/login", data={"username":username,"password":"member-test-password","csrf_token":token})
        return client, uid

    def test_member_issue_inbox_and_admin_reply(self):
        a, aid = self.member("sender")
        b, bid = self.member("another")
        with closing(sqlite3.connect(self.db)) as conn, conn:
            for key, uid in (("one",aid),("two",bid)):
                conn.execute("""INSERT INTO news_posts(slug,title,category,author_id,author_type,published,event_start,event_end)
                                VALUES(?,?,'행사•축제',?,'user',1,'2026-01-01','2099-12-31')""", (key,key,uid))
            issue_id = conn.execute("SELECT id FROM news_posts WHERE slug='one'").fetchone()[0]
            other_id = conn.execute("SELECT id FROM news_posts WHERE slug='two'").fetchone()[0]
        url = f"/contact?issue_id={issue_id}"
        self.assertIn(f'href="{url}"', a.get("/account").text)
        self.assertEqual(b.get(url).status_code,404)
        self.assertEqual(self.visitor.get(url,follow_redirects=False).status_code,303)
        self.assertIn("아직 등록한 문의가 없습니다.", a.get("/account").text)
        form = a.get(url)
        self.assertEqual(form.context["values"],{"inquiry_type":CONTENT_REQUEST,"title":"[ISSUE 수정·삭제 요청] one","name":"sender"})
        self.assertIn('placeholder="수정하거나 삭제가 필요한 내용을 알려주세요."></textarea>',form.text)
        data = {**self.form(), **form.context["values"], "csrf_token":form.context["csrf_token"],
                "form_token":form.context["form_token"], "title":"바꾼 제목","user_id":str(bid),"related_content_id":str(other_id)}
        self.assertEqual(a.post("/contact",data=data).status_code,400)
        self.assertEqual(a.post(f"/contact?issue_id={other_id}",data=data).status_code,404)
        self.assertEqual(a.post(url,data={**data,"inquiry_type":"기타"}).status_code,422)
        self.assertIn("내 문의 확인하기",a.post(url,data=data).text)
        a.post(url,data=data)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT user_id,related_content_type,related_content_id FROM contact_inquiries").fetchall(),
                             [(aid,"issue",issue_id)])
        self.assertIn("바꾼 제목",a.get("/account#my-inquiries").text)
        self.assertNotIn("바꾼 제목",b.get("/account").text)
        detail = "/account/inquiries/1"
        self.assertEqual(b.get(detail).status_code,404)
        self.assertEqual(b.get(detail).content,b.get("/account/inquiries/987654").content)
        self.assertEqual(self.visitor.get(detail,follow_redirects=False).status_code,303)
        before=a.get(detail).text
        self.assertNotIn("JEJU_KNOW 답변",before)
        self.assertIn('href="/news/one"',before)
        self.login()
        admin_path = "/admin/inquiries/1"
        page = self.admin.get(admin_path)
        self.assertIn(f'/admin/news/{issue_id}/edit', page.text)
        self.assertEqual(page.context["sender"]["username"],"sender")
        token = page.context["csrf_token"]
        reply = "<script>private reply</script>"
        self.assertEqual(b.post(admin_path+"/reply",data=data,follow_redirects=False).status_code,303)
        self.assertEqual(self.admin.post(admin_path+"/reply",data={"admin_reply":reply}).status_code,400)
        for status,label in (("read","확인중"),("done","처리완료")):
            self.admin.post(admin_path+"/status",data={"status":status,"csrf_token":token})
            self.assertIn(label,a.get(detail).text)
            self.assertIn(label,a.get("/account").text)
        self.admin.post(admin_path+"/reply",data={"admin_reply":reply,"csrf_token":token})
        self.assertIn("&lt;script&gt;private reply&lt;/script&gt;",a.get(detail).text)
        self.assertEqual(b.get(detail).status_code,404)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            encrypted, when = conn.execute("SELECT admin_reply,replied_at FROM contact_inquiries").fetchone()
            self.assertNotIn(reply.encode(),encrypted)
            self.assertTrue(when)
            conn.execute("UPDATE news_posts SET event_end='2026-02-01' WHERE id=?",(issue_id,))
        self.assertIn("종료",a.get("/account").text)
        self.assertEqual(a.get(url).status_code,200)
        self.assertNotIn('href="/news/one"',a.get(detail).text)

    def test_guest_and_generic_member_ownership(self):
        a, aid = self.member("sender")
        guest = self.form()
        guest_page=self.visitor.post("/contact",data={**guest,"user_id":aid})
        self.assertNotIn('href="/account#my-inquiries"',guest_page.text)
        form=a.get("/contact")
        a.post("/contact",data={**guest,"csrf_token":form.context["csrf_token"],"form_token":form.context["form_token"]})
        self.assertEqual(a.get("/account/inquiries/1").status_code,404)
        self.assertEqual(a.get("/account/inquiries/2").status_code,200)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT user_id,related_content_id FROM contact_inquiries ORDER BY id").fetchall(),[(None,None),(aid,None)])

    def test_additive_upgrade_keeps_original_columns_unchanged(self):
        path=self.db.with_name("legacy.db")
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, backup_path TEXT)")
            conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
        migrate(path)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("INSERT INTO contact_inquiries(submission_id,inquiry_type,message,status) VALUES('older','기타',x'010203','done')")
            previous=conn.execute("SELECT * FROM contact_inquiries").fetchall()[0]
        migrate_member_inquiries(path)
        migrate_member_inquiries(path)
        with closing(sqlite3.connect(path)) as conn:
            row=conn.execute("SELECT * FROM contact_inquiries").fetchone()
            self.assertEqual(row, previous+(None,)*5)
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
            receipt=conn.execute("SELECT backup_path FROM schema_migrations WHERE version='20261102_member_inquiries'").fetchone()[0]
        with closing(sqlite3.connect(receipt)) as backup:
            self.assertEqual(backup.execute("SELECT * FROM contact_inquiries").fetchone(),previous)
