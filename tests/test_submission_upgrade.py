"""Mobile facts, serial photo preparation, and atomic administrator review."""
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import test_community
from test_mobile_uploads import photo
from test_still_photos import encoded_photo
from auth_security import hash_password
from app import create_app
from image_uploads import optimize_image, ImageError
from image_limits import MAX_FILE_BYTES, FORM_BYTES
from submission_fields import controls
from submission_migrations import VERSION, backup_before_submission, migrate_submission


class SubmissionUpgradeTest(unittest.TestCase):
    setUp = test_community.CommunityTest.setUp
    tearDown = test_community.CommunityTest.tearDown
    rows = test_community.CommunityTest.rows
    execute = test_community.CommunityTest.execute
    csrf = test_community.CommunityTest.csrf
    post = test_community.CommunityTest.post
    form = test_community.CommunityTest.form
    create = test_community.CommunityTest.create
    place = test_community.CommunityTest.place

    @classmethod
    def setUpClass(cls): cls.password_hash = hash_password('community-test-secret')

    def details(self, **changes):
        term = self.rows("SELECT id FROM categories WHERE slug='cafe-view'")[0]['id']
        return dict(details_enabled='1', term_ids=[str(term)], detail_companions=['연인','친구'],
                    detail_parking_status='가능', detail_waiting_status='제한', **changes)

    def review(self, action='accept'):
        sid = self.rows('SELECT id FROM place_correction_suggestions ORDER BY id DESC')[0]['id']
        return self.post(self.admin, f'/admin/suggestions/{sid}/review', action=action)

    def test_hours_closed_phone_price_and_unknown(self):
        p = self.place(hours_present='1', hours_same='1', hours_start='10:00', hours_end='18:30',
                       closed_present='1', closed_mode='days', closed_weekdays=['화','월'], phone='0647992122',
                       signature_menu='아메리카노', representative_price='5,500원', tamnaneunjeon_status='가능')
        self.assertEqual((p['business_hours'], p['closed_days'], p['phone']), ('10:00 ~ 18:30', '월요일 · 화요일','064-799-2122'))
        self.assertEqual(p['representative_price'],5500)
        html = self.visitor.get('/place/'+p['slug']).text
        for value in ('5,500원','아메리카노','탐나는전','href="tel:0647992122"'): self.assertIn(value,html)
        self.assertEqual(controls(p)['hours_start'],'10:00')
        response = self.post(self.member,f"/places/{p['id']}/edit",**self.form(hours_present='1',hours_same='1',hours_unknown='1',closed_present='1',closed_mode='unknown'))
        self.assertEqual(response.status_code,303)
        updated = self.rows('SELECT * FROM places')[0]
        self.assertEqual((updated['business_hours'],updated['closed_days']),('',''))
        self.assertEqual(updated['signature_menu'],p['signature_menu'])
        self.assertEqual(updated['representative_price'],5500)

    def test_validation_and_old_text_round_trip(self):
        for change in ({'hours_present':'1','hours_same':'1','hours_start':'10:00'},
                       {'closed_present':'1','closed_mode':'days','closed_weekdays':['X']},
                       {'representative_price':'-3'}, {'representative_price':'1e8'},
                       {'tamnaneunjeon_status':'invented'}, {'signature_menu':'가'*1001}):
            self.assertEqual(self.create(**change).status_code,400,change)
        p=self.place(business_hours='평일 10시 / 주말 11시',closed_days='매월 둘째 주 수요일',signature_menu='커피\n차',representative_price='0')
        self.assertEqual(controls(p)['hours_same'],'')
        self.assertEqual(controls(p)['closed_mode'],'custom')
        self.assertEqual(self.post(self.member,f"/places/{p['id']}/edit",**self.form()).status_code,303)
        self.assertEqual(self.rows('SELECT * FROM places')[0]['signature_menu'],'커피\n차')
        self.assertIn('0원',self.visitor.get('/place/'+p['slug']).text)
        self.assertEqual(self.post(self.admin,f"/admin/places/{p['id']}/edit",**self.form(published='1',representative_price='bad')).status_code,400)

    def test_details_pending_then_atomic_approval_and_discovery(self):
        p=self.place(**self.details())
        self.assertEqual(json.loads(p['companions']),[])
        self.assertEqual(p['parking_status'],'미확인')
        self.assertFalse(self.rows('SELECT * FROM place_category_links'))
        self.assertEqual(self.visitor.get('/api/places?category=cafe&detail=cafe-view&companion=연인').json(),[])
        page=self.admin.get('/admin/suggestions').text
        for value in ('사용자 세부 정보','전체 승인','연인 / 친구','주차: 가능','웨이팅: 주말·성수기 있음'): self.assertIn(value,page)
        self.assertNotIn('submission_details',self.member.get(f"/places/{p['id']}/suggest").text)
        sid=self.rows('SELECT id FROM place_correction_suggestions')[0]['id']
        self.assertEqual(self.admin.post(f'/admin/suggestions/{sid}/review',data={'action':'accept'}).status_code,400)
        self.assertEqual(self.post(self.member,f'/admin/suggestions/{sid}/review',action='accept').status_code,303)
        self.assertEqual(self.rows('SELECT status FROM place_correction_suggestions')[0]['status'],'pending')
        self.assertEqual(self.review().status_code,303)
        updated=self.rows('SELECT * FROM places')[0]
        self.assertEqual(updated['verification_status'],'unverified')
        self.assertEqual(updated['parking_status'],'가능')
        self.assertEqual(updated['waiting_status'],'주말·성수기 있음')
        self.assertEqual(len(self.visitor.get('/api/places?category=cafe&detail=cafe-view&companion=연인').json()),1)
        self.assertIn('#연인',self.visitor.get('/place/'+p['slug']).text)
        self.assertEqual(self.review().status_code,409)

    def test_off_reject_stale_and_existing_official_data_preserved(self):
        p=self.place(**(self.details()|{'details_enabled':''}))
        self.assertFalse(self.rows('SELECT * FROM place_correction_suggestions'))
        self.execute("UPDATE places SET cafe_focus='[\"taste\"]',companions='[\"부모님\"]',child_status='가능'")
        path=f"/places/{p['id']}/edit"
        self.assertEqual(self.post(self.member,path,**self.form(**self.details())).status_code,303)
        self.assertEqual(self.review('reject').status_code,303)
        self.assertEqual(json.loads(self.rows('SELECT * FROM places')[0]['companions']),['부모님'])
        self.assertEqual(self.post(self.member,path,**self.form(**self.details())).status_code,303)
        self.execute("UPDATE places SET parking_status='불가'")
        before=self.rows('SELECT * FROM places')[0]
        self.assertEqual(self.review().status_code,409)
        self.assertEqual(self.rows('SELECT * FROM places')[0],before)
        self.assertEqual(self.post(self.member,path,**self.form()).status_code,303)
        self.assertEqual(self.rows('SELECT cafe_focus FROM places')[0]['cafe_focus'],'["taste"]')

    def test_details_tampering_and_empty_unknown_do_not_write(self):
        for extra in ({'detail_companions':['fake']},{'term_ids':['99999']},{'detail_parking_status':'fake'}):
            self.assertEqual(self.create(**(self.details()|extra)).status_code,400)
        for extra in ({'parking_status':'가능'},{'companions':['연인']},{'cafe_focus':['taste']}):
            self.assertEqual(self.create(**extra).status_code,403)
        p=self.place(details_enabled='1',detail_parking_status='미확인')
        self.assertFalse(self.rows('SELECT * FROM place_correction_suggestions'))
        html=self.member.get(f"/places/{p['id']}/edit").text
        self.assertNotIn('이 카페는 어떤 곳인가요?',html)
        self.assertIn('카페 특징 (선택)',html)
        self.assertIn('세부 정보도 알려줄게요',html)
        self.assertIn('accept="image/*"',html)

    def test_new_facts_corrections_preserve_until_admin_accept(self):
        p=self.place()
        for field,value in [('closed_days','화요일'),('signature_menu','커피'),('representative_price','5500'),('tamnaneunjeon_status','가능')]:
            before=self.rows('SELECT * FROM places')[0]
            result=self.post(self.member,f"/places/{p['id']}/suggest",field=field,new_value=value,reason='현장 안내판을 확인했습니다')
            self.assertEqual(result.status_code,303)
            self.assertEqual(before,self.rows('SELECT * FROM places')[0])
            self.assertEqual(self.review().status_code,303)
            self.assertEqual(str(self.rows('SELECT * FROM places')[0][field]),value)

    def test_prepare_four_formats_then_final_submission(self):
        optimized=[]
        for n,(fmt,name,mime) in enumerate([('HEIF','phone.heic','image/heic'),('JPEG','converted.HEIC','image/heic'),('PNG','no-extension','application/octet-stream'),('WEBP','android.webp','image/webp')]):
            response=self.member.post('/api/place-photos/prepare',data={'csrf_token':self.csrf(self.member)},files={'photo':(name,photo(fmt),mime)})
            self.assertEqual(response.status_code,200,response.text[:100] if response.status_code!=200 else '')
            self.assertEqual(response.headers['cache-control'],'no-store')
            with Image.open(io.BytesIO(response.content)) as image:
                self.assertEqual(image.format,'WEBP');self.assertFalse(image.getexif())
            optimized.append((f'image_{n}',('optimized-photo.webp',response.content,'image/webp')))
        self.assertFalse(self.rows('SELECT * FROM images'))
        r=self.member.post('/map/register',data={**self.form(),**self.details(),'csrf_token':self.csrf(self.member)},files=optimized,follow_redirects=False)
        self.assertEqual(r.status_code,303,r.text[:200])
        self.assertEqual(len(self.rows('SELECT * FROM images')),4)
        self.assertEqual(self.rows('SELECT status FROM place_correction_suggestions')[0]['status'],'pending')

    def test_prepare_auth_csrf_limit_corruption_and_no_orphans(self):
        endpoint='/api/place-photos/prepare'
        self.assertEqual(self.visitor.post(endpoint).status_code,401)
        self.assertEqual(self.member.post(endpoint,files={'photo':('x.jpg',photo(),'image/jpeg')}).status_code,400)
        r=self.member.post(endpoint,data={'csrf_token':self.csrf(self.member)},files={'photo':('x.jpg',b'\xff\xd8broken','image/jpeg')})
        self.assertEqual(r.status_code,400);self.assertIn('손상',r.json()['error'])
        self.assertEqual(self.member.post(endpoint,content=b'x',headers={'Content-Type':'multipart/form-data; boundary=t','Content-Length':str(MAX_FILE_BYTES+FORM_BYTES+1)}).status_code,413)
        with patch('community_routes.rate_limited',return_value=True):
            self.assertEqual(self.member.post(endpoint,data={'csrf_token':self.csrf(self.member)},files={'photo':('x.jpg',photo(),'image/jpeg')}).status_code,429)
        self.assertFalse(self.rows('SELECT * FROM images'))

    def test_mpo_prepare_and_save_while_real_animations_remain_blocked(self):
        endpoint='/api/place-photos/prepare'
        raw=encoded_photo('MPO',multiple=True)
        result=self.member.post(endpoint,data={'csrf_token':self.csrf(self.member)},files={'photo':('IMG_1234.JPG',raw,'image/jpeg')})
        self.assertEqual(result.status_code,200)
        with Image.open(io.BytesIO(result.content)) as image:
            self.assertEqual(image.format,'WEBP')
            self.assertEqual(image.n_frames,1)
        # Direct uploads (without browser JS) must use the same primary-image policy.
        saved=self.member.post('/map/register',data={**self.form(),'csrf_token':self.csrf(self.member)},files={'image_0':('IMG_1234.JPG',raw,'image/jpeg')},follow_redirects=False)
        self.assertEqual(saved.status_code,303,saved.text[:200])
        before=self.rows('SELECT * FROM images')
        self.assertEqual(len(before),1)
        for fmt in ('PNG','WEBP','GIF'):
            rejected=self.member.post(endpoint,data={'csrf_token':self.csrf(self.member)},files={'photo':('photo.jpg',encoded_photo(fmt,multiple=True,duration=100),'image/jpeg')})
            self.assertEqual(rejected.status_code,400)
            self.assertIn('애니메이션',rejected.json()['error'])
        self.assertEqual(before,self.rows('SELECT * FROM images'))


class SubmissionMigrationTest(unittest.TestCase):
    def test_verified_backup_additive_and_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'jejuno.db'
            with closing(sqlite3.connect(db)) as conn,conn:
                conn.execute('CREATE TABLE places(id INTEGER PRIMARY KEY,business_hours TEXT,signature_menu TEXT,cafe_focus TEXT)')
                conn.execute("INSERT INTO places VALUES(1,'기존 시간','기존 메뉴','[\"taste\"]')")
                conn.execute('CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,backup_path TEXT)')
            backup=backup_before_submission(db)
            migrate_submission(db,backup);migrate_submission(db,backup)
            with closing(sqlite3.connect(db)) as conn,closing(sqlite3.connect(backup)) as old:
                self.assertEqual(conn.execute('SELECT id,business_hours,signature_menu,cafe_focus FROM places').fetchall(),old.execute('SELECT * FROM places').fetchall())
                self.assertEqual(conn.execute('SELECT representative_price,tamnaneunjeon_status FROM places').fetchone(),(None,'미확인'))
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertIsNone(backup_before_submission(db))
            before=db.read_bytes()
            with patch('app.backup_before_submission',side_effect=OSError('no backup')):
                with self.assertRaises(OSError):create_app({'TESTING':True,'DATABASE':str(db),'SECRET_KEY':'isolated'})
            self.assertEqual(db.read_bytes(),before)
