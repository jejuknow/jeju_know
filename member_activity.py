"""Read-only views over a member's existing activity. No synthetic timeline rows."""
import json
from contextlib import closing
from datetime import date

from fastapi import Request
from fastapi.responses import RedirectResponse

from admin_ui import integer
from auth_security import current_user
from community_migrations import today
from community_places import SUGGESTION_FIELDS, editable
from contact_inquiries import cipher, kst_time, unpack
import user_status as status
from issue_discovery import public_where, schedule
from submission_suggestions import DETAIL_FIELD, DETAIL_LABEL, display_proposal


# These fixed scopes are shared by counts, lists, and the recent feed.
SCOPES = {
    "places": ("places p LEFT JOIN categories c ON c.id=p.category_id",
               "p.submitted_by_user_id=? AND p.deleted_at IS NULL", "p.updated_at"),
    "issues": ("news_posts p", "p.author_id=? AND p.author_type='user' AND p.deleted_at IS NULL", "p.updated_at"),
    "suggestions": ("place_correction_suggestions p JOIN places loc ON loc.id=p.place_id",
                    "p.user_id=?", "COALESCE(p.reviewed_at,p.created_at)"),
    "inquiries": ("contact_inquiries p", "p.user_id=?", "p.updated_at"),
}
TABS = {"all": "전체 활동", "places": "내 장소", "issues": "내 ISSUE", "suggestions": "수정 제안", "inquiries": "내 문의"}
CARDS = {"places": "등록 장소", "issues": "제주 ISSUE", "suggestions": "수정 제안", "inquiries": "내 문의"}
KINDS = {"places": "장소 등록", "issues": "ISSUE 등록", "suggestions": "수정 제안", "inquiries": "문의"}
PAGE_SIZE = 10
RECENT_LIMIT = 8


def count_owned(conn, section, uid):
    source, scope, _ = SCOPES[section]
    return conn.execute(f"SELECT COUNT(*) FROM {source} WHERE {scope}", (uid,)).fetchone()[0]


def suggestion_value(conn, field, value):
    if not value:
        return ""
    if field == DETAIL_FIELD:
        return display_proposal(conn, value)
    if field == "category_id":
        row = conn.execute("SELECT name FROM categories WHERE id=?", (value,)).fetchone()
        return row[0] if row else "이전 분류"
    if field in ("coordinates", "opening"):
        try:
            first, second = json.loads(value)
        except (ValueError, TypeError):
            return value
        if first is None:
            return ""
        if field == "opening":
            return f"{first}년" + (f" {second}월" if second else "")
        return f"{first}, {second}" if second is not None else ""
    return value


def activity_rows(conn, section, uid, day: date, limit, offset=0, *, recent=False):
    source, scope, changed = SCOPES[section]
    fields, args = "p.*", []
    if section == "places":
        fields += ", COALESCE(c.name,p.category) AS category_label"
    elif section == "issues":
        visible, args = public_where(day)
        fields += f", CASE WHEN {visible} THEN 1 ELSE 0 END AS visible"
    elif section == "suggestions":
        fields += ", loc.name AS place_name"
    rows = conn.execute(f"""SELECT {fields},datetime({changed}) AS activity_time FROM {source}
                          WHERE {scope} ORDER BY activity_time DESC,p.id DESC LIMIT ? OFFSET ?""",
                        [*args, uid, limit, offset]).fetchall()
    box = cipher() if section == "inquiries" and rows else None
    result = []
    for r in rows:
        item = dict(r)
        link = f"/account/{section}#activity-{r['id']}"
        if section == "places":
            shown = bool(r["published"] and not r["merged_into_id"])
            title = r["name"]
            labels = [status.place_review(r["source_type"], r["verification_status"]), status.VISIBILITY[shown]]
            item.update(visible=shown, can_edit=editable(r, uid))
            if shown:
                link = "/place/" + r["slug"]
        elif section == "issues":
            item.update(schedule(item, day))
            title = r["title"]
            labels = [status.issue_approval(r["approval_status"], r["visible"]), status.SCHEDULE[item["schedule_key"]]]
            if r["visible"]:
                link = "/news/" + r["slug"]
        elif section == "suggestions":
            title, labels = r["place_name"], [status.CORRECTION.get(r["status"], status.UNKNOWN)]
            if not recent:
                item.update(field_label=DETAIL_LABEL if r["field"] == DETAIL_FIELD else SUGGESTION_FIELDS.get(r["field"],"기타"),
                            before=suggestion_value(conn,r["field"],r["original_value"]),
                            after=suggestion_value(conn,r["field"],r["new_value"]))
        else:
            # Only decrypt after SQL has checked ownership; never expose the body in the feed.
            title, labels = unpack(r, box)["title"], [status.INQUIRY.get(r["status"], status.UNKNOWN)]
            link = f"/account/inquiries/{r['id']}"
        result.append(dict(item, title=title, labels=labels, kind=KINDS[section], href=link,
                           activity_date=kst_time(r["activity_time"]), registered=kst_time(r["created_at"])[:10]))
    return result


def register_activity_routes(app, templates, connect_db):
    def render(request, tab, **context):
        return templates.TemplateResponse(request=request,name="member_account.html",
            context={"tab":tab, "tabs":TABS, "cards":CARDS, **context}, headers={"Cache-Control":"no-store"})

    @app.get("/account")
    @app.get("/account/places")
    @app.get("/account/issues")
    @app.get("/account/suggestions")
    @app.get("/account/inquiries")
    @app.get("/account/settings")
    def activity(request: Request):
        user = current_user(request)
        path = request.url.path
        if not user:
            return RedirectResponse("/login?next=" + path, status_code=303)
        tab = path.rsplit("/",1)[1] if path != "/account" else "all"
        if tab == "settings":
            return render(request, tab)
        uid, day = user["id"], today()
        with closing(connect_db()) as conn:
            conn.execute("BEGIN")  # Cards and feed see the same snapshot.
            if tab == "all":
                counts = {key:count_owned(conn,key,uid) for key in CARDS}
                recent = [item for key in CARDS if counts[key]
                          for item in activity_rows(conn,key,uid,day,RECENT_LIMIT,recent=True)]
                recent.sort(key=lambda x:(x["activity_time"],x["id"],x["kind"]), reverse=True)
                return render(request,tab, counts=counts, recent=recent[:RECENT_LIMIT])
            count = count_owned(conn,tab,uid)
            pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
            page = integer(request.query_params.get("page"),1,1,pages)
            items = activity_rows(conn,tab,uid,day,PAGE_SIZE,(page-1)*PAGE_SIZE)
        return render(request,tab, items=items, count=count, page=page, pages=pages)
