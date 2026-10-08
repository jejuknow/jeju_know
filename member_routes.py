"""Local member accounts and publishing using the existing news table and forms."""
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from auth_security import DUMMY_HASH, PASSWORD_HASHER, current_user, hash_password, rate_limited, safe_external_url, verify_password


def validate_news(data, categories):
    limits = {"title": 200, "summary": 600, "body": 20000, "venue": 200,
              "source_name": 100, "sponsor_name": 100, "slug": 120}
    if not data["title"] or data["category"] not in categories:
        return "제목과 카테고리를 확인해주세요."
    if any(len(data.get(key, "")) > limit for key, limit in limits.items()):
        return "입력 가능한 글자 수를 초과했습니다. 제목 200자, 요약 600자, 본문 20,000자 이내로 입력해주세요."
    if not all(safe_external_url(data.get(key, "")) for key in ("image_url", "external_url")):
        return "외부 링크와 이미지는 올바른 http 또는 https URL로 입력해주세요."
    try:
        for key in ("event_start", "event_end", "checked_at"):
            if data.get(key):
                if len(data[key]) != 10:
                    raise ValueError
                date.fromisoformat(data[key])
    except ValueError:
        return "날짜 형식을 확인해주세요."
    if data.get("event_start") and data.get("event_end") and data["event_start"] > data["event_end"]:
        return "종료일은 시작일 이후로 입력해주세요."
    return None


def register_member_routes(app, templates, connect_db, require_csrf, flash,
                           parse_news_form, row_to_news, admin_guard, categories, secret):
    def render(request, template, status=200, **context):
        return templates.TemplateResponse(request=request, name=template, context=context, status_code=status)

    def redirect(url):
        return RedirectResponse(url, status_code=303)

    def guard(request):
        if not current_user(request):
            return redirect("/login")

    async def checked_form(request):
        form = await request.form()
        if not require_csrf(request, form):
            return None
        return form

    def csrf_error():
        return HTMLResponse("CSRF 검증에 실패했습니다. 페이지를 새로고침해주세요.", status_code=400)

    def limited(request, namespace, username="", limit=10, window=900):
        values = [("ip", request.client.host if request.client else "unknown")]
        if username:
            values.append(("account", username))
        return rate_limited(connect_db, secret, namespace, values, limit, window)

    def throttled():
        return HTMLResponse("요청이 너무 많습니다. 잠시 후 다시 시도해주세요.", status_code=429, headers={"Retry-After": "900"})

    def rotate(request, user_id):
        request.session.clear()
        request.session.update(user_id=user_id, csrf_token=secrets.token_urlsafe(32))
        request.scope["rotate_session"] = True

    @app.get("/signup", response_class=HTMLResponse)
    async def signup_get(request: Request):
        if current_user(request):
            return redirect("/account")
        return render(request, "member_auth.html", mode="signup", values={})

    @app.post("/signup")
    async def signup_post(request: Request):
        form = await checked_form(request)
        if form is None:
            return csrf_error()
        if limited(request, "signup", limit=5, window=3600):
            return throttled()
        username = str(form.get("username", "")).strip().lower()
        nickname = str(form.get("nickname", "")).strip()
        password = str(form.get("password", ""))
        error = None
        if not re.fullmatch(r"[a-z0-9_]{3,30}", username):
            error = "아이디는 영문 소문자·숫자·밑줄로 3~30자 입력해주세요."
        elif not 2 <= len(nickname) <= 30 or any(ord(c) < 32 for c in nickname):
            error = "닉네임은 2~30자 입력해주세요."
        elif not 12 <= len(password) <= 128:
            error = "비밀번호는 12~128자 입력해주세요."
        elif password != form.get("password_confirm"):
            error = "비밀번호 확인이 일치하지 않습니다."
        if not error:
            password_hash = await run_in_threadpool(hash_password, password)
            try:
                with closing(connect_db()) as conn, conn:
                    conn.execute("INSERT INTO users(username,password_hash,nickname) VALUES (?,?,?)", (username, password_hash, nickname))
            except sqlite3.IntegrityError:
                error = "이미 사용 중인 아이디입니다."
        if error:
            return render(request, "member_auth.html", status=400, mode="signup", error=error, values={"username": username, "nickname": nickname})
        flash(request, "가입이 완료되었습니다. 로그인해주세요.")
        return redirect("/login")

    @app.get("/login", response_class=HTMLResponse)
    async def login_get(request: Request):
        if current_user(request):
            return redirect("/account")
        return render(request, "member_auth.html", mode="login", values={})

    @app.post("/login")
    async def login_post(request: Request):
        form = await checked_form(request)
        if form is None:
            return csrf_error()
        username = str(form.get("username", "")).strip().lower()[:200]
        password = str(form.get("password", ""))
        if limited(request, "login", username):
            return throttled()
        with closing(connect_db()) as conn:
            user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        valid = await run_in_threadpool(verify_password, password, user["password_hash"] if user and user["password_hash"] else DUMMY_HASH)
        if not valid or not user or user["status"] != "active":
            return render(request, "member_auth.html", status=400, mode="login", values={"username": username}, error="아이디 또는 비밀번호를 확인해주세요.")
        with closing(connect_db()) as conn, conn:
            conn.execute("UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?", (user["id"],))
            if PASSWORD_HASHER.check_needs_rehash(user["password_hash"]):
                replacement = await run_in_threadpool(hash_password, password)
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (replacement, user["id"]))
        rotate(request, user["id"])
        return redirect("/account")

    @app.post("/logout")
    async def logout(request: Request):
        if await checked_form(request) is None:
            return csrf_error()
        request.session.clear()
        request.scope["rotate_session"] = True
        return redirect("/")

    @app.get("/account", response_class=HTMLResponse)
    async def account(request: Request):
        if response := guard(request):
            return response
        with closing(connect_db()) as conn:
            posts = conn.execute("SELECT * FROM news_posts WHERE author_id=? AND author_type='user' AND deleted_at IS NULL ORDER BY id DESC", (current_user(request)["id"],)).fetchall()
        return render(request, "member_account.html", posts=[row_to_news(row) for row in posts])

    @app.post("/account/profile")
    async def update_profile(request: Request):
        if response := guard(request):
            return response
        form = await checked_form(request)
        if form is None:
            return csrf_error()
        nickname = str(form.get("nickname", "")).strip()
        if not 2 <= len(nickname) <= 30 or any(ord(c) < 32 for c in nickname):
            flash(request, "닉네임은 2~30자 입력해주세요.", "error")
        else:
            with closing(connect_db()) as conn, conn:
                conn.execute("UPDATE users SET nickname=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'", (nickname, current_user(request)["id"]))
            flash(request, "프로필을 저장했습니다.")
        return redirect("/account#profile")

    @app.post("/account/password")
    async def change_password(request: Request):
        if response := guard(request):
            return response
        form = await checked_form(request)
        if form is None:
            return csrf_error()
        user_id = current_user(request)["id"]
        if limited(request, "password", str(user_id), limit=5):
            return throttled()
        with closing(connect_db()) as conn:
            user = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
        password = str(form.get("password", ""))
        if not await run_in_threadpool(verify_password, str(form.get("current_password", "")), user[0]):
            flash(request, "현재 비밀번호를 확인해주세요.", "error")
        elif not 12 <= len(password) <= 128 or password != form.get("password_confirm"):
            flash(request, "새 비밀번호를 12~128자로 입력하고 확인을 일치시켜주세요.", "error")
        else:
            replacement = await run_in_threadpool(hash_password, password)
            with closing(connect_db()) as conn, conn:
                conn.execute("UPDATE users SET password_hash=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (replacement, user_id))
                conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
            rotate(request, user_id)
            flash(request, "비밀번호를 변경했습니다. 다른 기기의 로그인은 해제되었습니다.")
        return redirect("/account#password")

    @app.post("/account/delete")
    async def delete_account(request: Request):
        if response := guard(request):
            return response
        form = await checked_form(request)
        if form is None:
            return csrf_error()
        user_id = current_user(request)["id"]
        if limited(request, "withdraw", str(user_id), limit=5):
            return throttled()
        with closing(connect_db()) as conn:
            user = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
        if form.get("confirm_delete") != "yes" or not await run_in_threadpool(verify_password, str(form.get("current_password", "")), user[0]):
            flash(request, "탈퇴 안내에 동의하고 현재 비밀번호를 확인해주세요.", "error")
            return redirect("/account#withdraw")
        with closing(connect_db()) as conn, conn:
            conn.execute("UPDATE users SET username=?,nickname='탈퇴한 사용자',password_hash='',status='deleted',last_login_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", ("deleted-" + secrets.token_hex(16), user_id))
            conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
        request.session.clear()
        request.scope["rotate_session"] = True
        flash(request, "탈퇴가 완료되었습니다. 작성한 글은 ‘탈퇴한 사용자’로 남습니다.")
        return redirect("/login")

    def owned_post(request, news_id):
        with closing(connect_db()) as conn:
            post = conn.execute("SELECT * FROM news_posts WHERE id=? AND deleted_at IS NULL", (news_id,)).fetchone()
        if not post:
            return None, HTMLResponse("게시물을 찾을 수 없습니다.", status_code=404)
        if post["author_type"] != "user" or post["author_id"] != current_user(request)["id"]:
            return None, HTMLResponse("본인의 게시물만 관리할 수 있습니다.", status_code=403)
        return post, None

    @app.get("/issues/new", response_class=HTMLResponse)
    async def user_issue_new_get(request: Request):
        if response := guard(request):
            return response
        return render(request, "member_issue_form.html", item=None)

    @app.get("/issues/{news_id}/edit", response_class=HTMLResponse)
    async def user_issue_edit_get(request: Request, news_id: int):
        if response := guard(request):
            return response
        post, response = owned_post(request, news_id)
        if response:
            return response
        return render(request, "member_issue_form.html", item=row_to_news(post))

    async def save_issue(request, news_id=None):
        if response := guard(request):
            return response
        post = None
        if news_id:
            post, response = owned_post(request, news_id)
            if response:
                return response
        form, data = await parse_news_form(request, post)
        if not require_csrf(request, form):
            return csrf_error()
        if limited(request, "publish", str(current_user(request)["id"]), limit=30, window=3600):
            return throttled()
        error = validate_news(data, categories)
        if error:
            return render(request, "member_issue_form.html", status=400, item={**data, "id": news_id}, error=error)
        # Allowlist editable fields: never accept author, visibility, or featured flags from members.
        fields = ("title", "category", "summary", "body", "image_url", "venue", "event_start", "event_end", "external_url", "source_name", "checked_at")
        values = [data[key] for key in fields]
        with closing(connect_db()) as conn, conn:
            if post:
                conn.execute("UPDATE news_posts SET " + ",".join(key + "=?" for key in fields) + ",updated_at=CURRENT_TIMESTAMP WHERE id=? AND author_id=? AND author_type='user' AND deleted_at IS NULL", (*values, news_id, current_user(request)["id"]))
                slug = post["slug"]
            else:
                slug = "issue-" + secrets.token_hex(12)
                conn.execute("INSERT INTO news_posts (" + ",".join(fields) + ",slug,author_id,author_type,published) VALUES (" + ",".join("?" for _ in fields) + ",?,?,'user',1)", (*values, slug, current_user(request)["id"]))
        flash(request, "제주 이슈를 저장했습니다." if post else "제주 이슈가 바로 공개되었습니다.")
        # Hidden posts stay hidden even after the author edits them.
        return redirect("/account")

    @app.post("/issues/new")
    async def user_issue_new_post(request: Request):
        return await save_issue(request)

    @app.post("/issues/{news_id}/edit")
    async def user_issue_edit_post(request: Request, news_id: int):
        return await save_issue(request, news_id)

    @app.post("/issues/{news_id}/delete")
    async def user_issue_delete(request: Request, news_id: int):
        if response := guard(request):
            return response
        if await checked_form(request) is None:
            return csrf_error()
        post, response = owned_post(request, news_id)
        if response:
            return response
        with closing(connect_db()) as conn, conn:
            conn.execute("UPDATE news_posts SET deleted_at=CURRENT_TIMESTAMP,published=0,updated_at=CURRENT_TIMESTAMP WHERE id=? AND author_id=? AND author_type='user'", (news_id, current_user(request)["id"]))
        flash(request, "게시물을 삭제했습니다.")
        return redirect("/account")

    @app.get("/admin/users", response_class=HTMLResponse)
    async def admin_users(request: Request):
        if response := admin_guard(request):
            return response
        with closing(connect_db()) as conn:
            users = conn.execute("SELECT u.id,u.username,u.nickname,u.created_at,u.status,(SELECT count(*) FROM news_posts n WHERE n.author_id=u.id AND n.author_type='user' AND n.deleted_at IS NULL) AS post_count FROM users u ORDER BY u.id DESC").fetchall()
        return render(request, "admin_users.html", users=users)

    @app.post("/admin/users/{user_id}/status")
    async def admin_user_status(request: Request, user_id: int):
        if response := admin_guard(request):
            return response
        form = await checked_form(request)
        if form is None:
            return csrf_error()
        status = form.get("status")
        if status not in ("active", "blocked"):
            return HTMLResponse("잘못된 상태입니다.", status_code=400)
        if current_user(request) and current_user(request)["id"] == user_id:
            return HTMLResponse("현재 로그인한 관리자의 상태는 변경할 수 없습니다.", status_code=400)
        with closing(connect_db()) as conn, conn:
            conn.execute("UPDATE users SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status<>'deleted'", (status, user_id))
            if status == "blocked":
                conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
        flash(request, "사용자 상태를 변경했습니다. 차단 시 모든 로그인이 해제됩니다.")
        return redirect("/admin/users")

    @app.get("/admin/user-posts", response_class=HTMLResponse)
    async def admin_user_posts(request: Request):
        if response := admin_guard(request):
            return response
        with closing(connect_db()) as conn:
            rows = conn.execute("SELECT * FROM news_posts WHERE author_type='user' ORDER BY id DESC").fetchall()
        return render(request, "admin_user_posts.html", posts=[row_to_news(row) for row in rows])
