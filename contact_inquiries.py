"""Private contact inbox. Public submissions never send mail or expose a contact record."""
import json
import os
import re
import secrets
from contextlib import closing
from datetime import datetime, timezone
from typing import Annotated

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from admin_ui import page_rows
from auth_security import current_user, rate_limited
from community_migrations import KST, today
from issue_discovery import public_where
from sqlite_migrations import verified_migration

CONTENT_REQUEST = "콘텐츠 수정·삭제"
TYPES = ("광고·제휴", "행사·공간 협업", "홈페이지 관련", CONTENT_REQUEST, "기타")
STATUSES = {"unread": "미확인", "read": "확인", "done": "처리완료"}
MEMBER_STATUSES = {"unread": "접수", "read": "확인중", "done": "처리완료"}
LIMITS = {"name": 60, "email": 254, "phone": 40, "title": 160, "content": 5000}
InquiryID = Annotated[int, Path(ge=1, le=2**63-1)]


def migrate(database):
    with verified_migration(database, "20261102_contact_inquiries", "jejuno-before-community-contact-links") as conn:
        if conn is None:
            return
        conn.execute("""CREATE TABLE contact_inquiries (
            id INTEGER PRIMARY KEY, submission_id TEXT NOT NULL UNIQUE, inquiry_type TEXT NOT NULL,
            message BLOB NOT NULL, status TEXT NOT NULL DEFAULT 'unread' CHECK(status IN ('unread','read','done')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("CREATE INDEX contact_inquiries_status ON contact_inquiries(status,id)")


def migrate_member_inquiries(database):
    with verified_migration(database, "20261102_member_inquiries", "jejuno-before-my-inquiries") as conn:
        if conn is None:
            return
        present = {r[1] for r in conn.execute("PRAGMA table_info(contact_inquiries)")}
        for name, kind in (("user_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
                           ("related_content_type", "TEXT"), ("related_content_id", "INTEGER"),
                           ("admin_reply", "BLOB"), ("replied_at", "TEXT")):
            if name not in present:
                conn.execute(f"ALTER TABLE contact_inquiries ADD COLUMN {name} {kind}")
        conn.execute("CREATE INDEX IF NOT EXISTS contact_inquiries_owner ON contact_inquiries(user_id,id) WHERE user_id IS NOT NULL")


def cipher():
    value = os.environ.get("PII_ENCRYPTION_KEY", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise HTTPException(503, "현재 문의를 처리할 수 없습니다. 잠시 후 다시 시도해주세요.")
    return AESGCM(bytes.fromhex(value))


def decrypt(box, message, context):
    try:
        return box.decrypt(message[:12], message[12:], context.encode()).decode()
    except InvalidTag:
        raise HTTPException(503, "문의 내용을 열 수 없습니다. 암호화 키 설정을 확인해주세요.") from None


def encrypt(box, message, context):
    nonce = secrets.token_bytes(12)
    return nonce + box.encrypt(nonce, message.encode(), context.encode())


def kst_time(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).astimezone(KST).strftime("%Y-%m-%d %H:%M")


def unpack(row, box, *, detail=False):
    details = json.loads(decrypt(box, row["message"], row["submission_id"]))
    if detail and row["admin_reply"]:
        details.update(admin_reply=decrypt(box, row["admin_reply"], "reply:"+row["submission_id"]),
                       replied_at=kst_time(row["replied_at"]))
    return {**details, "id":row["id"], "status":row["status"], "inquiry_type":row["inquiry_type"],
            "created_at":kst_time(row["created_at"])}


def owned_issue(conn, user_id, issue_id):
    issue = conn.execute("""SELECT id,title FROM news_posts WHERE id=? AND author_id=? AND author_type='user'
                            AND approval_status='approved' AND deleted_at IS NULL""", (issue_id,user_id)).fetchone()
    if not issue:
        raise HTTPException(404, "문의를 연결할 ISSUE를 찾을 수 없습니다.")
    return dict(issue)


def related_issue(conn, row, item):
    if row["related_content_type"] != "issue":
        return None
    where, args = public_where(today())
    issue = conn.execute(f"""SELECT id,title,slug,deleted_at,({where}) AS visible FROM news_posts
                             WHERE id=? AND author_id=? AND author_type='user'""",
                         [*args, row["related_content_id"], row["user_id"]]).fetchone()
    return dict(id=row["related_content_id"], title=issue["title"] if issue else item.get("issue_title", "ISSUE"),
                slug=issue["slug"] if issue and issue["visible"] else None, editable=bool(issue and not issue["deleted_at"]))


def register_contact_routes(app, templates, connect_db, guard, csrf, flash, secret):
    signer = URLSafeTimedSerializer(secret, salt="contact-form-v1")

    def render(request, template, status=200, **context):
        return templates.TemplateResponse(request=request, name=template, status_code=status,
            headers={"Cache-Control": "no-store"}, context={"types": TYPES, "statuses": STATUSES, "limits": LIMITS, **context})

    def form_page(request, issue=None, values=None, error="", status=200, token=None):
        session_csrf = request.session.setdefault("csrf_token", secrets.token_urlsafe(32))
        token = token or signer.dumps([session_csrf, secrets.token_hex(16), issue["id"] if issue else None])
        if values is None:
            user = current_user(request)
            values = {"name": user["nickname"] if user else ""}
            if issue:
                values.update(inquiry_type=CONTENT_REQUEST, title="[ISSUE 수정·삭제 요청] " + issue["title"])
        return render(request, "contact.html", status, values=values, issue=issue, error=error, form_token=token,
                      title_limit=220 if issue else LIMITS["title"])

    @app.get("/contact")
    def contact(request: Request, issue_id: int | None = Query(None, ge=1, le=2**63-1)):
        if issue_id is None:
            return form_page(request)
        user = current_user(request)
        if not user:
            return RedirectResponse(f"/login?next=%2Fcontact%3Fissue_id%3D{issue_id}", status_code=303)
        with closing(connect_db()) as conn:
            issue = owned_issue(conn, user["id"], issue_id)
        return form_page(request, issue)

    @app.get("/contact/sent")
    def contact_sent(request: Request):
        if not request.session.get("contact_sent"):
            return RedirectResponse("/contact", status_code=303)
        return render(request, "contact.html", sent=True)

    @app.post("/contact")
    async def submit_contact(request: Request, issue_id: int | None = Query(None, ge=1, le=2**63-1)):
        form = await request.form()
        if not csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        values = {key: str(form.get(key, "")).strip() for key in (*LIMITS, "inquiry_type")}
        user = current_user(request)
        user_id = user["id"] if user else None
        issue = None
        if issue_id is not None:
            if not user:
                raise HTTPException(403, "로그인이 필요합니다.")
            with closing(connect_db()) as conn:
                issue = owned_issue(conn, user_id, issue_id)
        token = str(form.get("form_token", ""))
        try:
            session_csrf, submission_id, *bound = signer.loads(token, max_age=3600)
            # Older generic forms may still be open during a deployment.
            if session_csrf != request.session["csrf_token"] or (bound[0] if bound else None) != issue_id:
                raise BadSignature("session")
        except BadSignature:
            return form_page(request, issue, values, "문의 양식을 다시 확인한 뒤 제출해주세요.", 400)
        error = None
        if values["inquiry_type"] not in TYPES or (issue and values["inquiry_type"] != CONTENT_REQUEST):
            error = "문의 유형을 선택해주세요."
        elif any(not values[k] for k in LIMITS if k != "phone"):
            error = "이름 또는 닉네임, 이메일, 제목과 문의 내용을 입력해주세요."
        elif any(len(values[k]) > (220 if issue and k=="title" else limit) for k, limit in LIMITS.items()):
            error = "입력 가능한 글자 수를 초과했습니다. 내용을 줄여주세요."
        elif not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", values["email"]):
            error = "이메일 주소를 확인해주세요."
        if error:
            return form_page(request, issue, values, error, 422, token)
        with closing(connect_db()) as conn:
            exists = conn.execute("SELECT 1 FROM contact_inquiries WHERE submission_id=?", (submission_id,)).fetchone()
        if not exists:
            box = cipher()
            if (rate_limited(connect_db, secret, "contact-form", [("session", session_csrf), ("email", values["email"].casefold())], limit=3, window=600)
                    or rate_limited(connect_db, secret, "contact-ip", [("ip", request.client.host if request.client else "unknown")], limit=30, window=600)):
                return form_page(request, issue, values, "짧은 시간에 여러 건의 문의가 접수되었습니다. 10분 후 다시 시도해주세요.", 429, token)
            payload = {k: values[k] for k in LIMITS}
            if issue:
                payload["issue_title"] = issue["title"]
            encrypted = encrypt(box, json.dumps(payload, ensure_ascii=False), submission_id)
            with closing(connect_db()) as conn, conn:
                if issue:
                    conn.execute("BEGIN IMMEDIATE")
                    owned_issue(conn, user_id, issue_id)
                conn.execute("""INSERT INTO contact_inquiries(submission_id,inquiry_type,message,user_id,related_content_type,related_content_id)
                                VALUES(?,?,?,?,?,?) ON CONFLICT(submission_id) DO NOTHING""",
                             (submission_id, values["inquiry_type"], encrypted, user_id, "issue" if issue else None, issue_id))
        request.session["contact_sent"] = True
        return RedirectResponse("/contact/sent", status_code=303)

    @app.get("/account/inquiries/{inquiry_id}")
    def my_inquiry(request: Request, inquiry_id: InquiryID):
        user = current_user(request)
        if not user:
            return RedirectResponse(f"/login?next=/account/inquiries/{inquiry_id}", status_code=303)
        with closing(connect_db()) as conn:
            row = conn.execute("SELECT * FROM contact_inquiries WHERE id=? AND user_id=?", (inquiry_id,user["id"])).fetchone()
            if not row:
                raise HTTPException(404, "문의를 찾을 수 없습니다.")
            item = unpack(row, cipher(), detail=True)
            issue = related_issue(conn, row, item)
        return render(request, "member_inquiry.html", item=item, issue=issue, inquiry_labels=MEMBER_STATUSES)

    @app.get("/admin/inquiries")
    def inquiry_list(request: Request):
        if response := guard(request):
            return response
        status = request.query_params.get("status", "")
        with closing(connect_db()) as conn:
            rows, pagination = page_rows(conn, request, "*", "FROM contact_inquiries",
                "status=?" if status in STATUSES else "1=1", [status] if status in STATUSES else [], "id DESC")
        box = cipher() if rows else None
        return render(request, "admin/inquiries.html", items=[unpack(row, box) for row in rows], current_status=status, pagination=pagination)

    @app.get("/admin/inquiries/{inquiry_id}")
    def inquiry_detail(request: Request, inquiry_id: InquiryID):
        if response := guard(request):
            return response
        with closing(connect_db()) as conn:
            row = conn.execute("SELECT * FROM contact_inquiries WHERE id=?", (inquiry_id,)).fetchone()
            if not row:
                raise HTTPException(404, "문의를 찾을 수 없습니다.")
            item = unpack(row, cipher(), detail=True)
            issue = related_issue(conn, row, item)
            sender = conn.execute("SELECT id,username,nickname,status FROM users WHERE id=?", (row["user_id"],)).fetchone()
        return render(request, "admin/inquiry_detail.html", item=item, issue=issue, sender=sender)

    @app.post("/admin/inquiries/{inquiry_id}/reply")
    async def save_reply(request: Request, inquiry_id: InquiryID):
        if response := guard(request):
            return response
        form = await request.form()
        if not csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        reply = str(form.get("admin_reply", "")).strip()
        if not reply or len(reply)>5000:
            raise HTTPException(422, "답변은 1~5000자로 입력해주세요.")
        with closing(connect_db()) as conn, conn:
            row = conn.execute("SELECT submission_id FROM contact_inquiries WHERE id=?", (inquiry_id,)).fetchone()
            if not row:
                raise HTTPException(404, "문의를 찾을 수 없습니다.")
            message = encrypt(cipher(), reply, "reply:"+row["submission_id"])
            conn.execute("""UPDATE contact_inquiries SET admin_reply=?,replied_at=CURRENT_TIMESTAMP,
                            updated_at=CURRENT_TIMESTAMP WHERE id=?""", (message,inquiry_id))
        flash(request, "답변을 저장했습니다.")
        return RedirectResponse(f"/admin/inquiries/{inquiry_id}", status_code=303)

    @app.post("/admin/inquiries/{inquiry_id}/status")
    async def inquiry_status(request: Request, inquiry_id: InquiryID):
        if response := guard(request):
            return response
        form = await request.form()
        if not csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        status = str(form.get("status", ""))
        if status not in STATUSES:
            raise HTTPException(400, "문의 상태를 선택해주세요.")
        with closing(connect_db()) as conn, conn:
            updated = conn.execute("UPDATE contact_inquiries SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, inquiry_id))
            if not updated.rowcount:
                raise HTTPException(404, "문의를 찾을 수 없습니다.")
        flash(request, "문의 상태를 변경했습니다.")
        return RedirectResponse(f"/admin/inquiries/{inquiry_id}", status_code=303)
