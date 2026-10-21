"""Cafe features and visit advice stay private until an atomic admin review."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cafe_flow_migrations import VERSION, backup_before_cafe_flow, migrate_cafe_flow
import unittest
import test_cafe_features as helpers


class CafeFlowTest(unittest.TestCase):
    setUp = helpers.CafeFeaturesTest.setUp
    tearDown = helpers.CafeFeaturesTest.tearDown
    setUpClass = classmethod(helpers.CafeFeaturesTest.setUpClass.__func__)
    rows = helpers.CafeFeaturesTest.rows
    execute = helpers.CafeFeaturesTest.execute
    csrf = helpers.CafeFeaturesTest.csrf
    post = helpers.CafeFeaturesTest.post
    form = helpers.CafeFeaturesTest.form
    api = helpers.CafeFeaturesTest.api
    term = helpers.CafeFeaturesTest.term
    create_cafe = helpers.CafeFeaturesTest.create_cafe

    def pending(self):
        return self.rows("SELECT * FROM place_correction_suggestions ORDER BY id DESC")[0]

    def review(self, action="accept", client=None):
        return self.post(client or self.admin, f"/admin/suggestions/{self.pending()['id']}/review", action=action, next="/admin/community")

    def test_all_details_pending_then_approved_in_existing_queue(self):
        p = self.create_cafe(["coffee", "bakery", "value"], details_enabled="1",
            detail_cafe_visit_times=["오전", "노을"], detail_recommended_menu="검증 추천빵",
            detail_coffee_taste="산미 적음", detail_dessert_note="검증 베이커리 설명")
        stored = self.rows("SELECT * FROM places")[0]
        self.assertEqual(stored["cafe_visit_times"], "[]")
        self.assertEqual(stored["recommended_menu"], "")
        self.assertFalse(self.api("?category=cafe&detail=cafe-bakery"))
        self.assertNotIn("검증 추천빵", self.visitor.get("/place/" + p["slug"]).text)
        for route in ("/admin/community", "/admin/suggestions"):
            page = self.admin.get(route).text
            for value in ("베이커리", "가성비", "오전 / 노을", "검증 추천빵"):
                self.assertIn(value, page)
        self.assertIn("전체 승인", self.admin.get("/admin/community").text)
        response = self.review()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/community")
        stored = self.rows("SELECT * FROM places")[0]
        self.assertEqual(json.loads(stored["cafe_visit_times"]), ["오전", "노을"])
        self.assertEqual(stored["recommended_menu"], "검증 추천빵")
        page = self.visitor.get("/place/" + p["slug"]).text
        for value in ("#베이커리", "#가성비", "추천 시간대", "검증 추천빵"):
            self.assertIn(value, page)
        self.assertEqual(self.api("?category=cafe&detail=cafe-bakery")[0]["id"], p["id"])
        self.assertEqual(self.review().status_code, 409)

    def test_rejection_and_nonadmin_cannot_publish(self):
        p = self.create_cafe(["bakery"], details_enabled="1", detail_cafe_visit_times=["저녁"])
        self.assertEqual(self.review(client=self.other).status_code, 303)
        self.assertEqual(self.pending()["status"], "pending")
        self.assertEqual(self.review("reject").status_code, 303)
        self.assertEqual(self.pending()["status"], "rejected")
        self.assertFalse(self.rows("SELECT * FROM place_category_links"))
        self.assertEqual(self.rows("SELECT cafe_visit_times FROM places WHERE id=?", (p["id"],))[0]["cafe_visit_times"], "[]")

    def test_stale_detail_rejects_entire_batch(self):
        p = self.create_cafe(["coffee"], detail_recommended_menu="새 추천메뉴")
        self.execute("UPDATE places SET recommended_menu='관리자가 먼저 수정' WHERE id=?", (p["id"],))
        self.assertEqual(self.review().status_code, 409)
        self.assertFalse(self.rows("SELECT * FROM place_category_links"))
        self.assertEqual(self.pending()["status"], "pending")

    def test_time_toggle_validation_and_error_preservation(self):
        self.create_cafe(details_enabled="0", detail_cafe_visit_times=["노을"])
        self.assertFalse(self.rows("SELECT * FROM place_correction_suggestions"))
        for changes in ({"details_enabled":"1", "detail_cafe_visit_times":["심야"]},
                        {"detail_coffee_taste":"단맛"}, {"detail_dessert_note":"가" * 301}):
            response = self.post(self.member, "/map/register", **self.form(cafe_features_present="1", **changes))
            self.assertEqual(response.status_code, 400)
        response = self.post(self.member, "/map/register", **self.form(name="", cafe_features_present="1", details_enabled="1", detail_cafe_visit_times=["노을"], detail_recommended_menu="입력 유지"))
        self.assertEqual(response.status_code, 400)
        self.assertIn('value="노을" checked', response.text)
        self.assertIn("입력 유지", response.text)

    def test_admin_times_direct_clear_and_member_edit_preserves_official(self):
        p = self.create_cafe()
        response = self.post(self.admin, f"/admin/places/{p['id']}/edit", **self.form(slug=p["slug"], published="1", cafe_visit_times_present="1", cafe_visit_times=["오전", "낮", "노을", "저녁"]))
        self.assertEqual(response.status_code, 303, response.text)
        self.post(self.member, f"/places/{p['id']}/edit", **self.form(cafe_features_present="1"))
        self.assertEqual(len(json.loads(self.rows("SELECT cafe_visit_times FROM places")[0]["cafe_visit_times"])), 4)
        self.post(self.admin, f"/admin/places/{p['id']}/edit", **self.form(slug=p["slug"], published="1", cafe_visit_times_present="1"))
        self.assertEqual(self.rows("SELECT cafe_visit_times FROM places")[0]["cafe_visit_times"], "[]")

    def test_features_or_other_dimensions_and_and_pending_not_searchable(self):
        first = self.create_cafe(["bakery"], region="애월")
        self.review()
        second = self.create_cafe(["view"], name="다른 뷰 카페", address="별개 주소", region="구좌")
        self.review()
        self.create_cafe(["coffee"], name="미승인 카페", address="검수 주소")
        query = "?category=cafe&detail=cafe-bakery,cafe-view"
        self.assertEqual({r["id"] for r in self.api(query)}, {first["id"], second["id"]})
        self.assertEqual([r["id"] for r in self.api(query + "&region=애월")], [first["id"]])
        self.assertFalse(self.api("?category=cafe&detail=cafe-coffee"))
        page = self.admin.get("/admin/places").text
        self.assertIn("베이커리", page)
        find = self.visitor.get("/find").text
        self.assertLess(find.index('id="categoryDetails"'), find.index('id="companionChips"'))
        self.assertNotIn('name="cafe_visit_times"', find)

    def test_flow_migration_verified_backup_and_preservation(self):
        p = self.create_cafe(["bakery"])
        self.review()
        self.execute("DELETE FROM schema_migrations WHERE version=?", (VERSION,))
        self.execute("ALTER TABLE places DROP COLUMN cafe_visit_times")
        self.execute("UPDATE categories SET member_policy='direct' WHERE slug='cafe-bakery'")
        before = self.rows("SELECT * FROM places")
        links = self.rows("SELECT * FROM place_category_links")
        backup = backup_before_cafe_flow(self.database)
        self.assertTrue(Path(backup).is_file())
        self.assertIn("jejuno-before-cafe-flow-redesign-", backup)
        migrate_cafe_flow(self.database, backup)
        after = self.rows("SELECT * FROM places")
        self.assertEqual([{k:v for k,v in row.items() if k != "cafe_visit_times"} for row in after], before)
        self.assertEqual(links, self.rows("SELECT * FROM place_category_links"))
        self.assertEqual(self.term("bakery")["member_policy"], "review")
        self.assertEqual(self.term("popular")["member_policy"], "admin")
        self.assertIsNone(backup_before_cafe_flow(self.database))
        migrate_cafe_flow(self.database, None)
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertNotIn("cafe_visit_times", [r[1] for r in conn.execute("PRAGMA table_info(places)")])

    def test_backup_failure_stops_startup(self):
        with patch("app.backup_before_cafe_flow", side_effect=OSError("backup unavailable")):
            from app import create_app
            with self.assertRaises(OSError):
                create_app(self.config)
