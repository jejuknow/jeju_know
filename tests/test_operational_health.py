import hashlib
import io
import json
import sqlite3
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import test_community as helpers
from auth_security import COOKIE_NAME, ADMIN_SESSION_SECONDS, MEMBER_SESSION_SECONDS, ServerSessionMiddleware
from operational_health import migrate
from place_duplicates import groups
from PIL import Image
from image_uploads import optimize_image, ImageError, ImageStore


class OperationalTests(unittest.TestCase):
    setUpClass = classmethod(helpers.CommunityTest.setUpClass.__func__)
    setUp = helpers.CommunityTest.setUp
    tearDown = helpers.CommunityTest.tearDown
    execute = helpers.CommunityTest.execute
    rows = helpers.CommunityTest.rows
    csrf = helpers.CommunityTest.csrf
    post = helpers.CommunityTest.post

    def add(self, slug, **values):
        data = dict(slug=slug, name=slug, region='애월', category='카페', category_id=self.category['cafe'], **values)
        return self.execute('INSERT INTO places('+','.join(data)+') VALUES('+','.join('?' for _ in data)+')',tuple(data.values()))

    def test_super_backup_csrf_restore_download_and_private_paths(self):
        for client in (self.visitor, self.member):
            self.assertEqual(client.get('/admin/backups',follow_redirects=False).status_code,303)
        self.execute("UPDATE users SET role='admin' WHERE username='other'")
        self.assertEqual(self.other.get('/admin/backups').status_code,403)
        self.assertEqual(self.other.post('/admin/backups/create').status_code,403)
        self.assertEqual(self.admin.post('/admin/backups/create',data={}).status_code,400)
        self.assertEqual(self.post(self.admin,'/admin/backups/create').status_code,303)
        status = self.admin.get('/admin/backups').context['backup']
        name = status['last_backup']['name']
        self.assertEqual(self.post(self.admin,'/admin/backups/verify').status_code,303)
        page = self.admin.get('/admin/backups')
        self.assertEqual(page.context['backup']['restore']['counts']['users'],2)
        self.assertTrue(page.context['backup']['restore']['isolated'])
        url='/admin/backups/download/'+name
        response=self.admin.get(url)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.content.startswith(b'SQLite format 3'))
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(self.other.get(url).status_code,403)
        self.assertEqual(self.visitor.get(url,follow_redirects=False).status_code,303)
        for url in ('/backups/'+name,'/static/backups/'+name,'/uploads/backups/'+name):
            self.assertIn(self.visitor.get(url).status_code,(403,404))
        self.assertEqual(len(self.rows("SELECT * FROM admin_activity_logs WHERE action='DB 수동 백업'")),1)

    def test_queues_match_filters_and_metadata_migration_preserves_data(self):
        first=self.add('without-coordinates',image_url='https://example.com/photo.jpg',checked_at='2020-01-01')
        second=self.add('without-image',latitude=33.4,longitude=126.5)
        deleted=self.add('deleted',deleted_at='2026-01-01')
        page=self.admin.get('/admin')
        self.assertEqual(page.context['health'],dict(coordinates=1,images=1,stale=1,never=1))
        for key,expected in [('coordinates',first),('image',second)]:
            result=self.admin.get('/admin/places?missing='+key)
            self.assertEqual([r['id'] for r in result.context['places']],[expected])
        before=self.rows('SELECT * FROM places')
        migrate(self.database)
        self.assertEqual(before,self.rows('SELECT * FROM places'))
        self.assertTrue(self.rows("SELECT backup_path FROM schema_migrations WHERE version='20261117_operational_health'"))

    def test_duplicate_cache_invalidates_transactionally_without_scanning_on_hit(self):
        self.add('first',address='제주 동일주소 123')
        second=self.add('second',address='제주 동일주소 123')
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory=sqlite3.Row
            self.assertEqual(len(groups(conn)),1)
            with patch('place_duplicates.rows',side_effect=AssertionError('unnecessary full scan')):
                self.assertEqual(len(groups(conn)),1)
            conn.execute('UPDATE places SET address=? WHERE id=?',('다른주소 567',second))
            conn.commit()
            self.assertEqual(groups(conn),[])
            conn.execute('UPDATE places SET address=? WHERE id=?',('제주 동일주소 123',second))
            conn.rollback()
            self.assertEqual(groups(conn),[])

    def test_cookie_absolute_expiry_logout_replay_and_legacy_admin_expiry(self):
        token=self.admin.cookies.get(COOKIE_NAME)
        digest=hashlib.sha256(token.encode()).hexdigest()
        row=self.rows('SELECT * FROM auth_sessions WHERE token_hash=?',(digest,))[0]
        self.assertAlmostEqual(row['expires_at']-time.time(),ADMIN_SESSION_SECONDS,delta=10)
        self.assertIn('auth_issued_at',json.loads(row['data']))
        self.post(self.admin,'/admin/logout')
        self.assertEqual(self.rows('SELECT * FROM auth_sessions WHERE token_hash=?',(digest,)),[])
        self.admin.cookies.set(COOKIE_NAME,token)
        self.assertEqual(self.admin.get('/admin',follow_redirects=False).status_code,303)
        self.assertTrue(self.rows("SELECT 1 FROM admin_activity_logs WHERE action='관리자 로그아웃'"))
        data=json.loads(row['data']); data.pop('auth_issued_at')
        self.execute('INSERT INTO auth_sessions VALUES(?,?,?,?,?)',(digest,json.dumps(data),None,row['admin_id'],int(time.time())+MEMBER_SESSION_SECONDS-9*3600))
        middleware=ServerSessionMiddleware(None,self.database)
        self.assertIsNone(middleware.load(digest,int(time.time()))[3])
        self.assertEqual(self.rows('SELECT 1 FROM auth_sessions WHERE token_hash=?',(digest,)),[])

    def test_error_response_and_cors_do_not_expose_credentials(self):
        @self.app.get('/qa-private-error')
        def explode():
            raise RuntimeError('PRIVATE-PASSWORD-DO-NOT-EXPOSE')
        with TestClient(self.app,raise_server_exceptions=False) as client:
            response=client.get('/qa-private-error')
            self.assertEqual(response.status_code,500)
            self.assertIn('잠시 후 다시 시도',response.text)
            self.assertNotIn('PRIVATE-',response.text)
            self.assertFalse(self.app.debug)
            for origin in ('https://www.jeju-know.com','https://jeju-know.com','https://untrusted.example','http://localhost:3000'):
                response=client.get('/health',headers={'Origin':origin})
                self.assertEqual(response.status_code,200)
                self.assertNotIn('access-control-allow-origin',response.headers)
                self.assertNotIn('access-control-allow-credentials',response.headers)

    def test_still_image_decode_and_upload_path_security(self):
        for format in ('JPEG', 'PNG', 'WEBP'):
            stream=io.BytesIO()
            Image.new('RGB',(24,24),'blue').save(stream,format=format)
            # An iPhone may send JPEG bytes while retaining a HEIC picker name/MIME.
            result,width,height=optimize_image(stream.getvalue(),'picker.heic','image/heic')
            with Image.open(io.BytesIO(result)) as decoded:
                self.assertEqual(decoded.format,'WEBP')
                self.assertEqual(decoded.size,(24,24))
        animated=io.BytesIO()
        Image.new('RGB',(24,24),'blue').save(animated,format='WEBP',save_all=True,
            append_images=[Image.new('RGB',(24,24),'red')],duration=100,loop=0)
        with self.assertRaises(ImageError):
            optimize_image(animated.getvalue(),'still.jpg','image/jpeg')
        store=ImageStore(Path(self.database).parent/'uploads',lambda:sqlite3.connect(self.database))
        for path in ('../jejuno.db','places/../../secret_key.txt','places/script.py','places/x.svg'):
            with self.assertRaises(ImageError):
                store.path(path)
