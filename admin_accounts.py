"""Administrator management and self service. Credentials never enter view contexts."""
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from typing import Annotated
from community_migrations import KST

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from admin_roles import ROLE_LABELS, SUB, SUPER, audit, identity, require_admin
from admin_ui import page_rows
from auth_security import hash_password, rate_limited, verify_password

PUBLIC_COLUMNS = "id,username,display_name,role,is_active,last_login_at,created_at,updated_at"
AdminID = Annotated[int, Path(ge=1, le=2**63-1)]


def valid_username(value):
    return 3 <= len(value) <= 64 and value.isprintable() and not any(c.isspace() for c in value)


def password_error(password, confirmation):
    if not 10 <= len(password) <= 128:
        return "비밀번호는 10~128자로 입력해주세요."
    if password != confirmation:
        return "비밀번호 확인이 일치하지 않습니다."
    return ""


def name_value(form):
    value = str(form.get("display_name", "")).strip()
    if not 1 <= len(value) <= 60 or not value.isprintable():
        raise FormProblem(422, "관리자명은 1~60자로 입력해주세요.")
    return value


def status_value(form):
    if form.get("is_active") not in ("0","1"):
        raise FormProblem(422, "관리자 상태를 선택해주세요.")
    return int(form["is_active"])


class FormProblem(HTTPException):
    """Validation failure containing only a fixed, safe message."""


def local_time(value):
    if not value:
        return "—"
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(KST).strftime("%Y-%m-%d %H:%M")


def register_admin_accounts(app, templates, db, csrf, flash, secret):
    templates.env.filters["admin_kst"] = local_time

    @app.exception_handler(FormProblem)
    async def form_error(request, exc):
        path = request.scope["path"]
        if path.startswith("/admin/account/"):
            path = "/admin/account"
        elif path.endswith("/password"):
            path = path.removesuffix("/password") + "/edit"
        return templates.TemplateResponse(request=request,name="admin/operator_error.html",
            context={"notice":exc.detail,"return_path":path},status_code=exc.status_code)

    def authorized(request: Request):
        denied = require_admin(request)
        if denied:
            raise HTTPException(denied.status_code, "이 기능은 슈퍼관리자만 사용할 수 있습니다." if denied.status_code == 403 else "관리자 로그인이 필요합니다.",
                                headers={"Location":denied.headers["location"]} if "location" in denied.headers else None)

    router = APIRouter(prefix="/admin", dependencies=[Depends(authorized)])

    def page(request, template, **context):
        return templates.TemplateResponse(request=request, name="admin/"+template, context={"roles":ROLE_LABELS, **context})

    async def checked(request):
        form = await request.form()
        if not csrf(request, form):
            raise HTTPException(400, "CSRF 검증에 실패했습니다.")
        return form

    def target_sub(conn, target):
        row = conn.execute("SELECT "+PUBLIC_COLUMNS+" FROM admins WHERE id=?", (target,)).fetchone()
        if not row:
            raise HTTPException(404, "관리자를 찾을 수 없습니다.")
        if row["role"] != SUB:
            raise HTTPException(403, "슈퍼관리자의 역할이나 상태는 이 화면에서 변경할 수 없습니다.")
        return row

    def saved(request, notice, url="/admin/admins"):
        flash(request, notice, "success")
        return RedirectResponse(url, 303)

    async def new_hash(form):
        password = str(form.get("password",""))
        error = password_error(password, form.get("password_confirm"))
        if error:
            raise FormProblem(422, error)
        return await run_in_threadpool(hash_password, password)

    def revoke(conn, admin_id):
        conn.execute("UPDATE admins SET auth_version=auth_version+1 WHERE id=?", (admin_id,))
        conn.execute("DELETE FROM auth_sessions WHERE admin_id=?", (admin_id,))

    @router.get("/admins")
    def admins(request: Request):
        with closing(db()) as conn:
            rows, pager = page_rows(conn, request, PUBLIC_COLUMNS,"FROM admins","1=1",[],"id")
        return page(request,"operators.html",items=rows,pagination=pager)

    @router.get("/admins/new")
    def new(request: Request):
        return page(request,"operator_form.html", item=None)

    @router.post("/admins/new")
    async def create(request: Request):
        form = await checked(request)
        username = str(form.get("username","")).strip()
        if not valid_username(username):
            raise FormProblem(422, "관리자 아이디는 공백 없이 3~64자로 입력해주세요.")
        name, active = name_value(form), status_value(form)
        password_hash = await new_hash(form)
        with closing(db()) as conn, conn:
            try:
                target = conn.execute("""INSERT INTO admins(username,display_name,password_hash,role,is_active,updated_at)
                    VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)""",(username,name,password_hash,SUB,active)).lastrowid
            except sqlite3.IntegrityError:
                raise FormProblem(409, "이미 사용 중인 관리자 아이디입니다.") from None
            audit(conn,request,"서브관리자 생성","관리자",target,username)
        return saved(request,"서브관리자가 생성되었습니다.")

    @router.get("/admins/{target}/edit")
    def edit(request: Request, target: AdminID):
        with closing(db()) as conn:
            row = target_sub(conn,target)
        return page(request,"operator_form.html",item=row)

    @router.post("/admins/{target}/edit")
    async def update(request: Request, target: AdminID):
        form = await checked(request)
        name, active = name_value(form), status_value(form)
        with closing(db()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            old = target_sub(conn,target)
            action = "서브관리자 수정"
            if active != old["is_active"]:
                action = "서브관리자 활성화" if active else "서브관리자 비활성화"
                revoke(conn,target)
            conn.execute("UPDATE admins SET display_name=?,is_active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (name,active,target))
            audit(conn,request,action,"관리자",target,old["username"])
        return saved(request,"관리자 정보를 저장했습니다.")

    @router.post("/admins/{target}/password")
    async def reset_password(request: Request, target: AdminID):
        replacement = await new_hash(await checked(request))
        with closing(db()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = target_sub(conn,target)
            conn.execute("UPDATE admins SET password_hash=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (replacement,target))
            revoke(conn,target)
            audit(conn,request,"서브관리자 비밀번호 재설정","관리자",target,row["username"])
        return saved(request,"새 비밀번호를 설정하고 이 관리자의 기존 로그인을 해제했습니다.")

    @router.get("/account")
    def account(request: Request):
        return page(request,"operator_account.html", item=identity(request))

    @router.post("/account/profile")
    async def profile(request: Request):
        form = await checked(request)
        actor = identity(request)
        with closing(db()) as conn, conn:
            conn.execute("UPDATE admins SET display_name=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (name_value(form),actor["id"]))
            audit(conn,request,"관리자명 변경","관리자",actor["id"],actor["username"])
        return saved(request,"관리자명을 저장했습니다.","/admin/account")

    @router.post("/account/username")
    @router.post("/account/password")
    async def credentials(request: Request):
        form = await checked(request)
        actor = identity(request)
        rename = request.url.path.endswith("/username")
        if rate_limited(db,secret,"admin-credentials",[("admin",str(actor["id"]))],limit=8):
            raise HTTPException(429,"잠시 후 다시 시도해주세요.")
        with closing(db()) as conn:
            row = conn.execute("SELECT password_hash FROM admins WHERE id=?",(actor["id"],)).fetchone()
        if not row or not await run_in_threadpool(verify_password,str(form.get("current_password","")),row[0]):
            raise FormProblem(403,"현재 비밀번호를 확인해주세요.")
        if rename:
            value = str(form.get("username","")).strip()
            if not valid_username(value) or value == actor["username"]:
                raise FormProblem(422,"현재와 다른 관리자 아이디를 공백 없이 3~64자로 입력해주세요.")
        else:
            value = await new_hash(form)
        with closing(db()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            audit(conn,request,"관리자 아이디 변경" if rename else "관리자 본인 비밀번호 변경",
                  "관리자",actor["id"],value if rename else actor["username"])
            try:
                if rename:
                    conn.execute("UPDATE admins SET username=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (value,actor["id"]))
                else:
                    conn.execute("UPDATE admins SET password_hash=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (value,actor["id"]))
            except sqlite3.IntegrityError:
                raise FormProblem(409,"이미 사용 중인 관리자 아이디입니다.") from None
            revoke(conn,actor["id"])
        request.session.clear()
        request.scope["rotate_session"] = True
        return saved(request,"관리자 아이디가 변경되었습니다. 새 아이디로 다시 로그인해주세요." if rename else
                     "비밀번호가 변경되었습니다. 새 비밀번호로 다시 로그인해주세요.","/admin/login")

    @router.get("/admin-logs")
    def logs(request: Request):
        params = request.query_params
        where, args = ["1=1"], []
        if params.get("actor"):
            where.append("admin_id=?")
            args.append(params["actor"])
        if params.get("action"):
            where.append("action=?")
            args.append(params["action"])
        for field, compare in (("start",">="),("end","<=")):
            if params.get(field):
                try:
                    day = date.fromisoformat(params[field]).isoformat()
                except ValueError:
                    raise FormProblem(422,"날짜를 확인해주세요.") from None
                where.append("date(created_at,'+9 hours') "+compare+" ?")
                args.append(day)
        with closing(db()) as conn:
            items, pager = page_rows(conn,request,"*,datetime(created_at,'+9 hours') AS local_time",
                                      "FROM admin_activity_logs"," AND ".join(where),args,"id DESC")
            admins = conn.execute("SELECT id,username FROM admins ORDER BY id").fetchall()
            actions = [r[0] for r in conn.execute("SELECT DISTINCT action FROM admin_activity_logs ORDER BY action")]
        return page(request,"operator_logs.html",items=items,pagination=pager,operators=admins,actions=actions,filters=params)

    app.include_router(router)
