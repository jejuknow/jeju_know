import json
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import create_app
from auth_security import hash_password
from cafe_feature_migrations import FEATURES, VERSION, backup_before_cafe_features, migrate_cafe_features
import test_community as helpers


class CafeFeaturesTest(unittest.TestCase):
    setUp = helpers.CommunityTest.setUp
    tearDown = helpers.CommunityTest.tearDown
    rows = helpers.CommunityTest.rows
    execute = helpers.CommunityTest.execute
    csrf = helpers.CommunityTest.csrf
    post = helpers.CommunityTest.post
    form = helpers.CommunityTest.form
    api = helpers.CommunityTest.api

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password("community-test-secret")

    def term(self, key):
        return self.rows("SELECT * FROM categories WHERE slug=?", ("cafe-" + key,))[0]

    def create_cafe(self, keys=(), **extra):
        response = self.post(self.member, "/map/register", **self.form(
            cafe_features_present="1", cafe_feature_ids=[str(self.term(k)["id"]) for k in keys], **extra))
        self.assertEqual(response.status_code, 303, response.text[:500])
        return self.rows("SELECT * FROM places ORDER BY id DESC")[0]

    def test_catalog_single_source_and_form_permissions(self):
        config = self.visitor.get("/api/place-taxonomy").json()
        terms = [r for r in config["terms"] if r["type"] == "cafe_feature"]
        self.assertEqual([r["name"] for r in terms], [name for _, name in FEATURES])
        self.assertEqual(len([r for r in terms if r["member_policy"] == "review"]), 14)
        page = self.member.get("/map/register").text
        self.assertEqual(page.count('name="cafe_feature_ids"'), 14)
        self.assertIn("가성비도 제안할 수 있어요", page)
        self.assertNotIn("인기 핫플", page)
        self.assertNotIn('name="cafe_focus"', page)
        admin = self.admin.get("/admin/places/new").text
        for _, name in FEATURES:
            self.assertIn(name, admin)
        self.assertNotIn(">맛 중심", admin)
        self.assertIn("디저트·베이커리 특징", admin)

    def test_member_choices_search_menu_and_no_inference(self):
        p = self.create_cafe(["bakery", "dessert", "coffee"], signature_menu="소금빵", latitude="33.4", longitude="126.5")
        self.assertFalse(self.api("?category=cafe&detail=cafe-bakery"))
        suggestion = self.rows("SELECT * FROM place_correction_suggestions")[0]
        self.assertEqual(self.post(self.admin, f"/admin/suggestions/{suggestion['id']}/review", action="accept").status_code, 303)
        self.create_cafe([], name="메뉴만 있는 카페", address="다른 주소", signature_menu="빵")
        for slug in ("bakery", "dessert", "coffee"):
            result = self.api("?category=cafe&detail=cafe-" + slug)
            self.assertEqual([r["id"] for r in result], [p["id"]])
        self.assertEqual([r["id"] for r in self.api("?category=cafe&q=베이커리")], [p["id"]])
        self.assertEqual([r["id"] for r in self.api("?category=cafe&q=소금빵")], [p["id"]])
        self.assertEqual(self.rows("SELECT status FROM place_correction_suggestions")[0]["status"], "accepted")
        self.assertEqual(self.rows("SELECT count(*) AS n FROM categories WHERE type='cafe_feature' AND is_active=1")[0]["n"], 15)
        page = self.visitor.get("/place/" + p["slug"]).text
        for text in ("#베이커리", "#디저트 전문", "#커피 전문"):
            self.assertIn(text, page)

    def test_map_prefill_only_selects_direct_features(self):
        response = self.member.get('/map/register?category=cafe&detail=cafe-bakery,cafe-dessert,cafe-popular,cafe-value')
        selected = re.findall(r'name="cafe_feature_ids" value="(\d+)" checked', response.text)
        self.assertEqual(set(selected),
                         {str(self.term(k)['id']) for k in ('bakery', 'dessert', 'value')})
        self.assertNotIn('name="cafe_value_suggestion" value="1" checked', response.text)

    def test_maximum_and_forged_popular_or_review_blocked(self):
        keys = ["coffee", "dessert", "bakery", "view", "mood"]
        self.create_cafe(keys)
        for extras in (
            {"cafe_feature_ids": [str(self.term(k)["id"]) for k in keys + ["quiet"]]},
            {"cafe_feature_ids": [str(self.term("popular")["id"])]},
            {"cafe_feature_ids": [str(self.term("taste")["id"])]},
            {"cafe_feature_ids": [str(self.term(k)["id"]) for k in keys], "cafe_value_suggestion": "1"},
            {"details_enabled": "1", "term_ids": [str(self.term("popular")["id"])]},
        ):
            response = self.post(self.member, "/map/register", **self.form(cafe_features_present="1", **extras))
            self.assertEqual(response.status_code, 400, response.text[:300])
        self.assertEqual(len(self.rows("SELECT * FROM places")), 1)

    def test_value_requires_existing_admin_review_and_owner_checks(self):
        p = self.create_cafe(["bakery"], cafe_value_suggestion="1")
        self.assertFalse(self.api("?category=cafe&detail=cafe-value"))
        self.assertFalse(self.api("?category=cafe&detail=cafe-bakery"))
        suggestion = self.rows("SELECT * FROM place_correction_suggestions")[0]
        self.assertEqual(suggestion["status"], "pending")
        self.assertIn("가성비", self.admin.get("/admin/suggestions").text)
        path = f"/admin/suggestions/{suggestion['id']}/review"
        self.assertEqual(self.post(self.member, path, action="accept").status_code, 303)
        self.assertFalse(self.api("?category=cafe&detail=cafe-value"))
        self.assertEqual(self.post(self.admin, path, action="accept").status_code, 303)
        self.assertEqual(self.api("?category=cafe&detail=cafe-value")[0]["id"], p["id"])
        response = self.post(self.other, f"/places/{p['id']}/edit", **self.form(cafe_features_present="1"))
        self.assertEqual(response.status_code, 403)

    def test_admin_unlimited_and_legacy_preserved_hidden(self):
        p = self.create_cafe()
        legacy = self.term("taste")["id"]
        self.execute("INSERT INTO place_category_links VALUES (?,?)", (p["id"], legacy))
        self.execute("UPDATE places SET cafe_focus=?,features=? WHERE id=?", ('["taste"]', '["맛 중심"]', p["id"]))
        keys = [key for key, _ in FEATURES]
        response = self.post(self.admin, f"/admin/places/{p['id']}/edit", **self.form(
            slug=p["slug"], published="1", taxonomy_present="1",
            term_ids=[str(self.term(k)["id"]) for k in keys], dessert_note="직접 만든 빵과 시즌 디저트"))
        self.assertEqual(response.status_code, 303, response.text[:300])
        self.assertEqual(len(self.rows("SELECT * FROM place_category_links")), 16)
        detail = self.visitor.get("/place/" + p["slug"]).text
        self.assertNotIn("#맛 중심", detail)
        self.assertIn("직접 만든 빵과 시즌 디저트", detail)
        self.assertEqual(len(self.api()[0]["classification_terms"]), 15)
        self.assertNotIn("맛 중심", self.api()[0]["features_public"])
        self.assertIn("베이커리", self.api()[0]["features_public"])
        self.assertEqual(self.api()[0]["features"], ["맛 중심"])
        self.assertFalse(self.api("?category=cafe&detail=cafe-taste"))
        self.assertFalse(self.api("?category=cafe&focus=taste"))
        self.assertIn("기존 값 보존", self.admin.get("/admin/classifications/cafe_feature?per_page=50").text)
        # A member cannot remove an admin's evaluation or hidden legacy associations.
        response = self.post(self.member, f"/places/{p['id']}/edit", **self.form(cafe_features_present="1", cafe_feature_ids=[str(self.term("coffee")["id"])]))
        self.assertEqual(response.status_code, 303)
        linked = {r["category_id"] for r in self.rows("SELECT * FROM place_category_links")}
        self.assertEqual(linked, {legacy, *[self.term(k)["id"] for k in keys]})
        self.assertEqual(self.rows("SELECT cafe_focus FROM places")[0]["cafe_focus"], '["taste"]')

    def test_used_delete_and_inactive_feature_rejected(self):
        p = self.create_cafe(["bakery"])
        suggestion = self.rows("SELECT * FROM place_correction_suggestions")[0]
        self.post(self.admin, f"/admin/suggestions/{suggestion['id']}/review", action="accept")
        term = self.term("bakery")
        self.post(self.admin, f"/admin/categories/{term['id']}/delete")
        self.assertTrue(self.term("bakery"))
        self.post(self.admin, f"/admin/categories/{term['id']}/edit", name=term["name"], slug=term["slug"], sort_order="3")
        self.assertFalse(self.api("?category=cafe&detail=cafe-bakery"))
        response = self.post(self.member, "/map/register", **self.form(cafe_features_present="1", cafe_feature_ids=[str(term["id"])]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.rows("SELECT place_id FROM place_category_links")[0]["place_id"], p["id"])


class CafeMigrationTest(unittest.TestCase):
    def test_backup_rename_stable_ids_preservation_and_idempotence(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"JEJUNO_SKIP_SEED": "1"}):
            path = Path(folder) / "old.db"
            with patch("app.migrate_cafe_features"), patch("app.migrate_cafe_flow"):
                create_app({"TESTING": True, "DATABASE": str(path), "SECRET_KEY": "test"})
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("INSERT INTO categories(name,slug,type,parent_id,legacy_aliases) SELECT '디저트 좋은','old-dessert','cafe_feature',id,'[]' FROM categories WHERE slug='cafe'")
                dessert = conn.execute("SELECT id FROM categories WHERE slug='old-dessert'").fetchone()[0]
                taste = conn.execute("SELECT id FROM categories WHERE slug='cafe-taste'").fetchone()[0]
                cafe = conn.execute("SELECT id FROM categories WHERE slug='cafe'").fetchone()[0]
                conn.execute("INSERT INTO places(name,slug,region,category,category_id,cafe_focus,features) VALUES ('보존','keep','애월','카페',?,'[\"taste\"]','[\"오전\",\"낮\",\"노을\",\"저녁\"]')", (cafe,))
                place = conn.execute("SELECT id FROM places").fetchone()[0]
                conn.executemany("INSERT INTO place_category_links VALUES (?,?)", [(place, dessert), (place, taste)])
                before_places = conn.execute("SELECT * FROM places").fetchall()
                before_links = conn.execute("SELECT * FROM place_category_links").fetchall()
            backup = backup_before_cafe_features(path)
            migrate_cafe_features(path, backup)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("SELECT * FROM places").fetchall(), before_places)
                self.assertEqual(conn.execute("SELECT * FROM place_category_links").fetchall(), before_links)
                self.assertEqual(conn.execute("SELECT name,slug FROM categories WHERE id=?", (dessert,)).fetchone(), ("디저트 전문", "old-dessert"))
                self.assertEqual(conn.execute("SELECT is_active,is_legacy FROM categories WHERE id=?", (taste,)).fetchone(), (0, 1))
                self.assertEqual(conn.execute("SELECT count(*) FROM categories WHERE type='cafe_feature' AND is_active=1").fetchone()[0], 15)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                snapshot = conn.execute("SELECT * FROM categories").fetchall()
            self.assertIsNone(backup_before_cafe_features(path))
            migrate_cafe_features(path, None)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("SELECT * FROM categories").fetchall(), snapshot)
            with closing(sqlite3.connect(backup)) as conn:
                self.assertEqual(conn.execute("SELECT name FROM categories WHERE id=?", (dessert,)).fetchone()[0], "디저트 좋은")

    def test_backup_failure_stops_startup(self):
        with tempfile.TemporaryDirectory() as folder, patch("app.backup_before_cafe_features", side_effect=OSError("backup unavailable")):
            with self.assertRaises(OSError):
                create_app({"TESTING": True, "DATABASE": str(Path(folder) / "db"), "SECRET_KEY": "test"})
