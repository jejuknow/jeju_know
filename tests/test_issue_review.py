import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch

import test_community as fixtures
from community_migrations import today
from test_images import picture


class IssueReviewTest(unittest.TestCase):
    setUp = fixtures.CommunityTest.setUp
    tearDown = fixtures.CommunityTest.tearDown
    csrf = fixtures.CommunityTest.csrf
    rows = fixtures.CommunityTest.rows
    execute = fixtures.CommunityTest.execute
    setUpClass = classmethod(fixtures.CommunityTest.setUpClass.__func__)

    def data(self, **fields):
        return dict({"title":"제주 주말 전시", "summary":"전시를 소개합니다.", "category":"전시•팝업",
                     "region":"애월", "address":"애월읍", "latitude":"33.4", "longitude":"126.4",
                     "event_start":today().isoformat(), "event_end":today().isoformat()}, **fields)

    def submit(self, photo=True, **fields):
        return self.member.post("/issues/new", data=self.data(csrf_token=self.csrf(self.member), **fields),
             files={"image_0":("poster.png", picture(), "image/png")} if photo else None, follow_redirects=False)

    def review(self, row, **fields):
        return self.admin.post(f'/admin/news/{row["id"]}/edit', data=self.data(csrf_token=self.csrf(self.admin,"/admin"),
            slug=row["slug"], review_version=row["review_version"], review_action="approve", show_on_map="1", **fields),
            follow_redirects=False)

    def test_pending_approval_lock_private_poster_public_discovery_and_expiry(self):
        self.assertEqual(self.visitor.get("/issues/new",follow_redirects=False).headers["location"],"/login?next=/issues/new")
        self.assertEqual(self.submit(published="1",approval_status="approved",author_type="admin").headers["location"],"/issues/submitted")
        row=self.rows("SELECT * FROM news_posts")[0]
        self.assertEqual((row["author_type"],row["published"],row["approval_status"]),("user",0,"pending"))
        slug_url="/news/"+row["slug"]
        poster="/uploads/"+self.rows("SELECT file_path FROM images")[0]["file_path"]
        for url in (slug_url,poster):
            self.assertEqual(self.visitor.get(url).status_code,404)
        self.assertIn("검토 대기",self.member.get("/account").text)
        self.assertIn('>검토 대기</span>',self.member.get("/issues/submitted").text)
        admin_dash=self.admin.get("/admin")
        self.assertEqual(admin_dash.context["admin_counts"]["issue_pending"],1)
        self.assertIn(row["title"],self.admin.get("/admin/issues?status=pending").text)
        self.assertEqual(self.admin.post(f'/admin/news/{row["id"]}/toggle-published',data={"csrf_token":self.csrf(self.admin,"/admin")}).status_code,409)
        edit=f'/issues/{row["id"]}/edit'
        self.assertEqual(self.member.post(edit,data=self.data(csrf_token=self.csrf(self.member)),follow_redirects=False).status_code,303)
        self.assertEqual(self.review(row).status_code,409)  # review page was opened before member's edit
        row=self.rows("SELECT * FROM news_posts")[0]
        self.assertEqual(self.review(row,title="검토한 전시").status_code,303)
        page=self.visitor.get("/news",params={"period":"today","region":"애월","category":"exhibitions","q":"검토한"})
        self.assertEqual(page.context["map_count"],1)
        self.assertIn("검토한 전시",page.text)
        self.assertIn('data-badge>진행중 · 오늘까지</span>', page.text)
        self.assertIn('data-badge>진행중 · 오늘까지</span>', self.visitor.get(slug_url).text)
        self.assertNotIn("승인·공개",page.text)
        self.assertIn(slug_url,self.visitor.get("/sitemap.xml").text)
        self.assertIn("검토한 전시",self.visitor.get("/").text)
        self.assertEqual(self.visitor.get(poster).status_code,200)
        self.assertEqual(self.member.get(edit).status_code,403)
        self.assertEqual(self.member.post(edit.replace("/edit","/delete"),data={"csrf_token":self.csrf(self.member)}).status_code,403)
        self.assertIn("승인·공개",self.member.get("/account").text)
        tomorrow=today()+timedelta(days=1)
        with patch("app.community_today",return_value=tomorrow), patch("member_activity.today",return_value=tomorrow):
            self.assertEqual(self.visitor.get("/news").context["map_count"],0)
            self.assertEqual(self.visitor.get(slug_url).status_code,404)
            self.assertIn("종료",self.member.get("/account").text)
        self.assertEqual(self.rows("SELECT reviewed_by,approval_status FROM news_posts")[0],
                         {"reviewed_by":"admin:1","approval_status":"approved"})

    def test_education_legacy_submission_edit_and_approval_use_canonical_label(self):
        for client, path in ((self.member,"/issues/new"),(self.admin,"/admin/news/new")):
            page = client.get(path)
            self.assertIn('value="교육·체험"', page.text)
            self.assertNotIn('value="체험', page.text)
        self.assertEqual(self.submit(category="체험•교육").status_code, 303)
        row = self.rows("SELECT * FROM news_posts")[0]
        self.assertEqual((row["category"],row["approval_status"],row["published"]), ("교육·체험","pending",0))
        # Simulate a pending record created before deployment; GET must not rewrite it.
        self.execute("UPDATE news_posts SET category='체험•교육' WHERE id=?", (row["id"],))
        path = f'/issues/{row["id"]}/edit'
        self.assertIn('value="교육·체험" selected',self.member.get(path).text)
        self.assertEqual(self.rows("SELECT category FROM news_posts")[0]["category"],"체험•교육")
        self.assertEqual(self.other.get(path).status_code, 403)
        saved = self.member.post(path, data=self.data(category="체험·교육",csrf_token=self.csrf(self.member)), follow_redirects=False)
        self.assertEqual(saved.status_code,303)
        row = self.rows("SELECT * FROM news_posts")[0]
        self.assertEqual((row["category"],row["approval_status"],row["published"]), ("교육·체험","pending",0))
        self.assertEqual(self.review(row,category="교육•체험").status_code,303)
        approved = self.rows("SELECT * FROM news_posts")[0]
        self.assertEqual((approved["category"],approved["approval_status"],approved["published"]), ("교육·체험","approved",1))
        self.assertEqual((approved["id"],approved["slug"],approved["author_id"]), (row["id"],row["slug"],row["author_id"]))
        self.assertIn(row["title"],self.visitor.get("/news?category=activities").text)

    def test_rejection_duplicates_disclosure_and_rate_limit(self):
        self.submit()
        row=self.rows("SELECT * FROM news_posts")[0]
        self.review(row)
        self.assertEqual(self.submit().status_code,409)
        self.assertIn("/news/"+row["slug"],self.submit().text)
        self.assertEqual(self.submit(duplicate_ack="1").status_code,303)
        second=self.rows("SELECT * FROM news_posts ORDER BY id DESC")[0]
        admin_page=f'/admin/news/{second["id"]}'
        self.assertIn("중복 의심",self.admin.get(admin_page+"/edit").text)
        self.assertEqual(self.submit(title="또 다른 소식").status_code,429)
        reason="주최 측 공지를 확인하기 어렵습니다."
        form={"csrf_token":self.csrf(self.admin,"/admin"),"review_version":second["review_version"],"rejection_reason":reason}
        self.assertEqual(self.admin.post(admin_page+"/reject",data={**form,"csrf_token":"wrong"}).status_code,400)
        self.assertEqual(self.admin.post(admin_page+"/reject",data=form,follow_redirects=False).status_code,303)
        self.assertIn(reason,self.member.get("/account/issues").text)
        self.assertNotIn(reason,self.other.get("/account/issues").text)
        self.assertEqual(self.visitor.get("/news/"+second["slug"]).status_code,404)
        self.assertEqual(self.admin.get("/admin").context["admin_counts"]["issue_pending"],0)

    def test_required_fields_past_dates_and_unresolved_address(self):
        for changes in ({"region":""},{"region":"다른 지역"},{"summary":" "},{"category":"공모•지원"},
                        {"event_start":""},{"body":"<script>unsafe()</script>"},{"external_url":"javascript:alert(1)"}):
            self.assertEqual(self.submit(**changes).status_code,400,changes)
        self.assertIn("대표이미지",self.submit(photo=False).text)
        yesterday=(today()-timedelta(days=1)).isoformat()
        self.assertIn("이미 종료된 ISSUE",self.submit(event_start=yesterday,event_end=yesterday).text)
        self.assertEqual(self.submit(latitude="",longitude="").status_code,303)
        row=self.rows("SELECT * FROM news_posts")[0]
        self.assertIsNone(row["latitude"])
        self.assertEqual(row["show_on_map"],0)
        self.assertIn("지도 위치 미확인",self.admin.get(f'/admin/news/{row["id"]}/edit').text)


class ReviewMigrationTest(unittest.TestCase):
    def test_verified_backup_preserves_existing_authors_and_visibility(self):
        from issue_review import migrate
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"jejuno.db"
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript("""CREATE TABLE news_posts(id INTEGER PRIMARY KEY,author_type TEXT,
                    author_id INTEGER,published INTEGER,deleted_at TEXT);
                    CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,backup_path TEXT);
                    INSERT INTO news_posts VALUES (1,'admin',NULL,1,NULL),(2,'admin',NULL,0,NULL),(3,'user',7,1,NULL);""")
            migrate(path)
            migrate(path)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("SELECT author_type,author_id,published,approval_status FROM news_posts ORDER BY id").fetchall(),
                                 [('admin',None,1,'approved'),('admin',None,0,'approved'),('user',7,1,'approved')])
                backups=conn.execute("SELECT backup_path FROM schema_migrations").fetchall()
                self.assertEqual(len(backups),1)
            with closing(sqlite3.connect(backups[0][0])) as backup:
                self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0],"ok")
                self.assertNotIn("approval_status",[c[1] for c in backup.execute("PRAGMA table_info(news_posts)")])
