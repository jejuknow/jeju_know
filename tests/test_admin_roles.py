import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from admin_roles import migrate
from app import create_app
from auth_security import hash_password, verify_password


class AdminRoleTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.path = Path(root.name)/"test.db"
        self.app = create_app({"DATABASE":str(self.path),"TESTING":True})
        self.owner, self.sub = self.client(), self.client()
        self.secret = "local-operator-test-123"
        self.assertEqual(self.post(self.owner,"/admin/setup",{
            "username":"owner","password":self.secret,"password_confirm":self.secret
        },token_page="/admin/setup").status_code,303)
        self.login(self.owner,"owner",self.secret)
        self.assertEqual(self.create().status_code,303)
        self.target = self.one("SELECT id FROM admins WHERE username='manager01'")[0]

    def client(self):
        client = TestClient(self.app)
        self.addCleanup(client.close)
        return client

    def one(self,query,params=()):
        with closing(sqlite3.connect(self.path)) as conn:
            return conn.execute(query,params).fetchone()

    def post(self,client,path,data,token_page="/admin"):
        token = client.get(token_page).context["csrf_token"]
        return client.post(path,data={**data,"csrf_token":token},follow_redirects=False)

    def login(self,client,username,password):
        return self.post(client,"/admin/login",dict(username=username,password=password),"/admin/login")

    def create(self, **changes):
        return self.post(self.owner,"/admin/admins/new",{
            "username":"manager01","display_name":"운영 점검","is_active":"1","role":"super_admin",
            "password":self.secret,"password_confirm":self.secret,**changes})

    def test_migration_preserves_existing_credentials_and_is_idempotent(self):
        path = self.path.with_name("legacy.db")
        stored = hash_password("legacy-secret-123")
        with closing(sqlite3.connect(path)) as conn,conn:
            conn.executescript("""CREATE TABLE admins(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password_hash TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP); CREATE TABLE users(id INTEGER PRIMARY KEY);
                CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,backup_path TEXT)""")
            conn.execute("INSERT INTO admins(id,username,password_hash) VALUES(7,'first',?)",(stored,))
        migrate(path)
        migrate(path)
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute("SELECT id,username,password_hash,role,is_active FROM admins").fetchall(),
                             [(7,"first",stored,"super_admin",1)])
            backup=conn.execute("SELECT backup_path FROM schema_migrations").fetchone()[0]
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertNotIn("role",[r[1] for r in conn.execute("PRAGMA table_info(admins)")])

    def test_roles_direct_urls_logging_and_deactivation(self):
        self.assertEqual(self.create().status_code,409)
        self.assertEqual(self.create(username="invalid",password_confirm="mismatch").status_code,422)
        self.login(self.sub,"manager01",self.secret)
        for path in ("/admin","/admin/places","/admin/community","/admin/issues","/admin/best","/admin/ads",
                     "/admin/inquiries","/admin/analytics","/admin/users","/admin/account"):
            response=self.sub.get(path,follow_redirects=False)
            self.assertEqual(response.status_code,200,path)
            self.assertNotIn('href="/admin/admins"',response.text)
        for path in ("/admin/admins","/admin/admins/new",f"/admin/admins/{self.target}/edit","/admin/admin-logs",
                     "/admin/settings/general","/admin/classifications/region","/admin/imports"):
            self.assertEqual(self.sub.get(path,follow_redirects=False).status_code,403,path)
        for path in ("/admin/admins/new",f"/admin/admins/{self.target}/password","/admin/account/username",
                     "/admin/users/1/status","/admin/settings/general","/admin/places/1/delete"):
            self.assertEqual(self.post(self.sub,path,{"role":"super_admin"}).status_code,403,path)
        self.assertEqual(self.owner.post("/admin/admins/new",data={}).status_code,400)
        stored,role=self.one("SELECT password_hash,role FROM admins WHERE id=?",(self.target,))
        self.assertTrue(stored.startswith("$argon2id$") and verify_password(self.secret,stored))
        self.assertEqual(role,"sub_admin")
        for page in ("/admin/admins",f"/admin/admins/{self.target}/edit","/admin/admin-logs","/admin/account"):
            html=self.owner.get(page).text
            self.assertNotIn(stored,html)
            self.assertNotIn(self.secret,html)
        self.assertEqual(self.post(self.owner,f"/admin/admins/{self.target}/edit",
                         {"display_name":"비활성 점검","is_active":"0"}).status_code,303)
        self.assertEqual(self.sub.get("/admin",follow_redirects=False).status_code,303)
        self.login(self.sub,"manager01",self.secret)
        self.assertEqual(self.sub.get("/admin",follow_redirects=False).status_code,303)
        self.assertGreater(self.one("SELECT count(*) FROM admin_activity_logs WHERE admin_id=?",(self.target,))[0],0)
        self.assertEqual(self.one("SELECT count(*) FROM admin_activity_logs WHERE action='서브관리자 비활성화'")[0],1)

    def test_credentials_revoke_sessions_and_protect_last_super(self):
        another=self.client()
        self.login(another,"owner",self.secret)
        owner_id=self.one("SELECT id FROM admins WHERE role='super_admin'")[0]
        self.assertEqual(self.post(self.owner,f"/admin/admins/{owner_id}/edit",
                         {"display_name":"잘못된 요청","is_active":"0"}).status_code,403)
        for sql in ("UPDATE admins SET is_active=0","UPDATE admins SET role='sub_admin'","DELETE FROM admins"):
            with closing(sqlite3.connect(self.path)) as conn,self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql+" WHERE id=?",(owner_id,))
        rename=dict(username="owner-new",current_password=self.secret)
        self.assertEqual(self.post(self.owner,"/admin/account/username",{**rename,"username":"manager01"}).status_code,409)
        self.assertEqual(self.post(self.owner,"/admin/account/username",{**rename,"current_password":"wrong"}).status_code,403)
        self.assertEqual(self.post(self.owner,"/admin/account/username",rename).headers["location"],"/admin/login")
        for c in (self.owner,another):
            self.assertEqual(c.get("/admin",follow_redirects=False).status_code,303)
        self.login(self.owner,"owner-new",self.secret)
        new_password="new-local-secret-1234"
        change=dict(password=new_password,password_confirm=new_password,current_password=self.secret)
        self.assertEqual(self.post(self.owner,"/admin/account/password",change).headers["location"],"/admin/login")
        self.login(self.owner,"owner-new",self.secret)
        self.assertEqual(self.owner.get("/admin",follow_redirects=False).status_code,303)
        self.login(self.owner,"owner-new",new_password)
        self.assertEqual(self.owner.get("/admin/admins").status_code,200)
        self.login(self.sub,"manager01",self.secret)
        self.assertEqual(self.post(self.sub,"/admin/account/password",change).status_code,303)
        self.login(self.sub,"manager01",new_password)
        self.assertEqual(self.sub.get("/admin",follow_redirects=False).status_code,200)
        self.post(self.owner,f"/admin/admins/{self.target}/password",
                  {"password":self.secret,"password_confirm":self.secret})
        self.assertEqual(self.sub.get("/admin",follow_redirects=False).status_code,303)
        self.assertTrue(self.one("SELECT last_login_at FROM admins WHERE id=?",(self.target,))[0])

    def test_editor_writes_and_super_audit_match_committed_targets(self):
        self.login(self.sub,"manager01",self.secret)
        place = {"name":"권한 점검 카페","region":"애월","category":"카페","published":"1"}
        self.assertEqual(self.post(self.sub,"/admin/places/new",place).status_code,303)
        pid = self.one("SELECT id FROM places WHERE name=?",(place["name"],))[0]
        path = f"/admin/places/{pid}/edit"
        self.assertEqual(self.post(self.sub,path,{**place,"name":"수정된 점검 카페"}).status_code,303)
        bad = self.post(self.sub,path,{**place,"name":""})
        self.assertEqual(bad.status_code,400)
        log = self.owner.get("/admin/admin-logs",params={"actor":self.target,"action":"장소 수정"})
        self.assertEqual([(r["actor_username"],r["target_id"],r["target_name"]) for r in log.context["items"]],
                         [("manager01",pid,"수정된 점검 카페")])
        self.assertEqual(self.post(self.sub,"/admin/best/new",{"title":"점검 모음","sort_order":"0"}).status_code,303)
        self.assertEqual(self.one("SELECT count(*) FROM admin_activity_logs WHERE admin_id=? AND action='BEST 등록'",
                                  (self.target,))[0],1)
        with closing(sqlite3.connect(self.path)) as conn,conn:
            conn.execute("UPDATE admins SET role='super_admin' WHERE id=?",(self.target,))
        self.assertEqual(self.sub.get("/admin/admin-logs",follow_redirects=False).status_code,200)
        with closing(sqlite3.connect(self.path)) as conn,conn:
            conn.execute("UPDATE admins SET role='sub_admin' WHERE id=?",(self.target,))
        self.assertEqual(self.sub.get("/admin/admin-logs",follow_redirects=False).status_code,403)
