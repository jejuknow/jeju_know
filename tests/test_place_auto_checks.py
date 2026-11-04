"""Real routes, isolated databases: publish boundaries, race safety and preservation."""
import asyncio
import io
import json
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch

from PIL import Image
from auth_security import hash_password
import test_community
from user_place_auto_publish_migration import migrate, VERSION


class AutoPublishTest(unittest.TestCase):
    setUp = test_community.CommunityTest.setUp
    tearDown = test_community.CommunityTest.tearDown
    rows = test_community.CommunityTest.rows
    execute = test_community.CommunityTest.execute
    csrf = test_community.CommunityTest.csrf
    post = test_community.CommunityTest.post
    form = test_community.CommunityTest.form
    create = test_community.CommunityTest.create
    place = test_community.CommunityTest.place
    api = test_community.CommunityTest.api
    pick = test_community.CommunityTest.pick

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('community-test-secret')

    def test_normal_photo_and_optional_unknowns(self):
        image = io.BytesIO(); Image.new('RGB',(80,80),'blue').save(image,'PNG')
        result = self.member.post('/map/register', data={**self.form(),'csrf_token':self.csrf(self.member)},
                                  files={'image_0':('photo.png',image.getvalue(),'image/png')}, follow_redirects=False)
        self.assertEqual(result.status_code,303,result.text)
        row = self.rows('SELECT * FROM places')[0]
        self.assertEqual((row['auto_check_status'], row['published'], row['verification_status']),('normal',1,'unverified'))
        self.assertEqual(self.api()[0]['status_label'],'사용자 등록')
        self.assertFalse(self.api()[0]['is_pick'])
        self.assertEqual(row['parking_status'],'미확인')
        self.assertEqual(row['business_hours'],'')
        self.assertEqual(self.rows('SELECT * FROM best_collection_places'),[])
        log = self.rows('SELECT * FROM admin_activity_logs')[-1]
        self.assertEqual(log['action'],'사용자 장소 자동 선공개')
        self.assertEqual(log['admin_id'],None)
        self.assertEqual(log['actor_member_id'],row['submitted_by_user_id'])

    def test_review_without_photo_and_near_similar(self):
        self.place(name='제주 바람 카페')
        p=self.place(name='제주 바람 카페점')
        self.assertEqual((p['auto_check_status'],p['published']),('review',1))
        self.assertEqual(set(json.loads(p['auto_check_reason'])),{'photo','similar'})

    def test_address_and_coordinate_boundary(self):
        for address in ('서울특별시 제주카페로 3','부산 제주특별자치도 제주시','제주특별자치도 서울로 1'):
            self.assertEqual(self.create(address=address).status_code,400)
        self.assertEqual(self.rows('SELECT * FROM places'),[])
        for lat,lng in (('',''),('37.56','126.97')):
            p=self.place(name='좌표 '+lat,latitude=lat,longitude=lng)
            self.assertEqual((p['auto_check_status'],p['published']),('hold',0))
            self.assertEqual(self.visitor.get('/place/'+p['slug']).status_code,404)
        self.assertEqual(self.api(),[])
        self.assertIn('위치 확인 필요',self.member.get('/account/places').text)
        self.assertNotIn('위치 확인 필요',self.other.get('/account/places').text)

    def test_exact_alias_and_private_duplicate(self):
        p=self.place(name='Cafe 바람')
        r=self.create(name='  CAFE  바람 ',address='제주 제주시 테스트로 123',duplicate_ack='1')
        self.assertEqual(r.status_code,409)
        self.assertIn('/place/'+p['slug'],r.text)
        self.execute('UPDATE places SET published=0')
        r=self.create(name='cafe바람')
        self.assertEqual(r.status_code,409)
        self.assertNotIn('/place/'+p['slug'],r.text)
        self.assertEqual(len(self.rows('SELECT * FROM places')),1)

    def test_unsafe_and_spam_hold_and_unsafe_url_rejected(self):
        for text in ('<script>alert(1)</script>','<img src=x onerror=alert(1)>','반복문구테스트'*7):
            p=self.place(name='안전 '+str(len(text)),one_line=text)
            self.assertEqual((p['auto_check_status'],p['published']),('hold',0))
        html=self.admin.get('/admin/community').text
        self.assertIn('공개 보류',html)
        self.assertEqual(self.api(),[])
        self.assertEqual(self.create(website_url='javascript:alert(1)').status_code,400)
        self.assertEqual(self.create(auto_check_status='normal').status_code,403)
        self.assertEqual(self.create(submitted_by_user_id='2').status_code,403)

    def test_pause_never_bulk_publishes_and_member_cannot_unhide(self):
        p=self.place()
        self.execute("UPDATE site_settings SET value='false' WHERE key='user_place_auto_publish'")
        held=self.place(name='중지 중 등록')
        self.assertEqual(held['published'],0)
        self.execute("UPDATE site_settings SET value='true' WHERE key='user_place_auto_publish'")
        self.assertEqual(self.rows('SELECT published FROM places WHERE id=?',(held['id'],))[0]['published'],0)
        self.assertEqual(self.post(self.member,f"/places/{held['id']}/edit",**self.form(name=held['name'])).status_code,303)
        self.assertEqual(self.rows('SELECT published FROM places WHERE id=?',(held['id'],))[0]['published'],0)
        self.assertEqual(self.api()[0]['id'],p['id'])

    def test_edit_rechecks_new_places_but_preserves_legacy_and_admin_hide(self):
        p=self.place()
        r=self.post(self.member,f"/places/{p['id']}/edit",**self.form(latitude='',longitude=''))
        self.assertEqual(r.status_code,303)
        self.assertTrue(r.headers['location'].startswith('/account/places'))
        self.assertEqual(self.api(),[])
        self.post(self.member,f"/places/{p['id']}/edit",**self.form())
        self.assertEqual(self.api(),[])  # Fixing coordinates does not undo a hold/hide.
        self.execute("UPDATE places SET auto_check_status=NULL,auto_check_reason=NULL,auto_checked_at=NULL,published=1")
        self.post(self.member,f"/places/{p['id']}/edit",**self.form(latitude='',longitude='',address='이전 주소'))
        self.assertEqual(len(self.api()),1)  # No retrospective policy changes to legacy places.

    def test_admin_queue_counts_sort_filters_and_settings(self):
        review=self.place(name='검사 확인 필요')
        hold=self.place(name='검사 공개 보류',latitude='',longitude='')
        page=self.admin.get('/admin/community').text
        self.assertLess(page.index(hold['name']),page.index(review['name']))
        self.assertIn('사진 미등록',page)
        self.assertIn('위치 확인 필요',page)
        filtered=self.admin.get('/admin/community?auto_check=hold').text
        self.assertIn(hold['name'],filtered);self.assertNotIn(review['name'],filtered)
        self.assertIn('장소 확인 우선',self.admin.get('/admin').text)
        self.assertIn('사용자 장소 자동 선공개',self.admin.get('/admin/settings/general').text)
        from admin_ui import counters
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(counters(conn)['auto'],{'normal':0,'review':1,'hold':1})
        before=self.rows('SELECT * FROM places')
        r=self.post(self.admin,'/admin/settings/general',site_name='JEJU_KNOW',site_description='',instagram_url='')
        self.assertEqual(r.status_code,303)
        self.assertEqual(self.rows('SELECT * FROM places'),before)
        self.assertEqual(self.rows("SELECT value FROM site_settings WHERE key='user_place_auto_publish'")[0]['value'],'false')

    def test_admin_verification_pick_and_hide(self):
        p=self.place()
        r=self.post(self.admin,f"/admin/places/{p['id']}/edit",**self.form(published='1',verification_status='jejuno_verified'))
        self.assertEqual(r.status_code,303,r.text)
        self.assertEqual(self.api()[0]['status_label'],'JEJU_KNOW 확인')
        self.assertEqual(self.pick(p).status_code,303)
        self.assertTrue(self.api()[0]['is_pick'])
        self.assertEqual(self.post(self.admin,f"/admin/places/{p['id']}/toggle-published").status_code,303)
        self.assertEqual(self.api(),[])

    def test_burst_limit(self):
        for n in range(10):
            self.assertEqual(self.create(name=f'속도 점검 {n}').status_code,303)
        self.assertEqual(self.create(name='열한번째').status_code,429)
        self.assertEqual(len(self.rows('SELECT * FROM places')),10)

    def test_double_submit_under_write_lock(self):
        # Both independent sessions pass the initial read before either begins its write.
        barrier=threading.Barrier(2)
        original=self.app.state.image_store.prepare
        async def synchronized(*args,**kwargs):
            plan=await original(*args,**kwargs)
            await asyncio.to_thread(barrier.wait,10)
            return plan
        token1=self.csrf(self.member);token2=self.csrf(self.other)
        def submit(pair):
            client,token=pair
            return client.post('/map/register',data={**self.form(),'csrf_token':token},follow_redirects=False).status_code
        with patch.object(self.app.state.image_store,'prepare',synchronized),ThreadPoolExecutor(2) as pool:
            results=list(pool.map(submit,[(self.member,token1),(self.other,token2)]))
        self.assertEqual(sorted(results),[303,409])
        self.assertEqual(len(self.rows('SELECT * FROM places')),1)

    def test_migration_backup_nullable_preserves_rows_and_flag_off(self):
        self.place()
        with closing(sqlite3.connect(self.database)) as conn,conn:
            conn.execute('DROP INDEX places_auto_check_queue')
            for key in ('auto_check_status','auto_check_reason','auto_checked_at'):
                conn.execute('ALTER TABLE places DROP COLUMN '+key)
            conn.execute('DELETE FROM schema_migrations WHERE version=?',(VERSION,))
            conn.execute("DELETE FROM site_settings WHERE key='user_place_auto_publish'")
            columns=[r[1] for r in conn.execute('PRAGMA table_info(places)')]
            before=conn.execute('SELECT * FROM places').fetchall()
        migrate(self.database);migrate(self.database)
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT '+','.join(columns)+' FROM places').fetchall(),before)
            self.assertEqual(conn.execute('SELECT auto_check_status,auto_check_reason,auto_checked_at FROM places').fetchone(),(None,None,None))
            self.assertEqual(conn.execute("SELECT value FROM site_settings WHERE key='user_place_auto_publish'").fetchone()[0],'false')
            path=conn.execute('SELECT backup_path FROM schema_migrations WHERE version=?',(VERSION,)).fetchone()[0]
        with closing(sqlite3.connect(path)) as backup:
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(backup.execute('SELECT * FROM places').fetchall(),before)
