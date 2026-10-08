"""Opaque, revocable server sessions and password/validation primitives."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import threading
from contextlib import closing
from urllib.parse import urlsplit

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError, InvalidHashError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.responses import Response

# OWASP Argon2id baseline: 19 MiB, two passes, one lane, random salt per hash.
PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
DUMMY_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))
COOKIE_NAME = "jejuno_session"
HASH_SLOTS = threading.BoundedSemaphore(2)


def hash_password(password):
    with HASH_SLOTS:
        return PASSWORD_HASHER.hash(password)


def verify_password(password, stored):
    if len(password) > 128:
        return False
    try:
        if stored.startswith("$argon2id$"):
            with HASH_SLOTS:
                return PASSWORD_HASHER.verify(stored, password)
        # Existing administrators keep their credentials; rehash on successful login.
        algorithm, iterations, salt, digest = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except (VerificationError, InvalidHashError, ValueError, TypeError):
        return False


def safe_external_url(value):
    if not value:
        return True
    if len(value) > 2048 or re.search(r'[\s<>"\'\\\x00-\x1f]', value):
        return False
    try:
        url = urlsplit(value)
        url.port  # Reject malformed ports as well as malformed hosts.
        return url.scheme in ("https", "http") and bool(url.hostname) and url.username is None and url.password is None
    except ValueError:
        return False


def current_user(request):
    return request.scope.get("member")


class ServerSessionMiddleware:
    """Cookie contains only a random token; SQLite stores its SHA-256 digest.

    Updating an existing session never inserts it again after concurrent revocation.
    Rotation is explicit at authentication boundaries. Expiry is absolute, not sliding.
    """
    def __init__(self, app, database, secure=True):
        self.app, self.database, self.secure = app, database, secure

    def connect(self):
        conn = sqlite3.connect(self.database, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def load(self, token_hash, now):
        with closing(self.connect()) as conn, conn:
            row = conn.execute("SELECT * FROM auth_sessions WHERE token_hash=? AND expires_at>?", (token_hash, now)).fetchone()
            if not row:
                return {}, None, None
            member = None
            if row["user_id"]:
                member = conn.execute("SELECT id,username,nickname,role,status,created_at FROM users WHERE id=? AND status='active'", (row["user_id"],)).fetchone()
                if not member:
                    conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
                    return {}, None, None
            if row["admin_id"] and not conn.execute("SELECT 1 FROM admins WHERE id=?", (row["admin_id"],)).fetchone():
                return {}, None, None
            return json.loads(row["data"]), dict(member) if member else None, row["expires_at"]

    def save(self, old_hash, data, expiry, rotate):
        now = int(time.time())
        with closing(self.connect()) as conn, conn:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (now,))
            if rotate or not data:
                conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (old_hash,))
            if not data:
                return "", 0
            user_id, admin_id = data.get("user_id"), data.get("admin_id")
            if user_id and not conn.execute("SELECT 1 FROM users WHERE id=? AND status='active'", (user_id,)).fetchone():
                return "", 0
            if rotate or not expiry:
                token = secrets.token_urlsafe(32)
                age = 7 * 86400 if user_id or admin_id else 3600
                conn.execute("INSERT INTO auth_sessions VALUES (?,?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), json.dumps(data), user_id, admin_id, now + age))
                return token, age
            conn.execute("UPDATE auth_sessions SET data=?,user_id=?,admin_id=? WHERE token_hash=? AND expires_at>?", (json.dumps(data), user_id, admin_id, old_hash, now))
            return None, 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        connection = HTTPConnection(scope)
        token = connection.cookies.get(COOKIE_NAME, "")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        data, member, expiry = await run_in_threadpool(self.load, token_hash, int(time.time()))
        scope["session"], scope["member"] = data, member
        original = json.dumps(data, sort_keys=True)
        # Bound form bodies before multipart parsing or expensive password hashing.
        if scope["method"] in ("POST", "PUT", "PATCH", "DELETE"):
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 131072:
                    return await Response("입력 내용이 너무 큽니다.", status_code=413)(scope, receive, send)
                if not message.get("more_body"):
                    break
            delivered = False
            original_receive = receive
            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()
            receive = bounded_receive

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Content-Security-Policy"] = "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
                if "text/html" in headers.get("content-type", "") or scope["path"].startswith(("/account", "/admin")):
                    headers["Cache-Control"] = "no-store"
                rotate = scope.get("rotate_session", False)
                if rotate or json.dumps(scope["session"], sort_keys=True) != original:
                    new_token, age = await run_in_threadpool(self.save, token_hash, scope["session"], expiry, rotate)
                    if new_token is not None:
                        cookie = Response()
                        cookie.set_cookie(COOKIE_NAME, new_token, max_age=age, secure=self.secure, httponly=True, samesite="lax", path="/")
                        headers.append("set-cookie", cookie.headers["set-cookie"])
            await send(message)
        await self.app(scope, receive, send_wrapper)


def rate_limited(connect_db, secret, namespace, values, limit=10, window=900):
    """Atomic SQLite counters survive deploys. HMAC keys avoid retaining raw IP/usernames."""
    now = int(time.time())
    keys = [hmac.new(secret.encode(), f"{namespace}:{kind}:{value}".encode(), hashlib.sha256).hexdigest() for kind, value in values]
    with closing(connect_db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM auth_rate_limits WHERE expires_at<=?", (now,))
        for key in keys:
            row = conn.execute("SELECT attempts FROM auth_rate_limits WHERE key_hash=?", (key,)).fetchone()
            if row and row[0] >= limit:
                return True
        for key in keys:
            conn.execute("INSERT INTO auth_rate_limits VALUES (?,1,?) ON CONFLICT(key_hash) DO UPDATE SET attempts=attempts+1", (key, now + window))
    return False
