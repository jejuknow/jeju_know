"""Admin workflow, access control, preservation and shared taxonomy regressions."""
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import create_app
from admin_ui import counters, admin_return
from admin_ui_migrations import VERSION, backup_before_admin_ui
from auth_security import hash_password
import test_community as helpers


class AdminUITest(unittest.TestCase):
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

    def test_all_admin_pages_share_layout_and_require_admin(self):
        paths = ['/admin','/admin/places','/admin/community','/admin/suggestions','/admin/new','/admin/picks','/admin/categories',
                 '/admin/issues','/admin/user-posts','/admin/best','/admin/best/new','/admin/users','/admin/places/new','/admin/news/new',
                 '/admin/settings/home','/admin/settings/general']
        paths += ['/admin/classifications/'+k for k in ('region','place','restaurant_subcategory','cafe_focus','cafe_feature','companion')]
        for path in paths:
            with self.subTest(path=path):
                result=self.admin.get(path)
                self.assertEqual(result.status_code,200,result.text[:100])
                self.assertIn('id="adminSidebar"',result.text)
                self.assertIn('id="adminContent"',result.text)
                self.assertNotIn('password_hash',result.text)
                for client in (self.member,self.visitor):
                    response=client.get(path,follow_redirects=False)
                    self.assertEqual(response.status_code,303)
                    self.assertIn('/admin/login',response.headers['location'])

    def test_new_mutations_keep_server_role_and_csrf_guards(self):
        for path in ('/admin/classifications/cafe_focus','/admin/settings/home','/admin/settings/general','/admin/best/new','/admin/best/1/delete'):
            for client in (self.member,self.visitor):
                self.assertEqual(self.post(client,path,action='new',value='denied',name='권한 없음').status_code,303)
            self.assertEqual(self.admin.post(path,data={}).status_code,400)
        self.assertFalse(self.rows('SELECT * FROM site_settings'))

    def test_dashboard_counts_and_member_status_tabs(self):
        p=self.place(name='사용자 미확인')
        other=self.place(name='확인한 사용자',duplicate_ack='1')
        self.execute("UPDATE places SET verification_status='jejuno_verified' WHERE id=?",(other['id'],))
        self.post(self.admin,'/admin/places/new',name='관리자 미확인',region='애월',category_id=self.category['cafe'])
        self.post(self.member,f"/places/{p['id']}/suggest",field='name',new_value='제안한 이름',reason='현장 확인')
        with closing(sqlite3.connect(self.database)) as c:
            self.assertEqual(counters(c)['unverified'],1)
            self.assertEqual(counters(c)['suggestions'],1)
        html=self.admin.get('/admin/community?status=all').text
        self.assertIn('사용자 미확인',html);self.assertIn('확인한 사용자',html);self.assertNotIn('관리자 미확인',html)
        html=self.admin.get('/admin/community?status=verified').text
        self.assertIn('확인한 사용자',html);self.assertNotIn('사용자 미확인',html)
        self.execute("UPDATE places SET verification_status='jejuno_verified' WHERE id=?",(p['id'],))
        with closing(sqlite3.connect(self.database)) as c: self.assertEqual(counters(c)['unverified'],0)

    def test_search_pagination_boundaries_and_filter_preservation(self):
        for i in range(32):
            self.execute("INSERT INTO places(name,slug,category,category_id,region,published,source_type) VALUES (?,?,?,?,?,1,'jejuno')",(f'카페 목록 {i:02}',f'listing-{i}','카페',self.category['cafe'],'애월'))
        page=self.admin.get('/admin/places?category=cafe&region=애월&per_page=10&page=2')
        self.assertEqual(page.status_code,200)
        self.assertEqual(page.text.count('data-row-select'),10)
        self.assertIn('category=cafe&amp;region=',page.text)
        self.assertIn('2 / 4 페이지',page.text)
        self.assertEqual(self.admin.get('/admin/places?per_page=50').text.count('data-row-select'),32)
        self.assertEqual(self.admin.get('/admin/places?per_page=30&page=999').text.count('data-row-select'),2)
        self.assertIn('조건에 맞는 장소가 없습니다',self.admin.get('/admin/places',params={'q':"' OR 1=1 --"}).text)
        self.assertIn('조건에 맞는 장소가 없습니다',self.admin.get('/admin/places?category=food').text)
        self.assertEqual(self.admin.get('/admin/places?search_by=name&q=목록+01').text.count('data-row-select'),1)

    def test_correction_current_original_proposed_and_stale_guard(self):
        p=self.place(name='이전 이름')
        self.post(self.member,f"/places/{p['id']}/suggest",field='name',new_value='제안 이름',reason='직접 확인')
        self.execute("UPDATE places SET name='현재 이름' WHERE id=?",(p['id'],))
        page=self.admin.get('/admin/suggestions')
        for text in ('이전 이름','현재 이름','제안 이름'): self.assertIn(text,page.text)
        sid=self.rows('SELECT id FROM place_correction_suggestions')[0]['id']
        self.assertEqual(self.post(self.admin,f'/admin/suggestions/{sid}/review',action='accept').status_code,409)
        self.assertEqual(self.post(self.admin,f'/admin/suggestions/{sid}/review',action='reject').status_code,303)
        self.assertNotIn('제안 이름',self.admin.get('/admin/suggestions').text)

    def test_new_year_and_monthly_pick_use_shared_places(self):
        a=self.place(name='올해 장소',opened_year=self.now.year)
        b=self.place(name='작년 장소',opened_year=self.now.year-1,duplicate_ack='1')
        self.assertIn('올해 장소',self.admin.get('/admin/new').text)
        self.assertNotIn('작년 장소',self.admin.get('/admin/new').text)
        self.assertIn('작년 장소',self.admin.get(f'/admin/new?year={self.now.year-1}').text)
        self.post(self.admin,'/admin/picks',place_id=a['id'],year=self.now.year,month=self.now.month,sort_order=2,pick_note='이달 설명',is_active='1')
        self.assertIn('이달 설명',self.admin.get('/admin/picks').text)
        self.post(self.admin,'/admin/picks',place_id=a['id'],year=self.now.year,month=self.now.month,sort_order=2,pick_note='보존 기록')
        self.assertFalse(self.api('?pick=current'))
        self.assertIn('보존 기록',self.admin.get('/admin/picks').text)

    def test_managed_focus_add_rename_disable_and_used_delete_guard(self):
        route='/admin/classifications/cafe_focus'
        self.assertEqual(self.post(self.admin,route,action='new',value='garden',name='정원 중심',sort_order=4,is_active='1').status_code,303)
        p=self.place()
        self.assertEqual(self.post(self.admin,f"/admin/places/{p['id']}/edit",**self.form(published='1',cafe_focus_present='1',cafe_focus=['garden'])).status_code,303)
        self.assertEqual(self.api('?category=cafe&focus=garden')[0]['id'],p['id'])
        self.assertEqual(self.post(self.admin,route,action='delete',value='garden').status_code,409)
        self.post(self.admin,route,action='edit',value='garden',name='정원 있는 카페',sort_order=4)
        self.assertEqual(self.api()[0]['cafe_focus_labels'],['정원 있는 카페'])
        self.assertEqual(self.api('?category=cafe&focus=garden'),[])
        self.assertEqual(self.post(self.member,f"/places/{p['id']}/edit",**self.form(name='기존 값 유지')).status_code,303)
        self.assertEqual(self.post(self.admin,'/admin/places/new',**self.form(name='신규 비활성 분류',cafe_focus_present='1',cafe_focus=['garden'])).status_code,400)

    def test_region_and_companion_management_preserves_stable_values(self):
        self.post(self.admin,'/admin/classifications/region',action='new',value='추자',name='추자도',sort_order=15,is_active='1')
        p=self.place(region='추자')
        self.assertEqual(self.api()[0]['region'],'추자')
        self.assertEqual(self.api()[0]['region_label'],'추자도')
        self.assertEqual(self.post(self.admin,'/admin/classifications/region',action='delete',value='추자').status_code,409)
        self.post(self.admin,'/admin/classifications/region',action='edit',value='추자',name='추자 지역',sort_order=15)
        self.assertNotIn('추자',self.visitor.get('/api/place-taxonomy').json()['regions'])
        self.assertEqual(self.post(self.member,f"/places/{p['id']}/edit",**self.form(region='추자')).status_code,303)
        self.post(self.admin,'/admin/classifications/companion',action='new',value='동료',name='동료',sort_order=8,is_active='1')
        self.assertIn('동료',self.visitor.get('/api/place-taxonomy').json()['companions'])

    def test_best_create_membership_copy_visibility_delete_preserves_places(self):
        p=self.place()
        result=self.post(self.admin,'/admin/best/new',title='새 모음',description='소개',sort_order=1,published='1')
        self.assertEqual(result.status_code,303)
        c=self.rows("SELECT * FROM best_collections WHERE title='새 모음'")[0]
        self.post(self.admin,f"/admin/best/{c['id']}/places",place_id=p['id'],sort_order=1,action='add')
        self.assertIn(p['name'],self.visitor.get('/best/'+c['slug']).text)
        self.assertEqual(self.post(self.admin,f"/admin/best/{c['id']}/copy").status_code,303)
        copied=self.rows("SELECT * FROM best_collections WHERE title LIKE '%(복사)'")[0]
        self.assertEqual(copied['published'],0)
        self.assertEqual(self.rows('SELECT count(*) AS n FROM best_collection_places WHERE collection_id=?',(copied['id'],))[0]['n'],1)
        self.post(self.admin,f"/admin/best/{c['id']}/toggle")
        self.assertEqual(self.visitor.get('/best/'+c['slug']).status_code,404)
        self.post(self.admin,f"/admin/best/{c['id']}/delete")
        self.assertIsNotNone(self.rows('SELECT deleted_at FROM best_collections WHERE id=?',(c['id'],))[0]['deleted_at'])
        self.assertEqual(self.rows('SELECT id FROM places')[0]['id'],p['id'])
        self.assertEqual(self.post(self.admin,f"/admin/best/{c['id']}/places",place_id=p['id'],action='add',sort_order=0).status_code,404)

    def test_settings_apply_without_disabling_public_routes(self):
        self.post(self.admin,'/admin/settings/home',home_new='1')
        html=self.visitor.get('/').text
        self.assertNotIn('id="pick"',html);self.assertNotIn('id="today"',html);self.assertNotIn('id="collections"',html)
        for route in ('/news','/best','/map?pick=current','/find'): self.assertEqual(self.visitor.get(route).status_code,200)
        self.post(self.admin,'/admin/settings/general',site_name='운영 이름',site_description='<script>alert(1)</script>',instagram_url='https://www.instagram.com/jeju_know/')
        html=self.visitor.get('/').text
        self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>alert(1)</script>',html)
        self.assertEqual(self.post(self.admin,'/admin/settings/general',site_name='사이트',instagram_url='javascript:alert(1)').status_code,400)

    def test_admin_counts_and_password_are_not_exposed_to_members(self):
        p=self.place()
        page=self.admin.get('/admin/users')
        self.assertNotIn(self.password_hash,page.text)
        self.assertIn('등록 장소',page.text)
        self.post(self.admin,'/admin/users/1/status',status='blocked')
        self.assertEqual(self.member.get('/account',follow_redirects=False).status_code,303)
        self.assertEqual(self.rows('SELECT id FROM places')[0]['id'],p['id'])

    def test_issue_filter_includes_legacy_categories_without_rewriting(self):
        self.execute("INSERT INTO news_posts(slug,title,category,published) VALUES ('legacy-event','기존 행사','행사·축제',1)")
        self.execute("INSERT INTO news_posts(slug,title,category,published) VALUES ('new-event','새 행사','행사•축제',1)")
        page=self.admin.get('/admin/issues',params={'category':'행사•축제'})
        self.assertEqual(page.status_code,200)
        self.assertIn('기존 행사',page.text); self.assertIn('새 행사',page.text)
        self.assertEqual(self.rows("SELECT category FROM news_posts WHERE title='기존 행사'")[0]['category'],'행사·축제')

    def test_admin_return_rejects_open_redirects(self):
        for value in ('https://evil.test/admin','//evil.test/admin','/admin\n/places','/elsewhere','http://[invalid'):
            self.assertEqual(admin_return({'next':value}),'/admin')
        self.assertEqual(admin_return({'next':'/admin/places?category=cafe&page=2'}),'/admin/places?category=cafe&page=2')

    def test_admin_migration_backup_integrity_preserves_previous_data(self):
        p=self.place()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('DELETE FROM schema_migrations WHERE version=?',(VERSION,))
            conn.execute('DROP TABLE site_settings')
            for column in ('image_url','updated_at','deleted_at'): conn.execute('ALTER TABLE best_collections DROP COLUMN '+column)
            previous=conn.execute('SELECT * FROM places').fetchall()
            previous_best=conn.execute('SELECT * FROM best_collections').fetchall()
        create_app(self.config)
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT * FROM places').fetchall(),previous)
            self.assertEqual(conn.execute('SELECT id,slug,title,description,emoji,sort_order,published FROM best_collections').fetchall(),previous_best)
            backup=conn.execute('SELECT backup_path FROM schema_migrations WHERE version=?',(VERSION,)).fetchone()[0]
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('SELECT * FROM places').fetchall(),previous)
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='site_settings'").fetchone())
        self.assertIsNone(backup_before_admin_ui(self.database))
        before=Path(self.database).read_bytes()
        with patch('app.backup_before_admin_ui',side_effect=OSError('backup failed')):
            with self.assertRaises(OSError): create_app(self.config)
        self.assertEqual(Path(self.database).read_bytes(),before)

