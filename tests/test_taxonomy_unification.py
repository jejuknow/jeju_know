"""Database edits propagate across submission, moderation, CSV and discovery."""
import re
import sqlite3
import unittest
from contextlib import closing

import test_community as community
import test_csv_import as csv_tests
from auth_security import hash_password
from csv_place_import import choice_lists


class TaxonomyUnificationTest(unittest.TestCase):
    setUp = community.CommunityTest.setUp
    tearDown = community.CommunityTest.tearDown
    rows = community.CommunityTest.rows
    execute = community.CommunityTest.execute
    csrf = community.CommunityTest.csrf
    post = community.CommunityTest.post
    form = community.CommunityTest.form
    api = community.CommunityTest.api
    row = csv_tests.CsvImportTest.row
    upload = csv_tests.CsvImportTest.upload
    preview = csv_tests.CsvImportTest.preview
    commit = csv_tests.CsvImportTest.commit

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('community-test-secret')

    def term(self, slug):
        return self.rows('SELECT * FROM categories WHERE slug=?', (slug,))[0]

    def choices(self):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory = sqlite3.Row
            return choice_lists(conn)

    def links(self, place_id):
        return {r['category_id'] for r in self.rows('SELECT category_id FROM place_category_links WHERE place_id=?', (place_id,))}

    def edit_term(self, slug, **changes):
        term = self.term(slug)
        data = {k: str(term[k]) for k in ('name', 'slug', 'parent_id', 'sort_order', 'is_active', 'member_policy')}
        response = self.post(self.admin, f"/admin/categories/{term['id']}/edit", **{**data, **changes})
        self.assertEqual(response.status_code, 303)

    def assert_choices(self, term, present=True):
        # Both FIND and MAP fetch this exact endpoint and share place-search.js.
        payload = self.visitor.get('/api/place-taxonomy').json()
        self.assertEqual(term['id'] in {r['id'] for r in payload['terms']}, present)
        for path in ('/find', '/map'):
            self.assertEqual(self.visitor.get(path).status_code, 200)
        for client, path in ((self.admin, '/admin/places/new'), (self.member, '/map/register')):
            page = client.get(path)
            if term['type'] == 'cafe_feature':
                name = 'term_ids' if client is self.admin else 'cafe_feature_ids'
                found = re.search(fr'name="{name}" value="{term["id"]}"', page.text) is not None
            else:
                select = re.search(r'<select name="restaurant_subtype_id".*?</select>', page.text, re.S)[0]
                found = re.search(fr'<option[^>]*value="{term["id"]}"[^>]*>', select) is not None
            self.assertEqual(found, present, path)
        key = '카페_특징' if term['type'] == 'cafe_feature' else '맛집_세부유형'
        self.assertEqual(term['name'] in self.choices()[key], present)

    def test_same_cafe_ids_through_admin_member_approval_and_csv(self):
        ids = [self.term('cafe-' + k)['id'] for k in ('coffee', 'bakery', 'view')]
        self.post(self.admin, '/admin/places/new', **self.form(name='관리자 카페', address='관리자 주소',
                  taxonomy_present='1', term_ids=list(map(str, ids)), published='1'))
        admin_place = self.rows('SELECT * FROM places ORDER BY id DESC')[0]
        result = self.post(self.member, '/map/register', **self.form(name='회원 카페', address='회원 주소',
                           cafe_features_present='1', cafe_feature_ids=list(map(str, ids))))
        self.assertEqual(result.status_code, 303)
        member_place = self.rows('SELECT * FROM places ORDER BY id DESC')[0]
        self.assertEqual(self.links(member_place['id']), set())
        suggestion = self.rows('SELECT * FROM place_correction_suggestions')[0]
        self.post(self.admin, f"/admin/suggestions/{suggestion['id']}/review", action='accept')
        path, _ = self.preview([self.row(장소명='CSV 카페', 카페_특징='커피 전문|베이커리|좋은 뷰', 공개여부='공개')])
        self.commit(path, action_2='create')
        csv_place = self.rows('SELECT * FROM places ORDER BY id DESC')[0]
        for place in (admin_place, member_place, csv_place):
            self.assertEqual(self.links(place['id']), set(ids))
            self.assertIn('#베이커리', self.visitor.get('/place/' + place['slug']).text)
        self.assertEqual(len(self.api('?category=cafe&detail=cafe-bakery')), 3)

    def test_same_food_ids_through_all_three_paths(self):
        main, child = self.term('food-japanese'), self.term('food-pork-cutlet')
        data = self.form(category_id=str(self.category['food']), restaurant_taxonomy_present='1',
                         restaurant_main_id=str(main['id']), restaurant_subtype_id=str(child['id']))
        for client, path, name in ((self.admin, '/admin/places/new', '관리자 맛집'), (self.member, '/map/register', '회원 맛집')):
            payload = {**data, 'name': name, 'address': name + ' 주소'}
            if client is self.admin:
                payload['published'] = '1'
            response = self.post(client, path, **payload)
            self.assertEqual(response.status_code, 303, response.text[:200])
        path, _ = self.preview([self.row('restaurant', 공개여부='공개')], 'restaurant')
        self.commit(path, action_2='create')
        places = self.rows('SELECT * FROM places')
        self.assertEqual(len(places), 3)
        for place in places:
            self.assertEqual(self.links(place['id']), {child['id']})
            self.assertIn('맛집 · 일식 · 돈가스', self.visitor.get('/place/' + place['slug']).text)
        self.assertEqual(len(self.api('?category=food&cuisine=food-japanese&detail=food-pork-cutlet')), 3)

    def test_added_renamed_and_disabled_terms_propagate_without_seed_fallback(self):
        for kind, name, slug, parent in (
            ('cafe_feature', '테스트특징', 'new-managed-cafe', self.category['cafe']),
            ('restaurant_subcategory', '테스트세부유형', 'new-managed-food', self.term('food-japanese')['id']),
        ):
            self.post(self.admin, '/admin/categories/new', type=kind, name=name, slug=slug,
                      parent_id=str(parent), sort_order='900', is_active='1')
            term = self.term(slug)
            self.assert_choices(term)
            values = {'카페_특징': name} if kind == 'cafe_feature' else {'맛집_세부유형': name}
            csv_kind = 'cafe' if kind == 'cafe_feature' else 'restaurant'
            path, page = self.preview([self.row(csv_kind, **values)], csv_kind)
            self.assertNotIn('활성 분류에 없습니다', page.text)
            self.edit_term(slug, name='변경된 ' + name)
            self.assertEqual(self.term(slug)['id'], term['id'])
            self.assert_choices(self.term(slug))
            self.assertNotIn(name, self.choices()['카페_특징' if kind == 'cafe_feature' else '맛집_세부유형'])
            self.edit_term(slug, is_active='0')
            self.assert_choices(self.term(slug), False)
            result = self.commit(path, action_2='create')
            self.assertNotIn('등록 완료', result.text)
            self.assertEqual(self.rows('SELECT * FROM places'), [])

    def test_disabled_ancestors_and_legacy_never_offer_new_choices(self):
        self.edit_term('food-japanese', is_active='0')
        child = self.term('food-pork-cutlet')
        self.assert_choices(child, False)
        response = self.post(self.member, '/map/register', **self.form(category_id=str(self.category['food']),
                    restaurant_main_id=str(child['parent_id']), restaurant_subtype_id=str(child['id'])))
        self.assertEqual(response.status_code, 400)
        self.edit_term('food-japanese', is_active='1')
        self.assert_choices(child)
        self.execute('UPDATE categories SET is_legacy=1 WHERE id=?', (child['parent_id'],))
        self.assert_choices(child, False)
        self.assertEqual(self.api('?category=food&detail=food-pork-cutlet'), [])

    def test_inactive_links_and_unrelated_fields_survive_save(self):
        term = self.term('cafe-bakery')
        path, _ = self.preview([self.row(공개여부='공개')]); self.commit(path, action_2='create')
        place = self.rows('SELECT * FROM places')[0]
        before = self.links(place['id'])
        self.edit_term('cafe-bakery', is_active='0')
        self.assert_choices(term, False)
        self.post(self.admin, f"/admin/places/{place['id']}/edit", **self.form(slug=place['slug'],
                  taxonomy_present='1', term_ids=[str(self.term('cafe-coffee')['id'])], published='1'))
        self.assertEqual(self.links(place['id']), before)
        self.assertEqual(self.api('?category=cafe&detail=cafe-bakery'), [])
        self.edit_term('cafe-bakery', is_active='1')
        self.assertEqual(len(self.api('?category=cafe&detail=cafe-bakery')), 1)

    def test_member_permission_is_managed_in_db_not_by_feature_name(self):
        term = self.term('cafe-bakery')
        self.edit_term('cafe-bakery', member_policy='admin')
        self.assertNotIn(f'name="cafe_feature_ids" value="{term["id"]}"', self.member.get('/map/register').text)
        self.assertIn(term['name'], self.choices()['카페_특징'])
        response = self.post(self.member, '/map/register', **self.form(cafe_features_present='1', cafe_feature_ids=[str(term['id'])]))
        self.assertEqual(response.status_code, 400)
        self.edit_term('cafe-bakery', member_policy='review')
        self.assert_choices(term)
        self.edit_term('cafe-bakery', member_policy='direct')
        self.assertEqual(self.term('cafe-bakery')['member_policy'], 'review')

    def test_member_tips_use_existing_review_and_note_column(self):
        response = self.post(self.member, '/map/register', **self.form(details_enabled='1', detail_note='뒤편 주차장을 이용하세요.'))
        self.assertEqual(response.status_code, 303)
        place = self.rows('SELECT * FROM places')[0]
        self.assertEqual(place['note'], '')
        suggestion = self.rows('SELECT * FROM place_correction_suggestions')[0]
        self.assertIn('꿀팁', self.admin.get('/admin/suggestions').text)
        self.post(self.admin, f"/admin/suggestions/{suggestion['id']}/review", action='accept')
        self.assertEqual(self.rows('SELECT note FROM places')[0]['note'], '뒤편 주차장을 이용하세요.')
        page = self.visitor.get('/place/' + place['slug']).text
        self.assertIn('꿀팁', page)
        self.assertIn('뒤편 주차장을 이용하세요.', page)
