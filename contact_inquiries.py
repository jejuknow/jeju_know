"""Private contact inbox. Public submissions never send mail or expose a contact record."""
import json
import os
import re
import secrets
from contextlib import closing
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from admin_ui import page_rows
from auth_security import rate_limited
from community_migrations import KST
from sqlite_migrations import verified_migration

TYPES = ("광고·제휴", "행사·공간 협업", "홈페이지 관련", "기타")
STATUSES = {"unread": "미확인", "read": "확인", "done": "처리완료"}
LIMITS = {"name": 60, "email": 254, "phone": 40, "title": 160, "content": 5000}


def migrate(database):
    with verified_migration(database, "20261102_contact_inquiries", "jejuno-before-community-contact-links") as conn:
        if conn is None:
            return
        conn.execute("""CREATE TABLE contact_inquiries (
            id INTEGER PRIMARY KEY, submission_id TEXT NOT NULL UNIQUE, inquiry_type TEXT NOT NULL,
            message BLOB NOT NULL, status TEXT NOT NULL DEFAULT 'unread' CHECK(status IN ('unread','read','done')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("CREATE INDEX contact_inquiries_status ON contact_inquiries(status,id)")


def cipher():
    value = os.environ.get("PII_ENCRYPTION_KEY", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise HTTPException(503, "현재 문의를 처리할 수 없습니다. 잠시 후 다시 시도해주세요.")
    return AESGCM(bytes.fromhex(value))


def unpack(row, box):
    message = row["message"]
    try:
        details = json.loads(box.decrypt(message[:12], message[12:], row["submission_id"].encode()))
    except InvalidTag:
        raise HTTPException(503, "문의 내용을 열 수 없습니다. 암호화 키 설정을 확인해주세요.") from None
    created_kst = datetime.fromisoformat(row["created_at"]).replace(tzinfo=timezone.utc).astimezone(KST)
    return {**details, "id":row["id"], "status":row["status"], "inquiry_type":row["inquiry_type"],
            "created_at":created_kst.strftime("%Y-%m-%d %H:%M")}


def register_contact_routes(app, templates, connect_db, guard, csrf, flash, secret):
    signer = URLSafeTimedSerializer(secret, salt="contact-form-v1")

    def render(request, template, status=200, **context):
        return templates.TemplateResponse(request=request, name=template, status_code=status,
            headers={"Cache-Control": "no-store"}, context={"types": TYPES, "statuses": STATUSES, "limits": LIMITS, **context})

    def form_page(request, values=None, error="", status=200, token=None):
        session_csrf = request.session.setdefault("csrf_token", secrets.token_urlsafe(32))
        token = token or signer.dumps([session_csrf, secrets.token_hex(16)])
        return render(request, "contact.html", status, values=values or {}, error=error, form_token=token)

    @app.get("/contact")
    def contact(request: Request):
        return form_page(request)

    @app.get("/contact/sent")
    def contact_sent(request: Request):
        if not request.session.get("contact_sent"):
            return RedirectResponse("/contact", status_code=303)
        return render(request, "contact.html", sent=True)

    @app.post("/contact")
    async def submit_contact(request: Request):
        form = await request.form()
        if not csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        values = {key: str(form.get(key, "")).strip() for key in (*LIMITS, "inquiry_type")}
        token = str(form.get("form_token", ""))
        try:
            session_csrf, submission_id = signer.loads(token, max_age=3600)
            if session_csrf != request.session["csrf_token"]:
                raise BadSignature("session")
        except BadSignature:
            return form_page(request, values, "입력 시간이 지났습니다. 내용을 확인한 뒤 다시 제출해주세요.", 400)
        error = None
        if values["inquiry_type"] not in TYPES:
            error = "문의 유형을 선택해주세요."
        elif any(not values[k] for k in LIMITS if k != "phone"):
            error = "이름 또는 닉네임, 이메일, 제목과 문의 내용을 입력해주세요."
        elif any(len(values[k]) > limit for k, limit in LIMITS.items()):
            error = "입력 가능한 글자 수를 초과했습니다. 내용을 줄여주세요."
        elif not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", values["email"]):
            error = "이메일 주소를 확인해주세요."
        if error:
            return form_page(request, values, error, 422, token)
        with closing(connect_db()) as conn:
            exists = conn.execute("SELECT 1 FROM contact_inquiries WHERE submission_id=?", (submission_id,)).fetchone()
        if not exists:
            box = cipher()
            if (rate_limited(connect_db, secret, "contact-form", [("session", session_csrf), ("email", values["email"].casefold())], limit=3, window=600)
                    or rate_limited(connect_db, secret, "contact-ip", [("ip", request.client.host if request.client else "unknown")], limit=30, window=600)):
                return form_page(request, values, "짧은 시간에 여러 건의 문의가 접수되었습니다. 10분 후 다시 시도해주세요.", 429, token)
            nonce = secrets.token_bytes(12)
            payload = json.dumps({k: values[k] for k in LIMITS}, ensure_ascii=False).encode()
            encrypted = nonce + box.encrypt(nonce, payload, submission_id.encode())
            with closing(connect_db()) as conn, conn:
                conn.execute("""INSERT INTO contact_inquiries(submission_id,inquiry_type,message) VALUES(?,?,?)
                                ON CONFLICT(submission_id) DO NOTHING""", (submission_id, values["inquiry_type"], encrypted))
        request.session["contact_sent"] = True
        return RedirectResponse("/contact/sent", status_code=303)

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
    def inquiry_detail(request: Request, inquiry_id: int):
        if response := guard(request):
            return response
        with closing(connect_db()) as conn:
            row = conn.execute("SELECT * FROM contact_inquiries WHERE id=?", (inquiry_id,)).fetchone()
        if not row:
            raise HTTPException(404, "문의를 찾을 수 없습니다.")
        return render(request, "admin/inquiry_detail.html", item=unpack(row, cipher()))

    @app.post("/admin/inquiries/{inquiry_id}/status")
    async def inquiry_status(request: Request, inquiry_id: int):
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
