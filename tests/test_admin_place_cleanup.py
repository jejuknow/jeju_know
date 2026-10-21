"""Admin form, migration preservation, and calendar NEW boundary regressions."""
import json
import sqlite3
import unittest
from contextlib import closing
from datetime import date
from unittest.mock import patch

import test_community as helpers
from app import create_app
from auth_security import hash_password
from admin_place_cleanup_migrations import VERSION, backup_before_admin_place_cleanup, migrate_admin_place_cleanup
from legacy_images import add_fifth
from test_mobile_uploads import photo


class AdminPlaceCleanupTest(unittest.TestCase):
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

    def save(self, place, **data):
        return self.post(self.admin, f"/admin/places/{place['id']}/edit", **self.form(published='1', **data))

    def test_hidden_food_legacy_tags_preserved_with_subtypes(self):
        p = self.place(category_id=self.category['food'])
        tags = ['뷰', '감성', '이전 사용자 정의 태그']
        self.execute('UPDATE places SET features=? WHERE id=?', (json.dumps(tags), p['id']))
        term = self.rows("SELECT id FROM categories WHERE slug='food-korean'")[0]['id']
        self.execute('INSERT OR IGNORE INTO place_category_links VALUES (?,?)', (p['id'], term))
        for extra in ({}, {'legacy_features_present':'1', 'features':['다른 태그']}):
            response = self.save(p, category_id=self.category['food'], taxonomy_present='1', term_ids=[str(term)], **extra)
            self.assertEqual(response.status_code,303)
            self.assertEqual(json.loads(self.rows('SELECT features FROM places')[0]['features']),tags)
            self.assertEqual(self.rows('SELECT category_id FROM place_category_links'),[{'category_id':term}])
        # A failed save must also leave the original tags intact.
        self.assertEqual(self.save(p, category_id=self.category['food'], name='').status_code,400)
        self.assertEqual(json.loads(self.rows('SELECT features FROM places')[0]['features']),tags)

    def test_attraction_explicit_tag_edit_clear_and_omitted_preservation(self):
        p = self.place()
        for slug in ['attraction']:
            self.assertEqual(self.save(p,category_id=self.category[slug],legacy_features_present='1',features=['사진','뷰']).status_code,303)
            self.assertEqual(self.save(p,category_id=self.category[slug]).status_code,303)
            self.assertEqual(json.loads(self.rows('SELECT features FROM places')[0]['features']),['사진','뷰'])
            self.assertEqual(self.save(p,category_id=self.category[slug],legacy_features_present='1').status_code,303)
            self.assertEqual(json.loads(self.rows('SELECT features FROM places')[0]['features']),[])

    def test_cafe_hidden_legacy_tags_preserved_even_with_stale_inputs(self):
        p = self.place()
        self.execute('UPDATE places SET features=? WHERE id=?', (json.dumps(['사진','뷰']), p['id']))
        for extra in ({}, {'legacy_features_present':'1'}, {'legacy_features_present':'1','features':['힐링']}):
            self.assertEqual(self.save(p, **extra).status_code,303)
            self.assertEqual(json.loads(self.rows('SELECT features FROM places')[0]['features']),['사진','뷰'])

    def test_switch_to_food_preserves_saved_legacy_tags_not_hidden_draft(self):
        p = self.place()
        self.execute('UPDATE places SET features=? WHERE id=?',(json.dumps(['감성','뷰']),p['id']))
        self.assertEqual(self.save(p,category_id=self.category['food'],legacy_features_present='1',features=['삭제하면 안 됨']).status_code,303)
        self.assertEqual(json.loads(self.rows('SELECT features FROM places')[0]['features']),['감성','뷰'])
        self.assertEqual(self.save(p,category_id=self.category['cafe']).status_code,303)
        html = self.admin.get(f"/admin/places/{p['id']}/edit").text
        self.assertIn('data-legacy-tags data-place-categories="attraction"',html)
        self.assertIn('value="감성" checked',html)

    def test_category_emoji_server_authority_and_legacy_focus_preserved(self):
        p = self.place()
        self.execute('UPDATE places SET cafe_focus=?', (json.dumps(['space']),))
        for slug, expected in [('food','🍚'),('cafe','☕'),('attraction','📍')]:
            self.assertEqual(self.save(p, category_id=self.category[slug], emoji='wrong').status_code,303)
            updated = self.rows('SELECT * FROM places')[0]
            self.assertEqual(updated['emoji'],expected)
            self.assertEqual(json.loads(updated['cafe_focus']),['space'])

    def test_shared_visit_controls_round_trip_and_validation(self):
        p = self.place()
        values = dict(hours_present='1',hours_same='1',hours_start='09:30',hours_end='18:00',
                      closed_present='1',closed_mode='days',closed_weekdays=['화','월'],phone='0647992122',
                      signature_menu='커피',representative_price='5,500원',tamnaneunjeon_status='가능')
        self.assertEqual(self.save(p,**values).status_code,303)
        updated = self.rows('SELECT * FROM places')[0]
        self.assertEqual((updated['business_hours'],updated['closed_days'],updated['phone'],updated['representative_price']),
                         ('09:30 ~ 18:00','월요일 · 화요일','064-799-2122',5500))
        self.assertEqual((updated['signature_menu'],updated['tamnaneunjeon_status']),('커피','가능'))
        html = self.admin.get(f"/admin/places/{p['id']}/edit").text
        self.assertIn('value="09:30" selected',html)
        self.assertIn('value="화" checked',html)
        self.assertEqual(self.save(p,**{**values,'hours_start':'invalid'}).status_code,400)
        self.assertEqual(self.save(p,**{**values,'closed_weekdays':[]}).status_code,400)
        self.assertEqual(self.save(p,hours_present='1',hours_unknown='1',closed_present='1',closed_mode='everyday').status_code,303)
        self.assertEqual(self.rows('SELECT business_hours,closed_days FROM places')[0],{'business_hours':'','closed_days':'연중무휴'})
        self.assertEqual(self.save(p,closed_present='1',closed_mode='unknown').status_code,303)
        for field in ['parking_status','pet_status','child_status','accessible_status','reservation_status','waiting_status']:
            self.assertEqual(self.rows('SELECT * FROM places')[0][field],'미확인')

    def test_existing_free_text_not_reformatted(self):
        p = self.place(business_hours='평일 10시, 주말 11시 / 브레이크 타임',closed_days='둘째 화요일')
        response = self.save(p,hours_present='1',business_hours=p['business_hours'],closed_present='1',closed_mode='custom',closed_days=p['closed_days'])
        self.assertEqual(response.status_code,303)
        self.assertEqual(self.rows('SELECT business_hours,closed_days FROM places')[0],{k:p[k] for k in ['business_hours','closed_days']})

    def test_four_new_uploads_fifth_rejected_and_legacy_fifth_preserved(self):
        p = self.place()
        path = f"/admin/places/{p['id']}/edit"
        data = {**self.form(published='1'),'csrf_token':self.csrf(self.admin,'/admin')}
        files = [(f'image_{n}',('photo.jpg',photo(),'image/jpeg')) for n in range(5)]
        self.assertEqual(self.admin.post(path,data=data,files=files,follow_redirects=False).status_code,400)
        self.assertEqual(self.rows('SELECT * FROM images'),[])
        self.assertEqual(self.admin.post(path,data=data,files=files[:4],follow_redirects=False).status_code,303)
        add_fifth(self.app.state.image_store,self.database,p['id'])
        before = self.rows('SELECT * FROM images ORDER BY id')
        html = self.admin.get(path).text
        self.assertEqual(html.count('type="file"'),4)
        self.assertIn('기존 추가사진 1장 보존 중',html)
        self.assertEqual(self.save(p,name='이름 수정',remove_image_4='1').status_code,303)
        self.assertEqual(before,self.rows('SELECT * FROM images ORDER BY id'))
        self.assertTrue(all(self.visitor.get('/uploads/'+i['file_path']).status_code==200 for i in before))

    def test_verification_admin_only_and_two_options(self):
        p=self.place()
        path=f"/admin/places/{p['id']}/edit"
        self.assertNotIn('정보 확인됨',self.admin.get(path).text)
        self.assertNotIn('name="emoji"',self.admin.get(path).text)
        self.assertNotIn('name="cafe_focus"',self.admin.get(path).text)
        self.assertNotIn('name="cafe_focus"',self.member.get('/map/register').text)
        self.assertEqual(self.save(p,verification_status='community_confirmed').status_code,400)
        self.assertEqual(self.post(self.member,f"/places/{p['id']}/edit",**self.form(verification_status='jejuno_verified')).status_code,403)
        self.assertEqual(self.save(p,verification_status='jejuno_verified').status_code,303)
        self.assertIn('JEJU_KNOW 확인',self.api()[0]['status_label'])
        self.assertEqual(self.save(p,verification_status='unverified').status_code,303)
        self.assertEqual(self.rows('SELECT verification_status FROM places')[0]['verification_status'],'unverified')

    def test_calendar_six_month_window_cross_year_manual_unknown_and_admin(self):
        p=self.place()
        self.execute('UPDATE places SET opened_year=2026,opened_month=10')
        for day, included in [(date(2026,9,30),False),(date(2026,10,1),True),(date(2027,3,31),True),(date(2027,4,1),False)]:
            with self.subTest(day=day), patch('place_taxonomy.today',return_value=day), patch('new_visibility.today',return_value=day):
                self.assertEqual(bool(self.api('?new=current')),included)
                self.assertEqual(p['name'] in self.admin.get('/admin/new').text,included)
        with patch('place_taxonomy.today',return_value=date(2027,2,1)):
            self.execute('UPDATE places SET opened_month=NULL')
            self.assertEqual(self.api('?new=current'),[])
            self.execute("UPDATE places SET new_override='show'")
            self.assertEqual(len(self.api('?new=current')),1)
            self.execute("UPDATE places SET new_override='hide',opened_month=10")
            self.assertEqual(self.api('?new=current'),[])
            self.execute("UPDATE places SET new_override='show',category_id=?",(self.category['attraction'],))
            self.assertEqual(self.api('?new=current'),[])

    def test_exact_focus_migration_backup_idempotence_and_shared_features(self):
        p=self.place()
        raw=json.dumps(['taste','space','experience'])
        self.execute("UPDATE places SET cafe_focus=?,verification_status='community_confirmed'",(raw,))
        self.execute('DELETE FROM schema_migrations WHERE version=?',(VERSION,))
        before=self.rows('SELECT * FROM places')[0]
        backup=backup_before_admin_place_cleanup(self.database)
        migrate_admin_place_cleanup(self.database,backup)
        updated=self.rows('SELECT * FROM places')[0]
        self.assertEqual(updated['verification_status'],'unverified')
        self.assertEqual(updated['cafe_focus'],raw)
        for key in before.keys()-{'verification_status','verified_at','verified_by'}:
            self.assertEqual(updated[key],before[key],key)
        links=self.rows('SELECT * FROM place_category_links')
        self.assertEqual(len(links),2)
        self.assertEqual(self.api('?category=cafe&detail=cafe-taste'),[])
        self.assertEqual(self.api('?category=cafe&detail=cafe-unique')[0]['id'],p['id'])
        self.assertEqual(self.api('?category=cafe&detail=cafe-space'),[])
        terms=self.api()[0]['classification_terms']
        self.assertEqual({t['slug'] for t in terms},{'cafe-unique'})
        detail=self.visitor.get('/place/'+p['slug']).text
        self.assertNotIn('<h2>카페 성향</h2>',detail)
        self.assertIn('이색 경험',detail)
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute('SELECT verification_status,cafe_focus FROM places').fetchone(),('community_confirmed',raw))
        self.assertIsNone(backup_before_admin_place_cleanup(self.database))
        migrate_admin_place_cleanup(self.database,None)
        create_app(self.config)
        self.assertEqual(links,self.rows('SELECT * FROM place_category_links'))
