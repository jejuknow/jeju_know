"""Multi-subtype contracts across real routes, CSV, review and shared search."""
import json
import sqlite3
import unittest
from contextlib import closing

import test_community as helpers
from auth_security import hash_password
from csv_place_import import HEADERS, validate_restaurant_row
import test_csv_import as csv_helpers

JAPANESE = ('food-pork-cutlet', 'food-udon', 'food-soba')
CHINESE = ('food-jajang', 'food-jjamppong', 'food-tangsuyuk')


class RestaurantMultiTest(unittest.TestCase):
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
        cls.password_hash = hash_password('community-test-secret')

    def term(self, slug):
        return self.rows('SELECT * FROM categories WHERE slug=?', (slug,))[0]['id']

    def food(self, major='food-japanese', children=JAPANESE, **extra):
        return self.form(category_id=str(self.category['food']), restaurant_taxonomy_present='1',
                         restaurant_main_id=str(self.term(major)),
                         restaurant_subtype_ids=[str(self.term(s)) for s in children], **extra)

    def create(self, admin=False, **extra):
        response = self.post(self.admin if admin else self.member, '/admin/places/new' if admin else '/map/register',
                             **self.food(**extra))
        self.assertEqual(response.status_code, 303, response.text[:500])
        return self.rows('SELECT * FROM places ORDER BY id DESC')[0]

    def links(self, place_id):
        return {r['category_id'] for r in self.rows('SELECT category_id FROM place_category_links WHERE place_id=?', (place_id,))}

    def test_member_japanese_and_chinese_registration_edit_and_api(self):
        for i, (major, children) in enumerate((('food-japanese', JAPANESE), ('food-chinese', CHINESE))):
            p = self.create(major=major, children=children, name=f'회원 다중 맛집 {i}', address=f'제주특별자치도 제주시 점검로 {i+1}')
            self.assertEqual(self.links(p['id']), {self.term(s) for s in children})
            self.assertEqual(p['published'], 1)
            self.assertIn(p['auto_check_status'], ('pass', 'review'))
            page = self.member.get(f"/places/{p['id']}/edit").text
            for slug in children:
                self.assertIn(f'value="{self.term(slug)}" checked', page)
                self.assertIn(p['id'], [r['id'] for r in self.api('?category=food&detail=' + slug)])
            entry = next(r for r in self.api() if r['id'] == p['id'])
            self.assertEqual([r['slug'] for r in entry['restaurant_subtypes']], list(children))
            self.assertEqual(len(entry['classification_terms']), 4)
            self.assertEqual(entry['restaurant_subtype_id'], '')
            self.assertIn(entry['restaurant_label'], self.visitor.get('/place/' + p['slug']).text)
            self.assertEqual(self.post(self.member, f"/places/{p['id']}/edit", **self.food(major, children[:1], name=p['name'], address=p['address'])).status_code, 303)
            self.assertEqual(self.links(p['id']), {self.term(children[0])})

    def test_admin_save_replace_reopen_and_signature_separation(self):
        p = self.create(admin=True, published='1', signature_menu='냉우동\n등심돈가스')
        page = self.admin.get(f"/admin/places/{p['id']}/edit")
        for slug in JAPANESE:
            self.assertIn(f'value="{self.term(slug)}" checked', page.text)
        self.assertIn('일식 · 돈가스 · 우동 · 소바', self.admin.get('/admin/places').text)
        self.assertIn('냉우동', self.visitor.get('/place/' + p['slug']).text)
        response = self.post(self.admin, f"/admin/places/{p['id']}/edit", **self.food('food-chinese', CHINESE, published='1'))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.links(p['id']), {self.term(s) for s in CHINESE})

    def test_server_rejects_four_wrong_parent_unknown_inactive_and_multiple_majors(self):
        cases = [dict(restaurant_subtype_ids=[str(self.term(s)) for s in (*JAPANESE, 'food-ramen')]),
                 dict(restaurant_subtype_ids=[str(self.term(s)) for s in (*JAPANESE[:2], 'food-jajang')]),
                 dict(restaurant_subtype_ids=['999999']),
                 dict(restaurant_main_id=[str(self.term('food-japanese')), str(self.term('food-chinese'))])]
        for client, path in ((self.member, '/map/register'), (self.admin, '/admin/places/new')):
            for change in cases:
                response = self.post(client, path, **{**self.food(), **change})
                self.assertEqual(response.status_code, 400, response.text[:300])
        self.execute('UPDATE categories SET is_active=0 WHERE id=?', (self.term('food-soba'),))
        self.assertEqual(self.post(self.member, '/map/register', **self.food()).status_code, 400)
        self.assertEqual(self.rows('SELECT id FROM places'), [])

    def test_legacy_generic_term_ids_cannot_bypass_limits(self):
        payload = self.food()
        for key in list(payload):
            if key.startswith('restaurant_'):
                del payload[key]
        for slugs in ((*JAPANESE, 'food-ramen'), ('food-udon', 'food-jajang')):
            r = self.post(self.admin, '/admin/places/new', **payload, taxonomy_present='1', term_ids=[str(self.term(s)) for s in slugs])
            self.assertEqual(r.status_code, 400)
        self.assertEqual(self.rows('SELECT id FROM places'), [])

    def test_dedupe_and_legacy_single_post_contract(self):
        p = self.create(children=('food-udon', 'food-udon', 'food-soba'))
        self.assertEqual(len(self.links(p['id'])), 2)
        data = self.food(children=())
        del data['restaurant_subtype_ids']
        data['restaurant_subtype_id'] = str(self.term('food-udon'))
        self.assertEqual(self.post(self.member, f"/places/{p['id']}/edit", **data).status_code, 303)
        self.assertEqual(self.api()[0]['restaurant_subtype_id'], str(self.term('food-udon')))

    def test_historical_four_links_survive_unrelated_edit(self):
        p = self.create(admin=True, published='1')
        self.execute('INSERT INTO place_category_links VALUES (?,?)', (p['id'], self.term('food-ramen')))
        before = self.links(p['id'])
        page = self.admin.get(f"/admin/places/{p['id']}/edit").text
        self.assertIn('기존 연결을 보존합니다', page)
        self.assertEqual(self.post(self.admin, f"/admin/places/{p['id']}/edit", **self.food(children=(), name='이름만 수정', published='1')).status_code, 303)
        self.assertEqual(self.links(p['id']), before)
        self.assertEqual(self.post(self.admin, f"/admin/places/{p['id']}/edit", **self.food(restaurant_replace='1', published='1')).status_code, 303)
        self.assertEqual(self.links(p['id']), {self.term(s) for s in JAPANESE})

    def test_or_filters_unique_cards_api_and_counts(self):
        a = self.create(children=('food-pork-cutlet', 'food-udon'), name='다중 맛집 A')
        b = self.create(children=('food-soba',), name='다중 맛집 B', address='제주특별자치도 제주시 검증로 999')
        for query in ('detail=food-udon,food-soba', 'cuisine=food-japanese&detail=food-pork-cutlet,food-udon,food-soba'):
            data = self.api('?category=food&' + query)
            self.assertEqual({r['id'] for r in data}, {a['id'], b['id']})
            self.assertEqual(len(data), 2)
            for path in ('/find?category=food&', '/map?category=food&'):
                self.assertEqual(self.visitor.get(path + query).status_code, 200)
        self.assertEqual(self.api('?category=food&cuisine=food-chinese&detail=food-udon'), [])
        self.assertEqual(len(self.api('?category=food&region=애월&detail=food-udon')), 1)

    def test_correction_is_reviewed_replacement_with_stale_guard(self):
        p = self.create(admin=True, children=('food-ramen',), published='1')
        data = dict(field='submission_details', reason='공식 메뉴판에서 확인했습니다', **self.food())
        self.assertIn('맛집 대분류·세부유형', self.member.get(f"/places/{p['id']}/suggest").text)
        r = self.post(self.member, f"/places/{p['id']}/suggest", **data)
        self.assertEqual(r.status_code, 303, r.text[:500])
        self.assertEqual(self.links(p['id']), {self.term('food-ramen')})
        suggestion = self.rows('SELECT * FROM place_correction_suggestions')[0]
        self.assertEqual(len(json.loads(suggestion['new_value'])['term_ids']), 3)
        page = self.admin.get(f"/admin/suggestions/{suggestion['id']}")
        for name in ('돈가스', '우동', '소바'):
            self.assertIn(name, page.text)
        r = self.post(self.admin, f"/admin/suggestions/{suggestion['id']}/review", action='accept')
        self.assertEqual(r.status_code, 303, r.text[:300])
        self.assertEqual(self.links(p['id']), {self.term(s) for s in JAPANESE})
        self.assertEqual(self.post(self.member, f"/places/{p['id']}/suggest", **data).status_code, 303)
        last = self.rows('SELECT * FROM place_correction_suggestions ORDER BY id DESC')[0]
        self.execute('DELETE FROM place_category_links WHERE place_id=? AND category_id=?', (p['id'], self.term('food-soba')))
        self.assertEqual(self.post(self.admin, f"/admin/suggestions/{last['id']}/review", action='accept').status_code, 409)

    def test_correction_rejects_invalid_and_admin_can_reject_without_change(self):
        p = self.create(admin=True, children=('food-ramen',), published='1')
        for children in ((*JAPANESE, 'food-ramen'), (*JAPANESE[:2], 'food-jajang')):
            r = self.post(self.member, f"/places/{p['id']}/suggest", field='submission_details', reason='메뉴판에서 확인했습니다', **self.food(children=children))
            self.assertEqual(r.status_code, 400)
        self.post(self.member, f"/places/{p['id']}/suggest", field='submission_details', reason='메뉴판에서 확인했습니다', **self.food())
        sid = self.rows('SELECT id FROM place_correction_suggestions')[0]['id']
        self.assertEqual(self.post(self.admin, f'/admin/suggestions/{sid}/review', action='reject').status_code, 303)
        self.assertEqual(self.links(p['id']), {self.term('food-ramen')})


class RestaurantMultiCsvTest(unittest.TestCase):
    setUp = csv_helpers.CsvImportTest.setUp
    tearDown = csv_helpers.CsvImportTest.tearDown
    rows = csv_helpers.CsvImportTest.rows
    execute = csv_helpers.CsvImportTest.execute
    csrf = csv_helpers.CsvImportTest.csrf
    row = csv_helpers.CsvImportTest.row
    upload = csv_helpers.CsvImportTest.upload
    preview = csv_helpers.CsvImportTest.preview
    commit = csv_helpers.CsvImportTest.commit

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('community-test-secret')

    def test_multi_csv_create_update_blank_and_preview(self):
        rows = [self.row('restaurant', 맛집_세부유형='돈가스 | 우동 | 소바', 공개여부='공개'),
                self.row('restaurant', 장소명='중식 복수유형', 주소='제주특별자치도 제주시 시험로 222', 맛집_대분류='중식', 맛집_세부유형='짜장면|짬뽕|탕수육', 공개여부='공개')]
        path, page = self.preview(rows, 'restaurant')
        self.assertIn('돈가스 · 우동 · 소바', page.text)
        self.assertIn('짜장면 · 짬뽕 · 탕수육', page.text)
        self.assertIn('등록 완료', self.commit(path, action_2='create', action_3='create').text)
        places = self.rows('SELECT * FROM places ORDER BY id')
        self.assertEqual(len(self.rows('SELECT * FROM place_category_links')), 6)
        p = places[0]
        raw = dict.fromkeys(HEADERS['restaurant'], '')
        raw.update(place_id=str(p['id']), 맛집_세부유형='우동||우동|소바')
        path, _ = self.preview([raw], 'restaurant')
        self.commit(path, action_2='update', target_2=str(p['id']))
        self.assertEqual(len(self.rows('SELECT * FROM place_category_links WHERE place_id=?', (p['id'],))), 2)
        raw['맛집_세부유형'] = ''
        raw['전화번호'] = '0641234567'
        before = self.rows('SELECT * FROM place_category_links ORDER BY place_id,category_id')
        path, _ = self.preview([raw], 'restaurant')
        self.commit(path, action_2='update', target_2=str(p['id']))
        self.assertEqual(self.rows('SELECT * FROM place_category_links ORDER BY place_id,category_id'), before)
        raw['맛집_대분류'] = '중식'
        path, page = self.preview([raw], 'restaurant')
        self.assertIn('기존 세부유형과 다른 대분류', page.text)

    def test_multi_csv_errors_and_tokens(self):
        cases = [('돈가스|우동|소바|라멘', '최대 3개'), ('돈가스|우동|짜장면', "세부유형 &#39;짜장면&#39;"),
                 ('돈가스|우동|제주특별면', '제주특별면'), ('짜장면|제주특별면', '제주특별면')]
        for raw, message in cases:
            path, page = self.preview([self.row('restaurant', 맛집_세부유형=raw)], 'restaurant')
            self.assertIn(message, page.text)
            self.commit(path, action_2='create')
        self.assertEqual(self.rows('SELECT id FROM places'), [])
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory = sqlite3.Row
            row = self.row('restaurant', 맛집_세부유형=' 우동 || 우동 | 소바 |')
            self.assertEqual(len(validate_restaurant_row(conn, row, self.category['food'], {})), 2)
