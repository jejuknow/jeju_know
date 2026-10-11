import io
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from app import create_app
from auth_security import hash_password
from image_migrations import VERSION, backup_before_image_migration
from image_uploads import MAX_FILE_BYTES, ImagePlan


def picture(fmt='PNG', size=(120, 180), color='navy', exif=None):
    output = io.BytesIO()
    Image.new('RGB', size, color).save(output, fmt, **({'exif': exif} if exif else {}))
    return output.getvalue()


class ImageUploadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tmp.name) / 'jejuno.db')
        self.env = patch.dict(os.environ, {'JEJUNO_SKIP_SEED': '1'})
        self.env.start()
        self.config = {'TESTING': True, 'DATABASE': self.database, 'SECRET_KEY': 'image-tests'}
        self.app = create_app(self.config)
        self.store = self.app.state.image_store
        self.client = TestClient(self.app)
        self.admin = TestClient(self.app)
        self.visitor = TestClient(self.app)
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('INSERT INTO users(username,password_hash,nickname) VALUES (?,?,?)', ('uploader',hash_password('test-image-secret'),'사진 회원'))
            conn.execute('INSERT INTO admins(username,password_hash) VALUES (?,?)', ('admin',hash_password('test-image-secret')))
        self.client.post('/login', data={'csrf_token': self.csrf(self.client, '/login'), 'username':'uploader','password':'test-image-secret'})
        self.admin.post('/admin/login', data={'csrf_token': self.csrf(self.admin, '/admin/login'), 'username':'admin','password':'test-image-secret'})

    def tearDown(self):
        for client in (self.client, self.admin, self.visitor):
            client.close()
        self.env.stop()
        self.tmp.cleanup()

    def csrf(self, client, path):
        return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text).group(1)

    def rows(self, sql, args=()):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(sql,args)]

    def save_issue(self, files=None, issue_id=None, client=None, **changes):
        client = client or self.client
        url = f'/issues/{issue_id}/edit' if issue_id else '/issues/new'
        if client is self.admin:
            url = f'/admin/news/{issue_id}/edit' if issue_id else '/admin/news/new'
        data = {'csrf_token': self.csrf(client, url), 'title':'이미지 테스트 소식', 'category':'지역소식', 'published':'1'}
        data.update(changes)
        return client.post(url, data=data, files=files, follow_redirects=False)

    def save_place(self, files=None, place_id=None, **changes):
        url = f'/admin/places/{place_id}/edit' if place_id else '/admin/places/new'
        data = {'csrf_token': self.csrf(self.admin,url), 'name':'사진 장소', 'category':'카페','region':'애월','published':'1','is_pick':'1'}
        data.update(changes)
        return self.admin.post(url,data=data,files=files,follow_redirects=False)

    def file(self, name='poster.png', raw=None, mime='image/png'):
        return ('image_0',(name,raw if raw is not None else picture(),mime))

    def test_member_poster_upload_render_replace_remove_and_legacy_url(self):
        response = self.save_issue(files=[self.file()], image_url='https://example.com/legacy.jpg')
        self.assertEqual(response.status_code,303,response.text)
        post = self.rows('SELECT * FROM news_posts')[0]
        image = self.rows('SELECT * FROM images')[0]
        url = '/uploads/' + image['file_path']
        self.assertRegex(image['file_path'],r'^issues/[0-9a-f]{32}\.webp$')
        self.assertEqual((image['width'],image['height']), (120,180))
        self.assertEqual(post['image_url'],'https://example.com/legacy.jpg')
        for page in ('/','/news','/news/'+post['slug']):
            self.assertIn(url,self.visitor.get(page).text)
        served = self.visitor.get(url)
        self.assertEqual(served.status_code,200)
        self.assertEqual(served.headers['content-type'],'image/webp')
        self.assertEqual(served.headers['x-content-type-options'],'nosniff')
        self.assertIn('https://example.com/legacy.jpg',self.client.get(f"/issues/{post['id']}/edit").text)
        self.assertEqual(self.save_issue(issue_id=post['id'],files=[self.file(raw=picture(color='green'))]).status_code,303)
        self.assertFalse(self.store.path(image['file_path']).exists())
        replacement = self.rows('SELECT * FROM images')[0]
        self.assertNotEqual(image['file_path'],replacement['file_path'])
        self.assertEqual(self.save_issue(issue_id=post['id'],remove_image_0='1').status_code,303)
        self.assertEqual(self.rows('SELECT * FROM images'),[])
        self.assertFalse(self.store.path(replacement['file_path']).exists())
        self.assertIn('https://example.com/legacy.jpg',self.visitor.get('/news/'+post['slug']).text)

    def test_place_five_images_gallery_primary_pick_and_restart(self):
        files = [(f'image_{n}',(f'photo{n}.png',picture(color='green'),'image/png')) for n in range(5)]
        self.assertEqual(self.save_place(files=files).status_code,303)
        place = self.rows('SELECT * FROM places')[0]
        images = self.rows('SELECT * FROM images ORDER BY sort_order')
        self.assertEqual([r['sort_order'] for r in images],list(range(5)))
        result = self.visitor.get('/api/places').json()[0]
        self.assertEqual(result['image_url'],'/uploads/'+images[0]['file_path'])
        self.assertTrue(result['is_pick'])
        html = self.visitor.get('/place/'+place['slug']).text
        self.assertIn('place-gallery',html)
        for image in images:
            self.assertIn('/uploads/'+image['file_path'],html)
        restarted = create_app(self.config)
        with TestClient(restarted) as client:
            self.assertEqual(client.get(result['image_url']).status_code,200)
            self.assertEqual(len(client.get('/api/places').json()[0]['images']),5)
        self.assertEqual(self.save_place(place_id=place['id'],remove_image_2='1',files=[self.file()]).status_code,303)
        self.assertEqual([r['sort_order'] for r in self.rows('SELECT * FROM images ORDER BY sort_order')],[0,1,3,4])
        self.assertFalse(self.store.path(images[0]['file_path']).exists())
        self.assertFalse(self.store.path(images[2]['file_path']).exists())

    def test_limits_and_invalid_uploads_do_not_create_content_or_files(self):
        cases = [self.file('evil.svg',b'<svg/>','image/svg+xml'),
                 self.file('evil.html',b'<html/>','text/html'),
                 self.file('evil.js',b'alert(1)','text/javascript'),
                 self.file('evil.exe',b'MZ','application/octet-stream'),
                 self.file('fake.png',b'not an image'),
                 self.file('fake.jpg',picture(),'image/jpeg'),
                 self.file('wrong.png',picture(),'text/html'),
                 self.file('huge.png',b'x'*(MAX_FILE_BYTES+1))]
        for upload in cases:
            with self.subTest(filename=upload[1][0]):
                response = self.save_issue(files=[upload])
                self.assertIn(response.status_code,(400,413),response.text)
                self.assertEqual(self.rows('SELECT * FROM news_posts'),[])
                self.assertEqual(self.rows('SELECT * FROM images'),[])
                self.assertEqual(list(self.store.root.rglob('*.webp')),[])
        files = [(f'image_{n}',(f'p{n}.png',picture(),'image/png')) for n in range(6)]
        self.assertEqual(self.save_place(files=files).status_code,400)
        self.assertEqual(self.rows('SELECT * FROM places'),[])

    def test_atomic_multiple_upload_failure_preserves_existing_images(self):
        self.save_place(files=[self.file()])
        before = self.rows('SELECT * FROM images')
        place = self.rows('SELECT * FROM places')[0]
        files = [self.file(raw=picture(color='red')),('image_1',('bad.png',b'broken','image/png'))]
        self.assertEqual(self.save_place(place_id=place['id'],files=files,remove_image_0='1').status_code,400)
        self.assertEqual(before,self.rows('SELECT * FROM images'))
        self.assertEqual(len(list(self.store.root.rglob('*.webp'))),1)

    def test_exact_ten_megabyte_and_webp_inputs_are_safely_reencoded(self):
        raw=picture()
        raw += b'\x00' * (MAX_FILE_BYTES-len(raw))
        self.assertEqual(self.save_issue(files=[self.file(raw=raw)]).status_code,303)
        self.assertLess(self.rows('SELECT byte_size FROM images')[0]['byte_size'],MAX_FILE_BYTES)
        self.assertEqual(self.save_issue(files=[self.file('image.webp',picture('WEBP'),'image/webp')]).status_code,303)
        self.assertEqual(len(self.rows('SELECT * FROM images')),2)

    def test_file_cleanup_retries_after_temporary_filesystem_error(self):
        self.save_place(files=[self.file()])
        image=self.rows('SELECT * FROM images')[0]
        original=self.store.path(image['file_path'])
        with patch.object(Path,'unlink',side_effect=PermissionError('temporary lock')):
            self.assertEqual(self.save_place(place_id=image['place_id'],remove_image_0='1').status_code,303)
        self.assertTrue(original.exists())
        self.assertEqual(len(self.rows('SELECT * FROM image_file_gc')),1)
        self.store.collect()
        self.assertFalse(original.exists())
        self.assertEqual(self.rows('SELECT * FROM image_file_gc'),[])

    def test_exif_orientation_resize_metadata_strip_and_safe_filename(self):
        exif = Image.Exif(); exif[274]=6; exif[270]='private GPS and camera metadata'
        raw = picture('JPEG',(2400,1200),exif=exif)
        self.assertEqual(self.save_issue(files=[self.file('../../private.jpg',raw,'image/jpeg')]).status_code,303)
        image = self.rows('SELECT * FROM images')[0]
        self.assertEqual((image['width'],image['height']),(960,1920))
        with Image.open(self.store.path(image['file_path'])) as decoded:
            self.assertEqual(decoded.format,'WEBP')
            self.assertFalse(decoded.getexif())
            self.assertFalse(any(k in decoded.info for k in ('exif','xmp','icc_profile')))
        self.assertNotIn('private',image['file_path'])
        self.assertLess(image['byte_size'],len(raw))
        with patch('image_uploads.MAX_PIXELS',100):
            self.assertEqual(self.save_issue(files=[self.file()]).status_code,400)

    def test_owner_csrf_admin_moderation_and_private_image_access(self):
        self.save_issue(files=[self.file()])
        post = self.rows('SELECT * FROM news_posts')[0]
        url = '/uploads/' + self.rows('SELECT * FROM images')[0]['file_path']
        with closing(sqlite3.connect(self.database)) as conn,conn:
            conn.execute('INSERT INTO users(username,password_hash,nickname) VALUES (?,?,?)',('stranger',hash_password('stranger-password'),'다른 회원'))
        self.visitor.post('/login',data={'csrf_token':self.csrf(self.visitor,'/login'),'username':'stranger','password':'stranger-password'})
        token = self.csrf(self.visitor,'/account')
        denied = self.visitor.post(f"/issues/{post['id']}/edit",data={'csrf_token':token,'remove_image_0':'1'},files=[self.file()])
        self.assertEqual(denied.status_code,403)
        self.assertEqual(self.client.post(f"/issues/{post['id']}/edit",data={'title':'CSRF'},files=[self.file()]).status_code,400)
        token = self.csrf(self.admin,'/admin')
        self.admin.post(f"/admin/news/{post['id']}/toggle-published",data={'csrf_token':token})
        self.assertEqual(self.visitor.get(url).status_code,404)
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.admin.get(url).status_code,200)
        self.assertEqual(self.save_issue(issue_id=post['id'],client=self.admin,files=[self.file()],published='').status_code,303)
        new_path=self.rows('SELECT * FROM images')[0]['file_path']
        self.assertEqual(self.visitor.get('/uploads/'+new_path).status_code,404)
        self.client.post(f"/issues/{post['id']}/delete",data={'csrf_token':self.csrf(self.client,'/account')})
        self.assertFalse(self.store.path(new_path).exists())
        self.assertEqual(self.rows('SELECT * FROM images'),[])

    def test_admin_issue_and_place_delete_files_and_fallback(self):
        self.assertEqual(self.save_issue(client=self.admin,files=[self.file()]).status_code,303)
        self.save_place(files=[self.file()])
        files = [self.store.path(r['file_path']) for r in self.rows('SELECT * FROM images')]
        for table, segment in (('news_posts','news'),('places','places')):
            record = self.rows(f'SELECT * FROM {table}')[0]
            response = self.admin.post(f"/admin/{segment}/{record['id']}/delete",data={'csrf_token':self.csrf(self.admin,'/admin')})
            self.assertEqual(response.status_code,200)
        self.assertEqual(self.rows('SELECT * FROM images'),[])
        self.assertFalse(any(path.exists() for path in files))
        self.save_place(emoji='☕')
        place = self.rows('SELECT * FROM places')[0]
        self.assertIn('☕',self.visitor.get('/place/'+place['slug']).text)

    def test_shared_reference_survives_until_last_reference_deleted(self):
        self.save_place(files=[self.file()])
        self.save_place(name='두 번째 장소')
        images = self.rows('SELECT * FROM images')
        places = self.rows('SELECT id FROM places')
        path = self.store.path(images[0]['file_path'])
        with closing(self.store.connect_db()) as conn,conn:
            conn.execute('INSERT INTO images(place_id,file_path,width,height,byte_size,sort_order) SELECT ?,file_path,width,height,byte_size,0 FROM images WHERE id=?',(places[1]['id'],images[0]['id']))
        self.save_place(place_id=places[0]['id'],remove_image_0='1')
        self.assertTrue(path.exists())
        self.assertEqual(self.visitor.get('/uploads/'+images[0]['file_path']).status_code,200)
        self.save_place(place_id=places[1]['id'],remove_image_0='1')
        self.assertFalse(path.exists())

    def test_database_rollback_removes_new_file_and_retains_old_file(self):
        self.save_place(files=[self.file()])
        previous = self.rows('SELECT * FROM images')[0]
        plan = ImagePlan(self.store,'places')
        relative = 'places/' + 'f'*32 + '.webp'
        self.store.write(relative,picture('WEBP'))
        plan.new[0] = {'file_path':relative,'width':120,'height':180,'byte_size':100}
        with self.assertRaises(RuntimeError):
            with plan.transaction() as conn:
                plan.apply(conn,previous['place_id'])
                raise RuntimeError('rollback fixture')
        self.assertFalse(self.store.path(relative).exists())
        self.assertTrue(self.store.path(previous['file_path']).exists())
        self.assertEqual(self.rows('SELECT * FROM images')[0],previous)

    def test_chunked_body_limit_and_upload_paths(self):
        self.assertEqual(self.client.post('/issues/new',content=iter([b'x'*70000,b'x'*70000]),headers={'Content-Type':'application/x-www-form-urlencoded'}).status_code,413)
        for url in ('/uploads/issues/unknown.webp','/uploads/backups/file.db','/uploads/issues/%2E%2E%2Fjejuno.db'):
            self.assertEqual(self.visitor.get(url).status_code,404)

    def test_forms_multipart_and_no_uploaded_path_written_to_legacy_field(self):
        self.save_place(files=[self.file()],image_url='https://example.com/old.png')
        place=self.rows('SELECT * FROM places')[0]
        html=self.admin.get(f"/admin/places/{place['id']}/edit").text
        self.assertEqual(html.count('type="file"'),5)
        self.assertIn('enctype="multipart/form-data"',html)
        self.assertIn('name="image_url" maxlength="2048" value="https://example.com/old.png"',html)
        self.assertEqual(self.client.get('/issues/new').text.count('type="file"'),1)


class ImageMigrationTest(unittest.TestCase):
    def test_backup_is_additive_preserves_rows_and_restart_uploads(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'JEJUNO_SKIP_SEED':'1'}):
            database = str(Path(tmp)/'old.db')
            app = create_app({'TESTING':True,'DATABASE':database,'SECRET_KEY':'migration-test'})
            with closing(sqlite3.connect(database)) as conn,conn:
                conn.execute("INSERT INTO news_posts(slug,title,category,image_url) VALUES ('old','기존 소식','지역소식','https://example.com/old.jpg')")
                conn.execute('DROP TABLE images'); conn.execute('DROP TABLE image_file_gc')
                conn.execute('DELETE FROM schema_migrations WHERE version=?',(VERSION,))
            before=Path(database).read_bytes()
            with patch('app.backup_before_image_migration',side_effect=OSError('backup denied')):
                with self.assertRaises(OSError): create_app({'TESTING':True,'DATABASE':database,'SECRET_KEY':'migration-test'})
            self.assertEqual(Path(database).read_bytes(),before)
            app=create_app({'TESTING':True,'DATABASE':database,'SECRET_KEY':'migration-test'})
            with closing(sqlite3.connect(database)) as conn:
                backup=conn.execute('SELECT backup_path FROM schema_migrations WHERE version=?',(VERSION,)).fetchone()[0]
                self.assertEqual(conn.execute('SELECT image_url FROM news_posts').fetchone()[0],'https://example.com/old.jpg')
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            with closing(sqlite3.connect(backup)) as conn:
                self.assertEqual(conn.execute('SELECT title FROM news_posts').fetchone()[0],'기존 소식')
                self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='images'").fetchone())
            marker=app.state.image_store.root/'issues'/'keep-existing.txt'
            marker.write_text('keep',encoding='utf-8')
            create_app({'TESTING':True,'DATABASE':database,'SECRET_KEY':'migration-test'})
            self.assertEqual(marker.read_text(),'keep')
            self.assertIsNone(backup_before_image_migration(database))
