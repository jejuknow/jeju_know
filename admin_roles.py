"""Dedicated administrator identities, role policy and transactional audit trail."""
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from sqlite_migrations import verified_migration

SUPER = "super_admin"
SUB = "sub_admin"
ROLE_LABELS = {SUPER: "슈퍼관리자", SUB: "서브관리자"}
# Only listed sections are delegated. New administrative features start super-only.
EDITOR_SECTIONS = {"places", "community", "suggestions", "new", "picks", "issues", "news",
                   "user-posts", "best", "ads", "inquiries", "analytics", "account", "logout"}


def migrate(database):
    with verified_migration(database, "20261103_admin_roles", "jejuno-before-admin-role-system") as conn:
        if conn is None:
            return
        for definition in (
            "role TEXT NOT NULL DEFAULT 'sub_admin' CHECK(role IN ('super_admin','sub_admin'))",
            "display_name TEXT NOT NULL DEFAULT ''",
            "is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1))",
            "last_login_at TEXT", "updated_at TEXT", "auth_version INTEGER NOT NULL DEFAULT 1",
        ):
            conn.execute("ALTER TABLE admins ADD COLUMN " + definition)
        conn.execute("UPDATE admins SET role='super_admin', display_name=username, updated_at=created_at")
        conn.execute("""CREATE TABLE admin_activity_logs (
            id INTEGER PRIMARY KEY, admin_id INTEGER REFERENCES admins(id), actor_member_id INTEGER REFERENCES users(id),
            actor_username TEXT NOT NULL, actor_name TEXT NOT NULL, action TEXT NOT NULL, target_type TEXT NOT NULL,
            target_id INTEGER, target_name TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK ((admin_id IS NULL) != (actor_member_id IS NULL)))""")
        conn.execute("CREATE INDEX admin_activity_by_actor ON admin_activity_logs(admin_id,id DESC)")
        conn.execute("CREATE INDEX admin_activity_by_time ON admin_activity_logs(created_at,id)")
        for operation in ("DELETE", "UPDATE OF role,is_active"):
            ref = "" if operation == "DELETE" else " AND (NEW.role!='super_admin' OR NEW.is_active=0)"
            name = "protect_last_super_" + operation.split()[0].lower()
            conn.execute(f"""CREATE TRIGGER {name} BEFORE {operation} ON admins
                WHEN OLD.role='super_admin' AND OLD.is_active=1 {ref} AND NOT EXISTS
                (SELECT 1 FROM admins WHERE id!=OLD.id AND role='super_admin' AND is_active=1)
                BEGIN SELECT RAISE(ABORT,'last active super administrator'); END""")


def identity(request):
    return request.scope.get("admin_identity")


def require_admin(request):
    actor = identity(request)
    if not actor:
        return RedirectResponse("/admin/login?next=" + request.url.path, 303)
    if actor["role"] == SUPER:
        return None
    parts = request.scope["path"].strip("/").split("/")
    section = parts[1] if len(parts) > 1 else ""
    permitted = (not section or section in EDITOR_SECTIONS or
                 (section == "users" and request.method in ("GET","HEAD") and len(parts) == 2))
    if section == "places" and parts[-1] in ("delete","merge"):
        permitted = False
    if section == "account" and (actor.get("member_id") or parts[-1] == "username"):
        permitted = False
    if not permitted:
        return HTMLResponse("이 기능은 슈퍼관리자만 사용할 수 있습니다.", status_code=403)
    return None


def audit(conn, request, action, target_type, target_id, name="", *, actor=None):
    actor = actor or identity(request)
    if actor.get("member_id"):
        valid = conn.execute("SELECT 1 FROM users WHERE id=? AND status='active' AND role='admin'",
                             (actor["member_id"],)).fetchone()
    else:
        valid = conn.execute("SELECT 1 FROM admins WHERE id=? AND is_active=1 AND role=? AND auth_version=?",
                             (actor["id"], actor["role"], actor["auth_version"])).fetchone()
    if not valid:
        raise HTTPException(403, "관리자 권한이 변경되었습니다. 다시 로그인해주세요.")
    # Only explicit operation labels and target identifiers; never capture requests or inquiry bodies.
    conn.execute("""INSERT INTO admin_activity_logs(admin_id,actor_member_id,actor_username,actor_name,
                  action,target_type,target_id,target_name) VALUES(?,?,?,?,?,?,?,?)""",
                 (actor.get("id"),actor.get("member_id"),actor["username"],actor["display_name"] or actor["username"],
                  action,target_type,target_id,str(name or "")[:200]))
