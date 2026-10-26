"""Retired admin controls must never erase or rewrite historical content."""
import unittest

from auth_security import hash_password
import test_community as helpers


RETIRED = ('reservation_status', 'waiting_status', 'recommended_menu',
           'reason', 'dessert_note', 'food_taste_note')


class AdminFormSimplificationTest(unittest.TestCase):
    setUp = helpers.CommunityTest.setUp
    tearDown = helpers.CommunityTest.tearDown
    rows = helpers.CommunityTest.rows
    execute = helpers.CommunityTest.execute
    csrf = helpers.CommunityTest.csrf
    post = helpers.CommunityTest.post
    form = helpers.CommunityTest.form
    create = helpers.CommunityTest.create
    place = helpers.CommunityTest.place

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('community-test-secret')

    def test_new_edit_and_validation_forms_have_no_retired_controls(self):
        for slug in ('cafe', 'food', 'attraction'):
            p = self.place(category_id=self.category[slug], name='검증 장소 ' + slug, address='제주 ' + slug)
            responses = [self.admin.get('/admin/places/new'),
                         self.admin.get(f"/admin/places/{p['id']}/edit"),
                         self.post(self.admin, f"/admin/places/{p['id']}/edit",
                                   **self.form(category_id=self.category[slug], name=''))]
            self.assertEqual([r.status_code for r in responses], [200, 200, 400])
            for response in responses:
                for field in RETIRED:
                    self.assertNotIn(f'name="{field}"', response.text)
                for field in ('signature_menu', 'representative_price', 'coffee_taste',
                              'restaurant_main_id', 'restaurant_subtype_id', 'note', 'companions'):
                    self.assertIn(f'name="{field}"', response.text)
                self.assertEqual(response.text.count('name="signature_menu"'), 1)
                self.assertLess(response.text.index('name="signature_menu"'), response.text.index('3. 카테고리별 정보'))
                self.assertEqual(response.text.count('type="file"'), 4)

    def test_edit_excludes_retired_columns_even_for_stale_or_category_switch_posts(self):
        p = self.place()
        # Include whitespace, long historical text and values outside current choices.
        historical = dict(zip(RETIRED, ('옛 예약 안내', '예전 웨이팅 안내', '  기존 추천\n메뉴  ',
                                       '  기존 추천 이유  ', '디저트' * 200, '  담백한 맛  ')))
        self.execute('UPDATE places SET ' + ','.join(f'{f}=?' for f in RETIRED) + ' WHERE id=?',
                     (*historical.values(), p['id']))
        # A value-preserving UPDATE is still forbidden: these columns must be absent.
        self.execute('CREATE TRIGGER guard_retired BEFORE UPDATE OF ' + ','.join(RETIRED)
                     + " ON places BEGIN SELECT RAISE(ABORT, 'retired field rewritten'); END")
        for slug, extra in [('cafe', {}), ('food', {f: '' for f in RETIRED}),
                            ('attraction', {f: 'stale overwrite' for f in RETIRED})]:
            response = self.post(self.admin, f"/admin/places/{p['id']}/edit",
                                 **self.form(category_id=self.category[slug], name='바뀐 이름',
                                             published='1', slug=p['slug'], **extra))
            self.assertEqual(response.status_code, 303, response.text[:300])
            stored = self.rows('SELECT * FROM places WHERE id=?', (p['id'],))[0]
            self.assertEqual({f: stored[f] for f in RETIRED}, historical)
            self.assertEqual(stored['name'], '바뀐 이름')
        page = self.visitor.get('/place/' + p['slug']).text
        self.assertIn('기존 추천 이유', page)

    def test_new_cafe_uses_taxonomy_coffee_and_signature_menu_with_legacy_defaults(self):
        ids = [str(r['id']) for r in self.rows("SELECT id FROM categories WHERE slug IN ('cafe-coffee','cafe-bakery')")]
        response = self.post(self.admin, '/admin/places/new', **self.form(
            name='새 카페', taxonomy_present='1', term_ids=ids, coffee_taste='산미 적음',
            signature_menu='소금빵', representative_price='4,000', published='1',
            **{field: 'stale input ignored' for field in RETIRED}))
        self.assertEqual(response.status_code, 303, response.text[:300])
        p = self.rows('SELECT * FROM places')[0]
        self.assertEqual((p['signature_menu'], p['coffee_taste'], p['representative_price']), ('소금빵', '산미 적음', 4000))
        self.assertEqual({str(r['category_id']) for r in self.rows('SELECT * FROM place_category_links')}, set(ids))
        self.assertEqual({f: p[f] for f in RETIRED}, dict(zip(RETIRED, ('미확인', '미확인', '', '', '', ''))))

    def test_new_food_uses_existing_parent_child_ids(self):
        main = self.rows("SELECT id FROM categories WHERE slug='food-korean'")[0]['id']
        child = self.rows("SELECT id FROM categories WHERE slug='food-noodles'")[0]['id']
        response = self.post(self.admin, '/admin/places/new', **self.form(
            category_id=self.category['food'], name='새 맛집', restaurant_main_id=str(main),
            restaurant_subtype_id=str(child), signature_menu='고기국수', published='1'))
        self.assertEqual(response.status_code, 303, response.text[:300])
        p = self.rows('SELECT * FROM places')[0]
        self.assertEqual(p['signature_menu'], '고기국수')
        self.assertEqual({r['category_id'] for r in self.rows('SELECT * FROM place_category_links')}, {child})
        self.assertEqual(self.rows('SELECT parent_id FROM categories WHERE id=?', (child,))[0]['parent_id'], main)
        self.assertIn('국수·면', self.visitor.get('/place/' + p['slug']).text)
