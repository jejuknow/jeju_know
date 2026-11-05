"""Schedule ordering, eligibility and badges must agree without changing stored posts."""
from datetime import datetime, timezone
from unittest.mock import patch
import unittest

from community_migrations import KST
from issue_discovery import as_moment, schedule
import test_community as fixtures
from auth_security import hash_password


class IssuePriorityTest(unittest.TestCase):
    setUp = fixtures.CommunityTest.setUp
    tearDown = fixtures.CommunityTest.tearDown
    execute = fixtures.CommunityTest.execute
    rows = fixtures.CommunityTest.rows
    csrf = fixtures.CommunityTest.csrf
    instant = datetime(2026, 11, 5, 12, tzinfo=KST)

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password("community-test-secret")

    def issue(self, slug, start, end, **changes):
        values = dict(slug=slug, title=slug, category="행사•축제", event_start=start,
                      event_end=end, published=1, region="애월", summary="일정 점검", show_on_map=1,
                      latitude=33.45, longitude=126.4, created_at="2026-10-01 00:00:00")
        values.update(changes)
        return self.execute("INSERT INTO news_posts (" + ",".join(values) + ") VALUES (" +
                            ",".join("?" for _ in values) + ")", list(values.values()))

    def listing(self, path="/", **query):
        response = self.visitor.get(path, params=query)
        self.assertEqual(response.status_code, 200)
        return response.context["news_items"]

    def test_priority_before_limit_visibility_authors_and_no_writes(self):
        uid = self.rows("SELECT id FROM users WHERE username='member'")[0]["id"]
        # Deliberately register in reverse schedule priority with different categories/flags.
        self.issue("F", "2026-11-19", "2026-11-22", is_promoted=1, is_featured=1)
        self.issue("E", "2026-11-12", "2026-11-12", category="교육·체험")
        self.issue("D", "2026-11-07", "2026-11-08")
        self.issue("C", "2026-11-06", "2026-11-08", author_type="user", author_id=uid)
        self.issue("B", "2026-11-01", "2026-11-08")
        self.issue("A", "2026-11-05", "2026-11-05", author_type="user", author_id=uid)
        self.issue("G", "2026-10-30", "2026-11-04")
        excluded = {"hidden": {"published":0}, "pending":{"approval_status":"pending"},
                    "rejected":{"approval_status":"rejected"}, "deleted":{"deleted_at":"2026-11-01"}}
        for slug, changes in excluded.items():
            self.issue(slug, "2026-11-05", "2026-11-05", **changes)
        self.issue("undated-local", "", "", category="지역소식")
        before = self.rows("SELECT * FROM news_posts ORDER BY id")
        schema = self.rows("SELECT * FROM sqlite_master ORDER BY name")
        with patch("app.issue_now", return_value=self.instant):
            home = self.listing()
            self.assertEqual([r["slug"] for r in home], list("ABCDE"))
            self.assertEqual([r["schedule_key"] for r in home], ["ongoing"]*2 + ["upcoming"]*3)
            self.assertEqual([r["date_badge"] for r in home], ["오늘까지", "진행중", "D-1", "D-2", "D-7"])
            self.assertEqual([r["slug"] for r in self.listing("/news")], list("ABCDEF"))
            self.assertEqual([r["slug"] for r in self.listing("/news", period="week", region="애월", q="일정", category="events", view="map")], list("ABCD"))
            self.assertEqual([r["slug"] for r in self.listing("/news", period="today")], list("AB"))
            for item in home:
                detail = self.visitor.get("/news/" + item["slug"])
                self.assertEqual(detail.context["item"]["schedule_key"], item["schedule_key"])
            for slug in ["G", "undated-local", *excluded]:
                self.assertEqual(self.visitor.get("/news/" + slug).status_code, 404)
        self.assertEqual(before, self.rows("SELECT * FROM news_posts ORDER BY id"))
        self.assertEqual(schema, self.rows("SELECT * FROM sqlite_master ORDER BY name"))

    def test_equal_dates_use_created_not_updated_flags_or_time_then_stable_id(self):
        self.issue("ongoing-old", "2026-11-01", "2026-11-08", is_promoted=1, updated_at="2099-01-01")
        self.issue("ongoing-new", "2026-11-05", "2026-11-08", created_at="2026-10-02T00:00:00Z")
        self.issue("upcoming-old", "2026-11-06", "2026-11-09", start_time="09:00", is_featured=1)
        self.issue("upcoming-new", "2026-11-06", "2026-11-09", start_time="18:00", created_at="2026-10-02T09:00:00+09:00")
        self.issue("upcoming-tie", "2026-11-06", "2026-11-10", created_at="2026-10-02 00:00:00")
        with patch("app.issue_now", return_value=self.instant):
            for path in ("/", "/news"):
                self.assertEqual([r["slug"] for r in self.listing(path)],
                    ["ongoing-new", "ongoing-old", "upcoming-tie", "upcoming-new", "upcoming-old"])

    def test_timed_today_sql_and_badges_agree_at_inclusive_boundaries(self):
        self.issue("timed", "2026-11-05", "2026-11-05", start_time="14:00", end_time="16:00")
        for instant, expected in [(self.instant, "upcoming"), (self.instant.replace(hour=14), "ongoing"),
                                  (self.instant.replace(hour=16), "ongoing"),
                                  (self.instant.replace(hour=16, microsecond=1), "ended")]:
            with self.subTest(instant=instant), patch("app.issue_now", return_value=instant):
                actual = schedule(self.rows("SELECT * FROM news_posts")[0], instant)
                self.assertEqual(actual["schedule_key"], expected)
                for path in ("/", "/news"):
                    items = self.listing(path)
                    self.assertEqual(len(items), 0 if expected == "ended" else 1)
                    if items:
                        self.assertEqual(items[0]["schedule_key"], expected)
                detail = self.visitor.get("/news/timed")
                self.assertEqual(detail.status_code, 404 if expected == "ended" else 200)

    def test_kst_date_only_last_microsecond_and_missing_local_not_backfilled(self):
        self.issue("day-only", "2026-11-05", "2026-11-05")
        self.issue("undated", "", "", category="지역소식")
        for utc, expected in [(datetime(2026,11,4,14,59,59,tzinfo=timezone.utc), "upcoming"),
                               (datetime(2026,11,4,15,tzinfo=timezone.utc), "ongoing"),
                               (datetime(2026,11,5,14,59,59,999999,tzinfo=timezone.utc), "ongoing"),
                               (datetime(2026,11,5,15,tzinfo=timezone.utc), "ended")]:
            with self.subTest(utc=utc), patch("app.issue_now", return_value=utc):
                items = self.listing()
                self.assertEqual([r["slug"] for r in items], [] if expected == "ended" else ["day-only"])
                if items:
                    self.assertEqual(items[0]["schedule_key"], expected)
        self.assertEqual(as_moment(datetime(2026,11,4,15,tzinfo=timezone.utc)).date().isoformat(), "2026-11-05")

    def test_legacy_invalid_times_fall_back_to_dates_and_invalid_ranges_are_excluded(self):
        self.issue("legacy", "2026-11-05", "2026-11-05", start_time="bad", end_time="24:00")
        self.issue("invalid-range", "2026-11-05", "2026-11-05", start_time="18:00", end_time="09:00")
        self.issue("invalid-date", "2026-02-30", "2026-11-06")
        with patch("app.issue_now", return_value=self.instant):
            self.assertEqual([r["slug"] for r in self.listing()], ["legacy"])
            self.assertEqual(self.listing()[0]["schedule_key"], "ongoing")
