import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import ADMIN_LOGIN_LIMIT, ADMIN_LOGIN_WINDOW_SECONDS, create_app
from auth_security import hash_password


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.path = Path(root.name) / "security.db"
        self.config = {"DATABASE": str(self.path), "TESTING": True, "SECRET_KEY": "isolated-security-test"}
        self.app = create_app(self.config)
        self.password = "isolated-password-123"

    def client(self, app=None):
        client = TestClient(app or self.app)
        self.addCleanup(client.close)
        return client

    def rows(self, sql):
        with closing(sqlite3.connect(self.path)) as conn:
            return conn.execute(sql).fetchall()

    def setup_admin(self):
        client = self.client()
        token = client.get("/admin/setup").context["csrf_token"]
        response = client.post("/admin/setup", data={"username": "owner", "password": self.password,
            "password_confirm": self.password, "csrf_token": token}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)

    def test_docs_fail_closed_by_default_and_for_unknown_environment(self):
        for env in (None, "production", "staging", "developmnt", ""):
            with self.subTest(env=env), patch.dict(os.environ):
                if env is None:
                    os.environ.pop("JEJUNO_ENV", None)
                else:
                    os.environ["JEJUNO_ENV"] = env
                app = create_app(self.config)
                client = self.client(app)
                paths = {route.path for route in app.routes}
                for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
                    self.assertNotIn(path, paths)
                    self.assertEqual(client.get(path).status_code, 404)
                self.assertEqual(client.get("/api/places").status_code, 200)

    def test_development_explicitly_enables_docs(self):
        with patch.dict(os.environ, {"JEJUNO_ENV": "development"}):
            client = self.client(create_app(self.config))
        self.assertIn("SwaggerUIBundle", client.get("/docs").text)
        self.assertIn("<redoc", client.get("/redoc").text)
        response = client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("openapi", response.json())

    def test_setup_requires_csrf_when_empty_and_blocks_get_and_post_when_existing(self):
        client = self.client()
        self.assertEqual(client.get("/admin/setup").status_code, 200)
        self.assertEqual(client.post("/admin/setup", data={}).status_code, 400)
        self.assertEqual(self.rows("SELECT count(*) FROM admins")[0][0], 0)
        self.setup_admin()
        before = self.rows("SELECT * FROM admins")
        for method in (client.get, client.post):
            response = method("/admin/setup", follow_redirects=False)
            self.assertEqual((response.status_code, response.headers["location"]), (303, "/admin/login"))
        self.assertEqual(self.rows("SELECT * FROM admins"), before)

    def test_concurrent_bootstrap_creates_exactly_one_super_admin(self):
        clients = [self.client(), self.client()]
        tokens = [client.get("/admin/setup").context["csrf_token"] for client in clients]
        barrier = threading.Barrier(2)
        stored_hash = hash_password(self.password)

        def synchronized_hash(password):
            barrier.wait(timeout=15)
            return stored_hash

        def submit(index):
            return clients[index].post("/admin/setup", data={"username": f"owner{index}",
                "password": self.password, "password_confirm": self.password,
                "csrf_token": tokens[index]}, follow_redirects=False)

        with patch("app.hash_password", side_effect=synchronized_hash), ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, range(2)))
        self.assertEqual([r.status_code for r in responses], [303, 303])
        self.assertEqual(self.rows("SELECT role FROM admins"), [("super_admin",)])

    def test_admin_limit_survives_restart_expires_and_allows_normal_login(self):
        self.setup_admin()
        client = self.client()
        token = client.get("/admin/login").context["csrf_token"]
        credentials = {"username": "owner", "password": "wrong", "csrf_token": token}
        for _ in range(ADMIN_LOGIN_LIMIT):
            self.assertEqual(client.post("/admin/login", data=credentials, follow_redirects=False).status_code, 303)
        state = self.rows("SELECT key_hash, attempts, expires_at FROM auth_rate_limits ORDER BY key_hash")
        self.assertEqual(len(state), 2)
        self.assertTrue(all(len(row[0]) == 64 and row[1] == ADMIN_LOGIN_LIMIT for row in state))
        restarted = self.client(create_app(self.config))
        token = restarted.get("/admin/login").context["csrf_token"]
        response = restarted.post("/admin/login", data={**credentials, "password": self.password,
            "csrf_token": token}, follow_redirects=False)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], str(ADMIN_LOGIN_WINDOW_SECONDS))
        self.assertEqual(self.rows("SELECT key_hash, attempts, expires_at FROM auth_rate_limits ORDER BY key_hash"), state)
        with patch("auth_security.time.time", return_value=time.time() + ADMIN_LOGIN_WINDOW_SECONDS + 1):
            response = restarted.post("/admin/login", data={**credentials, "password": self.password,
                "csrf_token": token}, follow_redirects=False)
            self.assertEqual((response.status_code, response.headers["location"]), (303, "/admin"))
            self.assertEqual(restarted.get("/admin").status_code, 200)
        self.assertEqual(self.rows("SELECT is_active FROM admins"), [(1,)])

    def test_admin_failure_is_uniform_and_ip_limit_covers_rotating_usernames(self):
        self.setup_admin()
        client = self.client()
        for index in range(ADMIN_LOGIN_LIMIT):
            token = client.get("/admin/login").context["csrf_token"]
            response = client.post("/admin/login", data={"username": "owner" if index == 0 else f"missing{index}",
                "password": "wrong", "csrf_token": token})
            self.assertIn("아이디 또는 비밀번호가 올바르지 않습니다.", response.text)
        token = client.get("/admin/login").context["csrf_token"]
        response = client.post("/admin/login", data={"username": "another-user", "password": "wrong",
            "csrf_token": token}, follow_redirects=False)
        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
