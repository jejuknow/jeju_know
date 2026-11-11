"""Automatic taxonomy identifiers, deliberate overrides and shared-link safety."""
import unittest
import sqlite3
from contextlib import closing
from unittest.mock import patch

from auth_security import hash_password
from admin_restaurant_taxonomy import PATH
import test_community as helpers


class TaxonomySlugTest(unittest.TestCase):
    setUp = helpers.CommunityTest.setUp
    tearDown = helpers.CommunityTest.tearDown
    rows = helpers.CommunityTest.rows
    execute = helpers.CommunityTest.execute
    csrf = helpers.CommunityTest.csrf
    post = helpers.CommunityTest.post
    form = helpers.CommunityTest.form
    create = helpers.CommunityTest.create
    place = helpers.CommunityTest.place
    api = helpers.CommunityTest.api

    @classmethod
    def setUpClass(cls): cls.password_hash = hash_password('community-test-secret')

    def term(self, slug):
        return self.rows('SELECT * FROM categories WHERE slug=?',(slug,))[0]

    def new(self, name, parent=None, **extras):
        return self.post(self.admin,'/admin/categories/new',name=name,type='restaurant_subcategory',
                         parent_id=parent or self.category['food'],sort_order=20,is_active='1',next=PATH,**extras)

    def edit(self, original, **changes):
        return self.post(self.admin,f"/admin/categories/{original['id']}/edit",**{
            'name':original['name'],'type':'restaurant_subcategory','parent_id':original['parent_id'],
            'sort_order':original['sort_order'],'is_active':'1','next':PATH,**changes})

    def test_new_major_and_subtype_without_slug_and_accordion_return(self):
        for name,parent in [('새로운 대분류',self.category['food']),('새로운 세부유형',self.term('food-japanese')['id'])]:
            response=self.new(name,parent)
            row=self.rows('SELECT * FROM categories WHERE name=?',(name,))[0]
            self.assertRegex(row['slug'],r'^food-type-[a-f0-9]{12}$')
            self.assertEqual(row['parent_id'],parent)
            self.assertIn(f"#taxonomy-{row['id']}",response.headers['location'])
            self.assertEqual(self.admin.get(response.headers['location']).status_code,200)

    def test_known_names_reuse_map_and_suffix_without_overwriting(self):
        for slug,name,parent in [('food-korean','한식',self.category['food']),('food-udon','우동',self.term('food-japanese')['id'])]:
            original=self.term(slug)
            self.execute('UPDATE categories SET name=? WHERE id=?',('기존 '+name,original['id']))
            before=self.rows('SELECT * FROM categories WHERE id=?',(original['id'],))
            self.new(name,parent)
            row=self.term(slug+'-2')
            self.assertEqual(row['name'],name)
            self.assertEqual(self.rows('SELECT * FROM categories WHERE id=?',(original['id'],)),before)
        self.assertEqual(self.term('food')['slug'],'food')

    def test_unknown_name_collision_is_unique_and_no_romanization(self):
        with patch('taxonomy_slugs.uuid.uuid4') as uuid:
            uuid.return_value.hex='a'*32
            self.new('한글 유형 하나')
            self.new('한글 유형 둘')
        self.assertEqual(self.term('food-type-'+'a'*12)['name'],'한글 유형 하나')
        self.assertEqual(self.term('food-type-'+'a'*12+'-2')['name'],'한글 유형 둘')

    def test_name_edit_keeps_slug_links_urls_and_csv_names(self):
        old=self.term('food-udon');parent=self.term('food-japanese')
        place=self.place(category_id=self.category['food'],restaurant_main_id=parent['id'],
                         restaurant_subtype_ids=[str(old['id'])])
        links=self.rows('SELECT * FROM place_category_links WHERE place_id=?',(place['id'],))
        response=self.edit(old,name='우동·면')
        self.assertEqual(response.status_code,303)
        changed=self.term('food-udon');self.assertEqual(changed['id'],old['id'])
        self.assertEqual(changed['name'],'우동·면')
        self.assertEqual(self.rows('SELECT * FROM place_category_links WHERE place_id=?',(place['id'],)),links)
        self.assertEqual([p['id'] for p in self.api('?category=food&cuisine=food-japanese&detail=food-udon')],[place['id']])
        for route in ('/find','/map'):
            self.assertEqual(self.visitor.get(route+'?category=food&cuisine=food-japanese&detail=food-udon').status_code,200)
        api=self.visitor.get('/api/place-taxonomy').json()
        self.assertTrue(any(t['id']==old['id'] and t['slug']=='food-udon' and t['name']=='우동·면' for t in api['terms']))
        from csv_place_import import parse_csv, validate_restaurant_row
        exported=self.admin.get('/admin/imports/restaurant/export.csv')
        self.assertEqual(exported.status_code,200)
        raw=parse_csv(exported.content,'restaurant.csv','text/csv','restaurant')[0]['raw']
        self.assertEqual(raw['맛집_세부유형'],'우동·면')
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory=sqlite3.Row
            self.assertEqual(validate_restaurant_row(conn,raw,self.category['food'],place),[old['id']])
        # An old form posting an accidental replacement never implicitly enables it.
        self.edit(changed,slug='ignored-client-slug')
        self.assertEqual(self.term('food-udon')['name'],'우동·면')

    def test_explicit_override_needs_confirmation_valid_unique_slug_and_current_version(self):
        old=self.term('food-udon')
        for extra in [dict(slug='new-udon'),dict(slug='Food Udon!',slug_change_confirm='1'),
                      dict(slug='food-soba',slug_change_confirm='1'),dict(slug='',slug_change_confirm='1'),
                      dict(slug='new-udon',slug_change_confirm='1',original_slug='stale'),
                      dict(slug='a'*81,slug_change_confirm='1')]:
            payload=dict(slug_edit='1',original_slug=old['slug']);payload.update(extra)
            self.edit(old,**payload)
            self.assertEqual(self.term('food-udon'),old)
        self.edit(old,slug_edit='1',slug=' Food-Udon-Updated ',original_slug='food-udon',slug_change_confirm='1')
        self.assertEqual(self.term('food-udon-updated')['id'],old['id'])

    def test_new_manual_override_validation_and_duplicate_names_roll_back(self):
        before=self.rows('SELECT * FROM categories ORDER BY id')
        for value in ('Food Udon!','food-udon','a'*81,'한글','food--new'):
            self.new('검증 신규',slug=value)
        self.new('한식') # Sibling-name constraint still applies after auto-generation.
        self.assertEqual(self.rows('SELECT * FROM categories ORDER BY id'),before)
        self.new('수동 지정',slug=' Food-Manual-123 ')
        self.assertEqual(self.term('food-manual-123')['name'],'수동 지정')

    def test_new_auto_slug_is_selectable_and_links_survive_explicit_override(self):
        self.new('새 면요리',self.term('food-japanese')['id'])
        row=self.rows('SELECT * FROM categories WHERE name=?',('새 면요리',))[0]
        place=self.place(category_id=self.category['food'],restaurant_main_id=row['parent_id'],
                         restaurant_subtype_ids=[str(row['id'])])
        self.assertEqual([p['id'] for p in self.api('?category=food&detail='+row['slug'])],[place['id']])
        links=self.rows('SELECT * FROM place_category_links WHERE place_id=?',(place['id'],))
        self.edit(row,slug_edit='1',slug='food-special-noodle',original_slug=row['slug'],slug_change_confirm='1')
        self.assertEqual(self.rows('SELECT * FROM place_category_links WHERE place_id=?',(place['id'],)),links)
        self.assertEqual([p['id'] for p in self.api('?category=food&detail=food-special-noodle')],[place['id']])

    def test_advanced_ui_and_other_taxonomy_policy_unchanged(self):
        page=self.admin.get(PATH)
        self.assertEqual(page.status_code,200)
        form=page.text.split('id="add-child-7"')[1].split('</form>')[0]
        self.assertIn('고급 설정',form)
        self.assertNotRegex(form,r'name="slug"[^>]*required')
        self.assertNotIn('영문 소문자·숫자·하이픈으로 고유한 슬러그를 입력하세요.',page.text)
        self.assertIn('readonly data-slug-input',page.text)
        self.assertIn('슬러그를 변경하면 기존 필터·URL·CSV 연동에 영향을 줄 수 있습니다.',page.text)
        cafe=self.term('cafe-taste') if self.rows("SELECT id FROM categories WHERE slug='cafe-taste'") else self.rows("SELECT * FROM categories WHERE type='cafe_feature' LIMIT 1")[0]
        self.post(self.admin,f"/admin/categories/{cafe['id']}/edit",name=cafe['name'],slug='new-cafe-slug',
                  type='restaurant_subcategory',parent_id=self.category['cafe'],sort_order=1,is_active='1',
                  slug_edit='1',slug_change_confirm='1',original_slug=cafe['slug'])
        self.assertEqual(self.rows('SELECT slug FROM categories WHERE id=?',(cafe['id'],))[0]['slug'],cafe['slug'])
