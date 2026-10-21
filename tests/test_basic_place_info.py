import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from auth_security import hash_password
import test_community
from app import create_app
from basic_place_info_migrations import VERSION, backup_before_basic_place_info, migrate_basic_place_info
from place_details import phone_link


class BasicPlaceInfoTest(unittest.TestCase):
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

    def test_all_categories_share_optional_facts_and_keep_legacy_edits(self):
        for index, category in enumerate(('cafe', 'food', 'attraction')):
            with self.subTest(category=category):
                p = self.place(name=f'공통 기본정보 {index}', category_id=self.category[category],
                               duplicate_ack='1', business_hours='평일 09:00 ~ 18:00\n주말 10:00 ~ 19:00', phone='  064-123-4567  ')
                self.assertEqual(p['phone'], '064-123-4567')
                self.assertIn('\n', p['business_hours'])
                detail = self.visitor.get('/place/' + p['slug'])
                self.assertEqual(detail.status_code, 200)
                self.assertIn('href="tel:0641234567"', detail.text)
                self.assertIn(p['business_hours'], detail.text)
                for client, url in ((self.member, f"/places/{p['id']}/edit"), (self.admin, f"/admin/places/{p['id']}/edit")):
                    page = client.get(url)
                    self.assertEqual(page.status_code, 200)
                    self.assertIn(p['business_hours'], page.text)
                    self.assertIn('value="064-123-4567"', page.text)
                response = self.post(self.member, f"/places/{p['id']}/edit", **self.form(name=p['name'], category_id=p['category_id'], duplicate_ack='1'))
                self.assertEqual(response.status_code, 303)
                updated = self.rows('SELECT * FROM places WHERE id=?', (p['id'],))[0]
                self.assertEqual((updated['phone'], updated['business_hours']), (p['phone'], p['business_hours']))
                response = self.post(self.member, f"/places/{p['id']}/edit", **self.form(name=p['name'], category_id=p['category_id'], duplicate_ack='1', phone='', business_hours=''))
                self.assertEqual(response.status_code, 303)
                detail = self.visitor.get('/place/' + p['slug'])
                self.assertEqual(detail.status_code, 200)
                self.assertIn('<dt>☎ 전화</dt><dd>미확인</dd>', detail.text)
                self.assertNotIn('href="tel:', detail.text)

    def test_validation_is_loose_links_are_safe_and_errors_retain_input(self):
        p = self.place(phone='0641234567', business_hours='10시부터')
        for number, link in [('010-1234-5678', 'tel:01012345678'), ('+82 (64) 123-4567', 'tel:+82641234567'),
                             ('전화 문의: 064-123-4567', ''), ('<script>alert(1)</script>', ''), ('0641234567;ext=9', '')]:
            self.assertEqual(phone_link(number), link)
            response = self.post(self.member, f"/places/{p['id']}/edit", **self.form(phone=number))
            self.assertEqual(response.status_code, 303)
            detail = self.visitor.get('/place/' + p['slug']).text
            self.assertNotIn('<script>alert(1)</script>', detail)
            if link:
                self.assertIn('href="' + link + '"', detail)
            else:
                self.assertNotIn('href="tel:', detail)
        for changes in ({'phone': '1'*81}, {'business_hours': '가'*201}, {'address': ''}):
            before = self.rows('SELECT * FROM places')[0]
            response = self.post(self.member, f"/places/{p['id']}/edit", **self.form(phone='064-123-4567', business_hours='오전 열 시부터') | changes)
            self.assertEqual(response.status_code, 400)
            self.assertIn('오전 열 시부터' if 'business_hours' not in changes else changes['business_hours'], response.text)
            self.assertEqual(self.rows('SELECT * FROM places')[0], before)

    def test_admin_edit_and_member_image_upload_preserve_links_and_discovery(self):
        buffer = io.BytesIO()
        Image.new('RGB', (80, 60), 'blue').save(buffer, format='PNG')
        response = self.member.post('/map/register', data={**self.form(opened_year=self.now.year,opened_month=self.now.month, business_hours='매일 10:00 ~ 18:00', phone='01012345678', latitude='33.45', longitude='126.57'),
            'csrf_token': self.csrf(self.member)}, files={'image_0': ('cover.png', buffer.getvalue(), 'image/png')}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[:500])
        p = self.rows('SELECT * FROM places')[0]
        images = self.rows('SELECT * FROM images')
        self.assertEqual(len(images), 1)
        response = self.post(self.admin, f"/admin/places/{p['id']}/edit", **self.form(slug=p['slug'], published='1', phone='064-987-6543', business_hours='화요일 휴무\n11시부터', opened_year=self.now.year,opened_month=self.now.month, latitude='33.45', longitude='126.57'))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.rows('SELECT * FROM images'), images)
        page = self.visitor.get('/place/' + p['slug']).text
        self.assertIn('href="tel:0649876543"', page)
        self.assertIn('화요일 휴무\n11시부터', page)
        self.assertEqual(self.visitor.get('/uploads/' + images[0]['file_path']).status_code, 200)
        for path in ('/find', '/new', '/map', '/api/places', '/api/places?new=current'):
            response = self.visitor.get(path)
            self.assertEqual(response.status_code, 200, path)
            if path.startswith('/api/places'):
                self.assertIn(p['name'], response.text)

    def test_corrections_need_admin_review_and_keep_original_on_reject_or_conflict(self):
        p = self.place(business_hours='기존 시간', phone='0641234567')
        path = f"/places/{p['id']}/suggest"
        for field, value in [('business_hours', '09:00 ~ 17:00'), ('phone', '  010-1234-5678  ')]:
            before = self.rows('SELECT * FROM places')[0]
            self.assertEqual(self.post(self.member, path, field=field, new_value=value, reason='공식 안내에서 확인했습니다').status_code, 303)
            self.assertEqual(self.rows('SELECT * FROM places')[0], before)
            suggestion = self.rows('SELECT * FROM place_correction_suggestions ORDER BY id DESC')[0]
            sid = suggestion['id']
            self.assertIn(value.strip(), self.admin.get('/admin/suggestions').text)
            self.assertEqual(self.post(self.member, f'/admin/suggestions/{sid}/review', action='accept').status_code, 303)
            self.assertEqual(self.rows('SELECT * FROM places')[0], before)
            self.assertEqual(self.admin.post(f'/admin/suggestions/{sid}/review', data={'action':'accept'}).status_code, 400)
            self.assertEqual(self.post(self.admin, f'/admin/suggestions/{sid}/review', action='accept').status_code, 303)
            self.assertEqual(self.rows('SELECT * FROM places')[0][field], value.strip())
        for action in ('reject', 'accept'):
            self.assertEqual(self.post(self.member, path, field='phone', new_value='064-111-2222', reason='공식 안내에서 확인했습니다').status_code, 303)
            sid = self.rows('SELECT id FROM place_correction_suggestions ORDER BY id DESC')[0]['id']
            if action == 'accept':
                self.execute("UPDATE places SET phone='064-999-0000'")
            before = self.rows('SELECT * FROM places')[0]
            self.assertEqual(self.post(self.admin, f'/admin/suggestions/{sid}/review', action=action).status_code, 303 if action == 'reject' else 409)
            self.assertEqual(self.rows('SELECT * FROM places')[0], before)

    def test_focus_does_not_duplicate_feature_taxonomy(self):
        term = self.rows("SELECT id FROM categories WHERE name='커피 전문' AND type='cafe_feature'")[0]['id']
        p = self.place()
        self.execute('UPDATE places SET cafe_focus=?', (json.dumps(['taste','space']),))
        response = self.post(self.member, f"/places/{p['id']}/edit", **self.form(details_enabled='1', term_ids=[str(term)]))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(json.loads(self.rows('SELECT cafe_focus FROM places')[0]['cafe_focus']), ['taste','space'])
        self.assertEqual(self.rows('SELECT * FROM place_category_links'), [])
        sid = self.rows('SELECT id FROM place_correction_suggestions')[0]['id']
        self.assertEqual(self.post(self.admin, f'/admin/suggestions/{sid}/review',action='accept').status_code,303)
        self.assertEqual(self.rows('SELECT category_id FROM place_category_links')[0]['category_id'],term)


class BasicPlaceMigrationTest(unittest.TestCase):
    def test_online_backup_additive_idempotent_and_preserves_existing_phone(self):
        for existing_phone in (False, True):
            with self.subTest(existing_phone=existing_phone), tempfile.TemporaryDirectory() as folder:
                db = Path(folder) / 'jejuno.db'
                with closing(sqlite3.connect(db)) as conn, conn:
                    conn.execute('CREATE TABLE places(id INTEGER PRIMARY KEY,name TEXT,business_hours TEXT' + (',phone TEXT' if existing_phone else '') + ')')
                    conn.execute('INSERT INTO places VALUES(1,\'기존 장소\',\'기존 시간\'' + (",'064-123-4567'" if existing_phone else '') + ')')
                    conn.execute('CREATE TABLE images(id INTEGER PRIMARY KEY,place_id INTEGER REFERENCES places(id),file_path TEXT)')
                    conn.execute("INSERT INTO images VALUES(1,1,'existing.webp')")
                    conn.execute('CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,backup_path TEXT)')
                backup = backup_before_basic_place_info(db)
                migrate_basic_place_info(db, backup)
                migrate_basic_place_info(db, backup)
                self.assertIsNone(backup_before_basic_place_info(db))
                with closing(sqlite3.connect(db)) as conn, closing(sqlite3.connect(backup)) as old:
                    cols = ','.join(r[1] for r in old.execute('PRAGMA table_info(places)'))
                    self.assertEqual(conn.execute('SELECT ' + cols + ' FROM places').fetchall(), old.execute('SELECT * FROM places').fetchall())
                    self.assertEqual(conn.execute('SELECT * FROM images').fetchall(), old.execute('SELECT * FROM images').fetchall())
                    self.assertEqual(conn.execute('SELECT phone FROM places').fetchone()[0], '064-123-4567' if existing_phone else '')
                    self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                    self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations WHERE version=?', (VERSION,)).fetchone()[0], 1)

    def test_backup_failure_stops_app_before_database_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'jejuno.db'
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute('CREATE TABLE places(id INTEGER PRIMARY KEY,name TEXT)')
                conn.execute("INSERT INTO places VALUES(1,'보존할 장소')")
            before = db.read_bytes()
            with patch('app.backup_before_basic_place_info', side_effect=OSError('backup unavailable')):
                with self.assertRaises(OSError):
                    create_app({'TESTING':True, 'DATABASE':str(db), 'SECRET_KEY':'isolated-test'})
            self.assertEqual(db.read_bytes(), before)
