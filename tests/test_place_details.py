import io
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from PIL import Image
from auth_security import hash_password
import test_community
from place_detail_migrations import backup_before_place_detail_migration, migrate_place_details, VERSION
from place_details import DETAIL_FIELDS


class PlaceDetailsTest(unittest.TestCase):
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
    def setUpClass(cls):
        cls.password_hash = hash_password('community-test-secret')

    def admin_place(self, **changes):
        data = self.form(slug='details-test', published='1', **changes)
        response = self.post(self.admin, '/admin/places/new', **data)
        self.assertEqual(response.status_code, 303, response.text[:1500])
        return self.rows('SELECT * FROM places ORDER BY id DESC')[0]

    def test_admin_create_edit_and_public_detail(self):
        facts = dict(representative_price=5500,tamnaneunjeon_status='가능',phone='064-123-4567',business_hours='10:00 ~ 18:00', closed_days='매주 화요일', last_order='17:30',
                     signature_menu='필터커피\n천연발효빵', recommended_menu='시즌 필터커피\n오늘의 빵',
                     coffee_taste='밸런스형', dessert_note='직접 만든 빵', food_taste_note='',
                     reservation_status='제한', waiting_status='주말·성수기 있음', parking_status='가능',
                     note='골목 안쪽에 있습니다.\n야외 좌석은 날씨 영향을 받습니다.',
                     reason='직접 확인한 추천 이유', companions=['부모님','아이'], features=['로컬'],
                     checked_at='2020-03-04', info_source='공식 Instagram / 현장 확인')
        local = self.rows("SELECT id FROM categories WHERE slug='cafe-local'")[0]['id']
        p = self.admin_place(taxonomy_present='1',term_ids=[str(local)],**facts)
        # These fields are historical content now, not accepted by the admin form.
        legacy = ('recommended_menu', 'dessert_note', 'food_taste_note', 'reservation_status', 'waiting_status', 'reason')
        self.execute('UPDATE places SET ' + ','.join(f'{f}=?' for f in legacy) + ' WHERE id=?',
                     (*[facts[f] for f in legacy], p['id']))
        p = self.rows('SELECT * FROM places WHERE id=?', (p['id'],))[0]
        for field in DETAIL_FIELDS:
            self.assertEqual(p[field], facts[field])
        edit = self.admin.get(f"/admin/places/{p['id']}/edit")
        self.assertEqual(edit.status_code, 200)
        self.assertIn('2020-03-04', edit.text)
        page = self.visitor.get('/place/details-test')
        for text in ['한눈에 보기','10:00 ~ 18:00','매주 화요일','라스트오더 17:30','시즌 필터커피','오늘의 빵','JEJU_KNOW 추천 이유','부모님','제주다움','꿀팁','공식 Instagram / 현장 확인','2020-03-04','정보 바로잡기']:
            self.assertIn(text, page.text)
        order = ['장소 특징','잘 맞는 동행','한눈에 보기','이용 정보','꿀팁','기본 정보 및 정보 확인','이전에 기록한 추가 정보','JEJU_KNOW 추천 이유','대표 / 추천 메뉴']
        body = page.text.split('<main', 1)[1]
        self.assertEqual(sorted(body.index(t) for t in order), [body.index(t) for t in order])
        response = self.post(self.admin,f"/admin/places/{p['id']}/edit", **self.form(slug=p['slug'],published='1',business_hours='11:00 ~ 19:00'))
        self.assertEqual(response.status_code,303)
        updated=self.rows('SELECT * FROM places')[0]
        self.assertEqual(updated['business_hours'],'11:00 ~ 19:00')
        self.assertEqual(updated['signature_menu'],facts['signature_menu'])

    def test_category_specific_rendering_preserves_inactive_values(self):
        p=self.admin_place(signature_menu='대표 요리',recommended_menu='추천 요리',coffee_taste='산미 있음',dessert_note='디저트 설명',food_taste_note='담백한 편')
        self.execute('UPDATE places SET recommended_menu=?,dessert_note=?,food_taste_note=? WHERE id=?',
                     ('추천 요리', '디저트 설명', '담백한 편', p['id']))
        self.assertIn('디저트 설명',self.visitor.get('/place/details-test').text)
        self.assertNotIn('담백한 편',self.visitor.get('/place/details-test').text)
        for category in ('food','attraction','cafe'):
            response=self.post(self.admin,f"/admin/places/{p['id']}/edit",**self.form(category_id=self.category[category],slug=p['slug'],published='1'))
            self.assertEqual(response.status_code,303)
            page=self.visitor.get('/place/details-test').text
            self.assertEqual('대표 / 추천 메뉴' in page,category!='attraction')
            self.assertEqual('담백한 편' in page,category=='food')
            self.assertEqual('디저트 설명' in page,category=='cafe')
            self.assertEqual(self.rows('SELECT signature_menu FROM places')[0]['signature_menu'],'대표 요리')

    def test_unknown_facts_and_actual_checked_date_are_not_invented(self):
        p=self.place()
        self.execute("UPDATE places SET checked_at='',info_source='',parking_status='',pet_status='',verified_at='2026-10-13',created_at='2026-10-13' WHERE id=?",(p['id'],))
        page=self.visitor.get('/place/'+p['slug'])
        self.assertEqual(page.status_code,200)
        self.assertIn('<dt>최근 정보 확인</dt><dd>미확인</dd>',page.text)
        self.assertIn('<dt>정보 출처</dt><dd>미확인</dd>',page.text)
        self.assertIn('<span>반려동물</span><strong>미확인</strong>',page.text)
        self.assertNotIn('대표 / 추천 메뉴',page.text)
        self.assertNotIn('JEJU_KNOW 추천 이유',page.text)
        self.assertIn(p['emoji'],page.text)

    def test_validation_and_xss(self):
        for data in ({'business_hours':'가'*201},{'signature_menu':'가'*1001},{'coffee_taste':'invented'},{'checked_at':'2026-02-30'}):
            response=self.post(self.admin,'/admin/places/new',**self.form(**data))
            self.assertEqual(response.status_code,400,data)
        self.assertFalse(self.rows('SELECT id FROM places'))
        self.admin_place(signature_menu='<script>alert(1)</script>',business_hours="' OR 1=1 --")
        page=self.visitor.get('/place/details-test').text
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',page)
        self.assertNotIn('<script>alert(1)</script>',page)

    def test_member_cannot_write_admin_details(self):
        for field in ('reason','is_pick','verification_status','checked_at','reservation_status'):
            self.assertEqual(self.create(**{field:'forged'}).status_code,403)
        self.assertFalse(self.rows('SELECT id FROM places'))

    def test_specific_corrections_require_review_and_validate_choices(self):
        p=self.place()
        path=f"/places/{p['id']}/suggest"
        for field,value in [('business_hours','09:00 ~ 17:00'),('closed_days','매주 월요일'),('reservation_status','가능'),('parking_status','제한')]:
            before=self.rows('SELECT * FROM places')[0]
            self.assertEqual(self.post(self.member,path,field=field,new_value=value,reason='공식 안내에서 확인했습니다').status_code,303)
            self.assertEqual(self.rows('SELECT * FROM places')[0],before)
            sid=self.rows('SELECT id FROM place_correction_suggestions ORDER BY id DESC')[0]['id']
            self.assertEqual(self.post(self.admin,f'/admin/suggestions/{sid}/review',action='accept').status_code,303)
            self.assertEqual(self.rows('SELECT * FROM places')[0][field],value)
        self.assertEqual(self.post(self.member,path,field='reservation_status',new_value='마음대로',reason='공식 안내에서 확인했습니다').status_code,400)
        self.assertEqual(self.post(self.member,path,field='business_hours',new_value='가'*201,reason='공식 안내에서 확인했습니다').status_code,400)

    def test_existing_images_and_routes(self):
        buffer=io.BytesIO();Image.new('RGB',(80,60),'blue').save(buffer,format='PNG')
        response=self.admin.post('/admin/places/new',data={**self.form(slug='gallery-detail',published='1'), 'csrf_token':self.csrf(self.admin,'/admin')},files={'image_0':('cover.png',buffer.getvalue(),'image/png'),'image_1':('extra.png',buffer.getvalue(),'image/png')},follow_redirects=False)
        self.assertEqual(response.status_code,303)
        p=self.rows('SELECT * FROM places')[0]
        images=self.rows('SELECT * FROM images')
        self.assertEqual(len(images),2)
        page=self.visitor.get('/place/gallery-detail').text
        self.assertIn('대표사진 크게 보기',page)
        self.assertIn('추가사진 1',page)
        self.assertEqual(self.post(self.admin,f"/admin/places/{p['id']}/edit",**self.form(slug=p['slug'],published='1',business_hours='10시부터')).status_code,303)
        self.assertEqual(self.rows('SELECT * FROM images'),images)
        for path in ('/find','/new','/news','/map','/best','/api/places','/map?pick=current'):
            self.assertEqual(self.visitor.get(path).status_code,200,path)


class PlaceDetailMigrationTest(unittest.TestCase):
    def test_backup_additive_and_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'jejuno.db'
            with closing(sqlite3.connect(db)) as conn,conn:
                conn.execute('CREATE TABLE places(id INTEGER PRIMARY KEY,name TEXT,price_range TEXT,note TEXT,checked_at TEXT)')
                conn.execute("INSERT INTO places VALUES(1,'기존 장소','보존할 기존 값','기존 메모','2020-01-01')")
                conn.execute('CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,backup_path TEXT)')
            backup=backup_before_place_detail_migration(db)
            self.assertTrue(Path(backup).is_file())
            migrate_place_details(db,backup)
            migrate_place_details(db,backup)
            self.assertIsNone(backup_before_place_detail_migration(db))
            with closing(sqlite3.connect(db)) as conn, closing(sqlite3.connect(backup)) as old:
                self.assertEqual(conn.execute('SELECT id,name,price_range,note,checked_at FROM places').fetchall(),old.execute('SELECT * FROM places').fetchall())
                self.assertEqual(conn.execute('SELECT business_hours,reservation_status FROM places').fetchone(),('','미확인'))
                self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations WHERE version=?',(VERSION,)).fetchone()[0],1)
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
