import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from auth_security import hash_password
from community_migrations import today
from issue_calendar_migration import VERSION, migrate
from issue_discovery import period_bounds, schedule
import test_community as fixtures


class IssueCalendarTest(unittest.TestCase):
    def test_inclusive_status_and_year_boundary(self):
        day = date(2026, 11, 2)
        cases = [
            ("2026-11-10", "2026-11-15", "예정", "D-8"),
            ("2026-11-02", "2026-11-09", "진행중", "오늘 시작"),
            ("2026-11-02", "2026-11-02", "진행중", "오늘까지"),
            ("2026-10-26", "2026-11-02", "진행중", "오늘까지"),
            ("2026-10-30", "2026-11-03", "진행중", "진행중"),
            ("2026-10-30", "2026-11-01", "종료", "종료"),
            ("", "", "일정 미등록", "일정 미등록"),
        ]
        for a,b,status,badge in cases:
            item = schedule({"event_start":a,"event_end":b}, day)
            self.assertEqual((item["event_status"],item["date_badge"]),(status,badge))
        self.assertEqual(schedule({"event_start":"2026-12-28","event_end":"2027-01-05"},day)["date_label"],
                         "2026.12.28 - 2027.01.05")
        self.assertEqual(period_bounds("weekend",date(2027,1,1)),(date(2027,1,2),date(2027,1,3)))
        self.assertEqual(period_bounds("month",date(2028,2,1))[1],date(2028,2,29))

    def test_kst_midnight(self):
        for minute, expected in [(14*60+59,date(2026,11,2)),(15*60,date(2026,11,3))]:
            instant = datetime(2026,11,2,minute//60,minute%60,59,tzinfo=timezone.utc)
            with patch("community_migrations.datetime") as clock:
                clock.now.side_effect = lambda tz: instant.astimezone(tz)
                self.assertEqual(today(), expected)

    def test_migration_preserves_opportunities_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder)/"old.db"
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.executescript("""CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,backup_path TEXT);
                    CREATE TABLE news_posts(id INTEGER PRIMARY KEY,category TEXT,event_start TEXT,event_end TEXT,
                    published INTEGER,deleted_at TEXT,image_url TEXT,address TEXT);
                    INSERT INTO news_posts VALUES(8,'지원사업','','',1,NULL,'/uploads/test.webp','기존 주소');""")
            migrate(db)
            migrate(db)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute("SELECT id,category,event_start,event_end,image_url,address,show_on_map FROM news_posts").fetchone(),
                                 (8,"지원사업","","","/uploads/test.webp","기존 주소",0))
                receipt = conn.execute("SELECT backup_path FROM schema_migrations WHERE version=?", (VERSION,)).fetchall()
                self.assertEqual(len(receipt),1)
                with closing(sqlite3.connect(receipt[0][0])) as backup:
                    self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0],"ok")
                    self.assertNotIn("show_on_map",[r[1] for r in backup.execute("PRAGMA table_info(news_posts)")])


class IssueBrowserTest(unittest.TestCase):
    setUp = fixtures.CommunityTest.setUp
    tearDown = fixtures.CommunityTest.tearDown
    execute = fixtures.CommunityTest.execute
    rows = fixtures.CommunityTest.rows
    csrf = fixtures.CommunityTest.csrf

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password("community-test-secret")

    def issue(self, slug, start, end, category="전시•팝업", public=1, mapped=0, coords=(None,None)):
        return self.execute("""INSERT INTO news_posts(slug,title,category,event_start,event_end,
            region,published,show_on_map,latitude,longitude) VALUES (?,?,?,?,?,'애월',?,?,?,?)""",
            (slug,slug,category,start,end,public,mapped,*coords))

    def test_education_aliases_preserve_records_across_public_admin_and_account(self):
        day = today().isoformat()
        aliases = ("체험•교육", "체험·교육", "교육•체험", "교육·체험")
        uid = self.rows("SELECT id FROM users WHERE username='member'")[0]["id"]
        for index, label in enumerate(aliases):
            row_id = self.issue(f"education-{index}", day, day, label, mapped=1, coords=(33.45,126.4))
            self.execute("UPDATE news_posts SET author_type='user',author_id=?,venue='교육장',address='제주 애월읍' WHERE id=?", (uid,row_id))
        self.issue("other-event", day, day, "행사•축제")
        before = self.rows("SELECT * FROM news_posts ORDER BY id")
        for category in (*aliases, "activities"):
            page = self.visitor.get("/news", params={"category":category,"period":"today",
                "region":"애월","q":"education-","view":"map"})
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.context["selected"]["category"], "교육·체험")
            self.assertEqual(page.context["map_count"], 4)
            self.assertEqual({r["slug"] for r in page.context["news_items"]}, {f"education-{i}" for i in range(4)})
            self.assertNotIn("체험·교육", page.text)
            self.assertNotIn("체험•교육", page.text)
        for label in aliases:
            page = self.admin.get("/admin/issues", params={"category":label})
            self.assertEqual(page.context["filters"]["category"], "교육·체험")
            self.assertEqual(len(page.context["issues"]), 4)
            self.assertIn('<option selected>교육·체험</option>', page.text)
        for row in before[:4]:
            detail = self.visitor.get("/news/"+row["slug"])
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.context["item"]["category_label"], "교육·체험")
            self.assertIn("교육·체험", detail.context["seo"]["title"])
            self.assertIn('"@type": "Event"', detail.text)
            edit = self.admin.get(f'/admin/news/{row["id"]}/edit')
            self.assertIn('value="교육·체험" selected', edit.text)
        account = self.member.get("/account/issues")
        self.assertIn("교육·체험", account.text)
        self.assertNotIn("체험·교육", account.text)
        self.assertNotIn("education-", self.other.get("/account/issues").text)
        self.assertEqual(self.rows("SELECT * FROM news_posts ORDER BY id"), before)

    def test_all_public_entry_points_and_intersections(self):
        self.issue("running", "2026-10-30","2026-11-09", mapped=1, coords=(33.45,126.4))
        self.issue("weekend", "2026-11-06","2026-11-09", mapped=1)
        self.issue("day-only", "2026-11-02","2026-11-02", coords=(33.44,126.3))
        self.issue("later-month","2026-11-29","2026-12-02",category="지역소식")
        for slug,a,b,cat,public in [("ended","2026-10-01","2026-11-01","행사·축제",1),
                                   ("hidden","2026-11-01","2026-11-06","행사·축제",0),
                                   ("support","2026-11-01","2026-11-06","지원사업",1),
                                   ("undated","","","행사•축제",1)]:
            self.issue(slug,a,b,cat,public,1,(33.4,126.5))
        day = date(2026,11,2)
        with patch("app.issue_now",return_value=day), patch("issue_discovery.today",return_value=day):
            for period,expected in {"all":["day-only","running","weekend","later-month"],
                      "today":["day-only","running"], "week":["day-only","running","weekend"],
                      "weekend":["running","weekend"],"month":["day-only","running","weekend","later-month"]}.items():
                page = self.visitor.get("/news",params={"period":period})
                self.assertEqual([x["slug"] for x in page.context["news_items"]],expected)
                self.assertEqual(page.context["map_count"],1)
            page=self.visitor.get("/news",params={"period":"weekend","category":"전시·팝업","region":"애월","q":"weekend","view":"map"})
            self.assertEqual([x["slug"] for x in page.context["news_items"]],["weekend"])
            self.assertEqual(page.context["map_count"],0)
            self.assertEqual(page.context["selected"]["view"],"map")
            self.assertEqual(self.visitor.get("/news?region=조천").context["news_items"],[])
            public = {x["slug"] for x in self.visitor.get("/").context["news_items"]}
            self.assertEqual(public,{"running","day-only","weekend","later-month"})
            sitemap=self.visitor.get("/sitemap.xml").text
            for slug in ("ended","hidden","support","undated"):
                self.assertNotIn("/news/"+slug,sitemap)
                self.assertEqual(self.visitor.get("/news/"+slug).status_code,404)
            self.assertIn("오늘까지",self.visitor.get("/news/day-only").text)
        with patch("app.issue_now",return_value=date(2026,11,3)):
            self.assertEqual(self.visitor.get("/news/day-only").status_code,404)
        self.assertEqual(len(self.rows("SELECT * FROM news_posts")),8)

    def test_admin_crud_validation_status_and_mapping(self):
        url="/admin/news/new"
        payload={"csrf_token":self.csrf(self.admin,"/admin"),"slug":"timed","title":"날짜 검증",
                 "category":"행사•축제","event_start":"2026-11-10","event_end":"2026-11-09","published":"1",
                 "region":"애월","address":"애월읍","show_on_map":"1","start_time":"10:00","end_time":"18:00"}
        rejected=self.admin.post(url,data=payload)
        self.assertEqual(rejected.status_code,400)
        self.assertIn("종료일은 시작일 이후 날짜를 선택해주세요.",rejected.text)
        payload["event_end"]="2026-11-10"
        self.assertEqual(self.admin.post(url,data=payload,follow_redirects=False).status_code,303)
        p=self.rows("SELECT * FROM news_posts")[0]
        self.assertEqual((p["latitude"],p["longitude"],p["show_on_map"],p["region"],p["start_time"]),(None,None,1,"애월","10:00"))
        edit=f"/admin/news/{p['id']}/edit"
        self.assertIn("지도 위치 미등록",self.admin.get(edit).text)
        payload.update(latitude="33.43",longitude="126.4",start_time="",end_time="")
        self.assertEqual(self.admin.post(edit,data=payload,follow_redirects=False).status_code,303)
        with patch("admin_ui.today",return_value=date(2026,11,11)):
            self.assertIn("날짜 검증",self.admin.get("/admin/issues?status=ended").text)
            self.assertNotIn("날짜 검증",self.admin.get("/admin/issues?status=ongoing").text)
        self.assertEqual(self.admin.post(edit,data={**payload,"latitude":"NaN"}).status_code,400)
        self.assertEqual(self.visitor.get(edit,follow_redirects=False).status_code,303)
        self.assertEqual(self.admin.post(edit,data={**payload,"csrf_token":"invalid"}).status_code,400)

    def test_member_public_only_and_no_map_privilege_escalation(self):
        data={"csrf_token":self.csrf(self.member,"/issues/new"),"title":"회원 행사","category":"공모•지원",
              "event_start":"2099-01-01","event_end":"2099-01-01","show_on_map":"1","latitude":"33.4","longitude":"126.5"}
        self.assertEqual(self.member.post("/issues/new",data=data).status_code,400)
        from test_images import picture
        data.update(category="지역소식", region="애월", summary="소식")
        self.assertEqual(self.member.post("/issues/new",data=data,files={"image_0":("poster.png", picture(),"image/png")},follow_redirects=False).status_code,303)
        self.assertEqual(self.rows("SELECT published,show_on_map,latitude FROM news_posts"),[{"published":0,"show_on_map":0,"latitude":None}])
