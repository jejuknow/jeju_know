"""Super-only backup operations; no live database restoration."""
from contextlib import closing
import os
from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from admin_roles import audit, require_admin
from auth_security import rate_limited
from sqlite_backups import BackupBusy


def register_backups(app, templates, connect_db, csrf, flash, secret):
    manager = app.state.backups

    @app.get("/admin/backups")
    def backups(request: Request):
        if response := require_admin(request):
            return response
        with closing(connect_db()) as conn:
            runtime = {"journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
                       "secret_env": bool(os.environ.get("SECRET_KEY")),
                       "pii_env": bool(os.environ.get("PII_ENCRYPTION_KEY")),
                       "debug": app.debug}
        return templates.TemplateResponse(request=request, name="admin/backups.html",
                                          context={"backup": manager.status(), "database_path": str(manager.database), "runtime": runtime})

    @app.post("/admin/backups/{operation}")
    async def backup_action(request: Request, operation: str):
        if response := require_admin(request):
            return response
        if operation not in ("create", "verify"):
            return HTMLResponse("찾을 수 없습니다.", status_code=404)
        form = await request.form()
        if not csrf(request, form):
            return HTMLResponse("요청을 확인해주세요.", status_code=400)
        if rate_limited(connect_db, secret, "backup", [("admin", str(request.scope["admin_identity"]["id"]))], limit=5, window=300):
            return HTMLResponse("잠시 후 다시 시도해주세요.", status_code=429)
        try:
            result = await run_in_threadpool(manager.create if operation == "create" else manager.verify_restore)
            with closing(connect_db()) as conn, conn:
                audit(conn, request, "DB 수동 백업" if operation == "create" else "DB 복구 검증", "백업", None, result["name"])
            flash(request, "백업을 생성하고 무결성을 확인했습니다." if operation == "create" else "임시 DB 복구와 주요 테이블 조회를 확인했습니다. 운영 DB는 변경하지 않았습니다.")
        except BackupBusy:
            flash(request, "다른 백업 작업이 진행 중입니다. 잠시 후 확인해주세요.", "error")
        except Exception:
            flash(request, "백업 작업을 완료하지 못했습니다. 백업 상태와 서버 로그를 확인해주세요.", "error")
        return RedirectResponse("/admin/backups", status_code=303)

    @app.get("/admin/backups/download/{name}")
    def download(request: Request, name: str):
        if response := require_admin(request):
            return response
        try:
            path = manager.path(name)
        except ValueError:
            return HTMLResponse("백업을 찾을 수 없습니다.", status_code=404)
        with closing(connect_db()) as conn, conn:
            audit(conn, request, "DB 백업 다운로드", "백업", None, name)
        return FileResponse(path, filename=name, media_type="application/octet-stream",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
