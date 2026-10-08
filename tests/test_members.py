import hashlib
import base64
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import create_app
from auth_security import COOKIE_NAME, ServerSessionMiddleware, hash_password, verify_password
from user_migrations import VERSION, backup_before_user_migration, migrate_users


class MembersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tmp.name) / 'members.db')
        os.environ['JEJUNO_SKIP_SEED'] = '1'
        self.config = {'TESTING': True, 'DATABASE': self.database, 'SECRET_KEY': 'member-test-only'}
        self.app = create_app(self.config)
        self.clients = []
        self.client = self.new_client()

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.tmp.cleanup()

    def new_client(self, app=None, secure=False):
        client = TestClient(app or self.app, base_url='https://testserver' if secure else 'http://testserver')
        self.clients.append(client)
        return client

    def rows(self, query, args=()):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(query, args)]

    def execute(self, query, args=()):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute(query, args)

    def csrf(self, client, path):
        page = client.get(path)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        self.assertIsNotNone(token, page.text[:200])
        return token.group(1)

    def signup(self, client=None, username='traveler', nickname='제주 여행자'):
        client = client or self.client
        return client.post('/signup', data={'csrf_token': self.csrf(client, '/signup'), 'username': username,
            'nickname': nickname, 'password': 'A-long-secret-2026', 'password_confirm': 'A-long-secret-2026'}, follow_redirects=False)

    def login(self, client=None, username='traveler', password='A-long-secret-2026'):
        client = client or self.client
        return client.post('/login', data={'csrf_token': self.csrf(client, '/login'), 'username': username, 'password': password}, follow_redirects=False)

    def member(self, client=None, username='traveler'):
        self.assertEqual(self.signup(client, username).status_code, 303)
        self.assertEqual(self.login(client, username).status_code, 303)

    def post(self, client=None, **changes):
        client = client or self.client
        data = {'csrf_token': self.csrf(client, '/issues/new'), 'title': '제주 행사', 'category': '행사•축제',
                'summary': '함께 즐겨요', 'body': '직접 확인한 소식\n둘째 줄'}
        data.update(changes)
        return client.post('/issues/new', data=data, follow_redirects=False)

    def admin(self):
        admin = self.new_client()
        self.execute('INSERT INTO admins(username,password_hash) VALUES (?,?)', ('admin', hash_password('admin-only-secret')))
        token = self.csrf(admin, '/admin/login')
        response = admin.post('/admin/login?next=https://evil.example', data={'csrf_token':token, 'username':'admin', 'password':'admin-only-secret'}, follow_redirects=False)
        self.assertEqual(response.headers['location'], '/admin')
        return admin

    def test_signup_hash_unique_casefold_and_minimal_fields(self):
        self.assertEqual(self.signup(username='TravelER').status_code, 303)
        user = self.rows('SELECT * FROM users')[0]
        self.assertEqual(user['username'], 'traveler')
        self.assertTrue(user['password_hash'].startswith('$argon2id$v=19$m=19456,t=2,p=1$'))
        self.assertTrue(verify_password('A-long-secret-2026', user['password_hash']))
        self.assertNotIn('email', user)
        self.assertNotIn('phone', user)
        duplicate = self.signup(username='TRAVELER')
        self.assertEqual(duplicate.status_code, 400)
        self.assertNotIn('A-long-secret-2026', duplicate.text)
        self.assertEqual(len(self.rows('SELECT * FROM users')), 1)
        bad = self.client.post('/signup', data={'csrf_token': self.csrf(self.client, '/signup'), 'username':'other','nickname':'짧음','password':'short','password_confirm':'short'})
        self.assertEqual(bad.status_code, 400)

    def test_secure_opaque_server_session_rotation_logout_and_restart(self):
        self.member()
        before = self.client.cookies.get(COOKIE_NAME)
        self.assertEqual(len(before), 43)
        self.assertNotIn('traveler', before)
        stored = self.rows('SELECT * FROM auth_sessions WHERE user_id=1')[0]
        self.assertEqual(stored['token_hash'], hashlib.sha256(before.encode()).hexdigest())
        self.assertNotIn('password', stored['data'])
        restarted = self.new_client(create_app(self.config))
        restarted.cookies.set(COOKIE_NAME, before)
        self.assertEqual(restarted.get('/account').status_code, 200)
        token = self.csrf(self.client, '/account')
        self.assertEqual(self.client.post('/logout', data={'csrf_token':token}, follow_redirects=False).status_code,303)
        self.assertEqual(restarted.get('/account', follow_redirects=False).status_code, 303)
        secure_app = create_app({**self.config, 'TESTING':False})
        secure = self.new_client(secure_app, secure=True)
        response = secure.get('/login')
        cookie = response.headers['set-cookie']
        for flag in ('HttpOnly', 'Secure', 'SameSite=lax', 'Path=/'):
            self.assertIn(flag, cookie)
        anonymous = secure.cookies.get(COOKIE_NAME)
        self.assertEqual(self.login(secure).status_code, 303)
        self.assertNotEqual(anonymous, secure.cookies.get(COOKIE_NAME))
        self.assertEqual(secure.get('/account').headers['cache-control'], 'no-store')

    def test_uniform_login_failure_and_persistent_rate_limit(self):
        self.signup()
        wrong = self.login(password='incorrect-password')
        unknown = self.login(username='nobody', password='incorrect-password')
        for response in (wrong, unknown):
            self.assertEqual(response.status_code,400)
            self.assertIn('아이디 또는 비밀번호를 확인해주세요.',response.text)
        for _ in range(8):
            self.login(password='incorrect-password')
        self.assertEqual(self.login(password='incorrect-password').status_code,429)
        restarted = self.new_client(create_app(self.config))
        self.assertEqual(self.login(restarted).status_code,429)
        for row in self.rows('SELECT key_hash FROM auth_rate_limits'):
            self.assertEqual(len(row['key_hash']),64)
            self.assertNotIn('traveler', row['key_hash'])

    def test_publish_owner_permissions_immediate_public_and_no_mass_assignment(self):
        self.member()
        response = self.post(author_id=999,author_type='admin',published='0',is_featured='1')
        self.assertEqual(response.status_code,303)
        item = self.rows('SELECT * FROM news_posts')[0]
        self.assertEqual((item['author_id'],item['author_type'],item['published'],item['is_featured']), (1,'user',1,0))
        visitor = self.new_client()
        detail = visitor.get('/news/'+item['slug'])
        self.assertEqual(detail.status_code,200)
        self.assertIn('사용자 등록 · 제주 여행자', detail.text)
        self.assertIn('제주 행사', visitor.get('/').text)
        stranger = self.new_client()
        self.member(stranger, 'another')
        for method in ('get','post'):
            response = getattr(stranger,method)(f"/issues/{item['id']}/edit", data={'title':'stolen'} ) if method=='post' else stranger.get(f"/issues/{item['id']}/edit")
            self.assertEqual(response.status_code,403)
        token = self.csrf(stranger,'/account')
        self.assertEqual(stranger.post(f"/issues/{item['id']}/delete",data={'csrf_token':token}).status_code,403)
        self.assertEqual(visitor.get('/issues/new',follow_redirects=False).status_code,303)
        for url in ('/admin','/admin/users','/admin/user-posts'):
            self.assertEqual(self.client.get(url,follow_redirects=False).status_code,303)
        token = self.csrf(self.client,'/account')
        changed = self.client.post(f"/issues/{item['id']}/edit",data={'csrf_token':token,'title':'수정한 제목','category':'전시•팝업','author_id':2},follow_redirects=False)
        self.assertEqual(changed.status_code,303)
        self.assertIn('수정한 제목',visitor.get('/news/'+item['slug']).text)
        self.client.post(f"/issues/{item['id']}/delete",data={'csrf_token':token})
        self.assertEqual(visitor.get('/news/'+item['slug']).status_code,404)
        self.assertIsNotNone(self.rows('SELECT deleted_at FROM news_posts')[0]['deleted_at'])

    def test_xss_urls_sql_lengths_dates_and_all_csrf_boundaries(self):
        self.member()
        attack = "<script>alert('x')</script>"
        self.assertEqual(self.post(title=attack,body=attack).status_code,303)
        item = self.rows('SELECT * FROM news_posts')[0]
        page = self.client.get('/news/'+item['slug'])
        self.assertNotIn(attack,page.text)
        self.assertIn('&lt;script&gt;',page.text)
        for changes in ({'external_url':'javascript:alert(1)'},{'image_url':'data:image/svg+xml,test'}, {'title':'x'*201},{'body':'x'*20001},{'event_start':'2026-99-99'},{'event_start':'2026-10-10','event_end':'2026-01-01'}):
            self.assertEqual(self.post(**changes).status_code,400)
        self.assertEqual(self.client.get("/news?q=' OR 1=1 --").status_code,200)
        for path in ('/issues/new',f"/issues/{item['id']}/edit",f"/issues/{item['id']}/delete",'/account/profile','/account/password','/account/delete','/logout'):
            self.assertEqual(self.client.post(path,data={'csrf_token':'잘못된토큰'}).status_code,400,path)
        self.assertEqual(self.client.post('/issues/new',content=b'x'*131073).status_code,413)
        anonymous = self.new_client()
        for path in ('/signup','/login'):
            self.assertEqual(anonymous.post(path,data={}).status_code,400)

    def test_password_change_revokes_other_sessions_and_old_password(self):
        self.member()
        other = self.new_client()
        self.login(other)
        old_hash = self.rows('SELECT password_hash FROM users')[0]['password_hash']
        token = self.csrf(self.client,'/account')
        payload={'csrf_token':token,'current_password':'wrong','password':'A-new-secret-2026','password_confirm':'A-new-secret-2026'}
        self.client.post('/account/password',data=payload)
        self.assertEqual(old_hash,self.rows('SELECT password_hash FROM users')[0]['password_hash'])
        payload['current_password']='A-long-secret-2026'
        response=self.client.post('/account/password',data=payload)
        self.assertIn('다른 기기의 로그인은 해제되었습니다',response.text)
        self.assertEqual(other.get('/account',follow_redirects=False).status_code,303)
        self.assertEqual(self.login(other).status_code,400)
        self.assertEqual(self.login(other,password='A-new-secret-2026').status_code,303)
        self.assertNotEqual(old_hash,self.rows('SELECT password_hash FROM users')[0]['password_hash'])

    def test_withdraw_anonymizes_preserves_posts_revokes_and_prevents_login(self):
        self.member()
        self.post()
        item=self.rows('SELECT * FROM news_posts')[0]
        other=self.new_client(); self.login(other)
        token=self.csrf(self.client,'/account')
        self.client.post('/account/delete',data={'csrf_token':token,'current_password':'A-long-secret-2026','confirm_delete':'yes'})
        user=self.rows('SELECT * FROM users')[0]
        self.assertEqual((user['status'],user['nickname'],user['password_hash']),('deleted','탈퇴한 사용자',''))
        self.assertNotEqual(user['username'],'traveler')
        self.assertEqual(other.get('/account',follow_redirects=False).status_code,303)
        self.assertEqual(self.login(other).status_code,400)
        detail=self.client.get('/news/'+item['slug'])
        self.assertEqual(detail.status_code,200)
        self.assertIn('탈퇴한 사용자',detail.text)

    def test_admin_moderation_blocking_and_author_cannot_unhide(self):
        self.member(); self.post()
        item=self.rows('SELECT * FROM news_posts')[0]
        admin=self.admin()
        self.assertIn('제주 여행자',admin.get('/admin/users').text)
        self.assertIn('제주 행사',admin.get('/admin/user-posts').text)
        token=self.csrf(admin,'/admin')
        admin.post(f"/admin/news/{item['id']}/edit",data={'csrf_token':token,'title':'관리자 수정','slug':item['slug'],'category':'지역소식','published':'1'})
        self.assertEqual(self.rows('SELECT title,author_id,author_type FROM news_posts')[0],{'title':'관리자 수정','author_id':1,'author_type':'user'})
        self.assertEqual(admin.post('/admin/users/1/status',data={'status':'blocked'}).status_code,400)
        admin.post(f"/admin/news/{item['id']}/toggle-published",data={'csrf_token':token})
        member_token=self.csrf(self.client,'/account')
        self.client.post(f"/issues/{item['id']}/edit",data={'csrf_token':member_token,'title':'숨김 유지','category':'지역소식','published':'1'})
        self.assertEqual(self.client.get('/news/'+item['slug']).status_code,404)
        admin.post('/admin/users/1/status',data={'csrf_token':token,'status':'blocked'})
        self.assertEqual(self.client.get('/account',follow_redirects=False).status_code,303)
        self.assertEqual(self.login().status_code,400)
        admin.post('/admin/users/1/status',data={'csrf_token':token,'status':'active'})
        self.assertEqual(self.login().status_code,303)
        admin.post(f"/admin/news/{item['id']}/delete",data={'csrf_token':token})
        self.assertEqual(self.rows('SELECT * FROM news_posts'),[])

    def test_revoked_session_cannot_be_resurrected_by_inflight_response(self):
        self.member()
        cookie=self.client.cookies.get(COOKIE_NAME)
        key=hashlib.sha256(cookie.encode()).hexdigest()
        middleware=ServerSessionMiddleware(self.app,self.database,False)
        data,_,expiry=middleware.load(key,0)
        self.execute('DELETE FROM auth_sessions WHERE token_hash=?',(key,))
        data['flashes']=[{'message':'in flight','category':'success'}]
        middleware.save(key,data,expiry,False)
        self.assertEqual(self.rows('SELECT * FROM auth_sessions WHERE token_hash=?',(key,)),[])

    def test_expired_tampered_sessions_and_profile_escaping(self):
        self.member()
        token=self.csrf(self.client,'/account')
        nickname='<img src=x onerror=alert(1)>'
        page=self.client.post('/account/profile',data={'csrf_token':token,'nickname':nickname})
        self.assertNotIn(nickname,page.text)
        self.assertIn('&lt;img',page.text)
        self.assertEqual(self.rows('SELECT nickname FROM users')[0]['nickname'],nickname)
        self.execute('UPDATE auth_sessions SET expires_at=1 WHERE user_id=1')
        self.assertEqual(self.client.get('/account',follow_redirects=False).status_code,303)
        tampered=self.new_client(); tampered.cookies.set(COOKIE_NAME,'invented-user-id-1')
        self.assertEqual(tampered.get('/account',follow_redirects=False).status_code,303)

    def test_legacy_admin_hash_upgrades_without_password_reset(self):
        salt=b'legacy-test-salt'
        digest=hashlib.pbkdf2_hmac('sha256',b'legacy-password',salt,200000)
        stored='pbkdf2_sha256$200000$'+base64.b64encode(salt).decode()+'$'+base64.b64encode(digest).decode()
        self.execute('INSERT INTO admins(username,password_hash) VALUES (?,?)',('legacy-admin',stored))
        token=self.csrf(self.client,'/admin/login')
        response=self.client.post('/admin/login',data={'csrf_token':token,'username':'legacy-admin','password':'legacy-password'})
        self.assertIn('장소 관리',response.text)
        updated=self.rows('SELECT password_hash FROM admins')[0]['password_hash']
        self.assertTrue(updated.startswith('$argon2id$'))
        self.assertTrue(verify_password('legacy-password',updated))

    def test_role_is_read_from_database_and_signup_cannot_elevate(self):
        token=self.csrf(self.client,'/signup')
        self.client.post('/signup',data={'csrf_token':token,'username':'traveler','nickname':'회원','password':'A-long-secret-2026','password_confirm':'A-long-secret-2026','role':'admin','status':'active'})
        self.login()
        self.assertEqual(self.rows('SELECT role FROM users')[0]['role'],'user')
        self.assertEqual(self.client.get('/admin/users',follow_redirects=False).status_code,303)
        self.execute("UPDATE users SET role='admin' WHERE id=1")
        self.assertEqual(self.client.get('/admin/users').status_code,200)
        self.execute("UPDATE users SET role='user' WHERE id=1")
        self.assertEqual(self.client.get('/admin/users',follow_redirects=False).status_code,303)


class UserMigrationTest(unittest.TestCase):
    def test_backup_preserves_original_rows_and_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.db'
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript("CREATE TABLE admins(id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT); INSERT INTO admins VALUES(1,'old-admin','legacy-hash'); CREATE TABLE places(id INTEGER PRIMARY KEY,name TEXT); INSERT INTO places VALUES(1,'보존할 장소'); CREATE TABLE news_posts(id INTEGER PRIMARY KEY,slug TEXT,published INTEGER,body TEXT); INSERT INTO news_posts VALUES(1,'old-news',1,'원문 보존'); CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,applied_at TEXT DEFAULT CURRENT_TIMESTAMP,backup_path TEXT);")
            backup=backup_before_user_migration(path)
            self.assertTrue(Path(backup).is_file())
            migrate_users(path,backup)
            with closing(sqlite3.connect(path)) as current, closing(sqlite3.connect(backup)) as old:
                for table in ('admins','places','news_posts'):
                    columns=','.join(row[1] for row in old.execute('PRAGMA table_info('+table+')'))
                    self.assertEqual(current.execute('SELECT '+columns+' FROM '+table).fetchall(),old.execute('SELECT * FROM '+table).fetchall())
                self.assertEqual(current.execute('SELECT author_id,author_type,deleted_at FROM news_posts').fetchone(),(None,'admin',None))
                self.assertEqual(current.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(current.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertIsNone(backup_before_user_migration(path))
            migrate_users(path,None)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations WHERE version=?',(VERSION,)).fetchone()[0],1)

    def test_backup_failure_prevents_startup_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.db'
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('CREATE TABLE preserved(id INTEGER)')
            before=path.read_bytes()
            with patch('app.backup_before_user_migration',side_effect=OSError('backup unavailable')):
                with self.assertRaises(OSError):
                    create_app({'TESTING':True,'DATABASE':str(path),'SECRET_KEY':'test'})
            self.assertEqual(path.read_bytes(),before)
