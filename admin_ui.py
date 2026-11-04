"""Admin presentation/query layer. Existing write routes remain authoritative."""
import json
import math
import re
import sqlite3
import uuid
from contextlib import closing
from urllib.parse import urlencode, urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from issue_discovery import VALID_DATES
from admin_settings import catalog, catalog_usage, read_settings, write_setting, DEFAULTS
from auth_security import current_user, safe_external_url
from community_migrations import today
from place_freshness import CHECK_DATE, NEVER, STALE, freshness_counts, six_month_cutoff
from new_visibility import new_clause
from community_places import SUGGESTION_FIELDS
from admin_roles import ROLE_LABELS, SUPER, audit, identity
from submission_suggestions import DETAIL_FIELD, DETAIL_LABEL, display_proposal, snapshot
SUGGESTION_LABELS = {**SUGGESTION_FIELDS, DETAIL_FIELD: DETAIL_LABEL}
from taxonomy_migrations import TYPES
from place_auto_checks import counts as auto_counts, ORDER as AUTO_ORDER, decorate as decorate_check

ACTIVE = "p.deleted_at IS NULL AND p.merged_into_id IS NULL"
KINDS = {"region": "지역", "place": "장소 카테고리", "restaurant_subcategory": "맛집 유형",
         "cafe_focus": "카페 성향", "cafe_feature": "카페 특징", "companion": "동행"}


def integer(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def admin_return(form, default="/admin"):
    value = str(form.get("next", ""))
    try:
        parts = urlsplit(value)
    except ValueError:
        return default
    if not parts.scheme and not parts.netloc and re.fullmatch(r"/admin(?:/[a-zA-Z0-9_-]+)*", parts.path) and len(value)<2000 and not any(ord(c)<32 for c in value):
        return value
    return default


def paginate(request, total):
    size = integer(request.query_params.get("per_page"), 10, 10, 50)
    if size not in (10, 30, 50):
        size = 10
    pages = max(1, math.ceil(total / size))
    page = integer(request.query_params.get("page"), 1, 1, pages)
    def href(number):
        args = dict(request.query_params)
        args.update(page=number, per_page=size)
        return request.url.path + "?" + urlencode(args)
    return dict(total=total, page=page, pages=pages, size=size, offset=(page-1)*size,
                links=[(n, href(n)) for n in range(max(1, page-2), min(pages, page+2)+1)],
                previous=href(page-1) if page > 1 else None, next=href(page+1) if page < pages else None)


def page_rows(conn, request, select, source, where, args, order):
    total = conn.execute("SELECT count(*) " + source + " WHERE " + where, args).fetchone()[0]
    page = paginate(request, total)
    rows = conn.execute("SELECT " + select + " " + source + " WHERE " + where + " ORDER BY " + order + " LIMIT ? OFFSET ?", [*args, page["size"], page["offset"]]).fetchall()
    return rows, page


def counters(conn):
    now = today()
    new_sql, new_args = new_clause(now)
    scalar = lambda sql, args=(): conn.execute(sql, args).fetchone()[0]
    return dict(auto=auto_counts(conn), total=scalar("SELECT count(*) FROM places p WHERE " + ACTIVE),
                unverified=scalar("SELECT count(*) FROM places p WHERE " + ACTIVE + " AND source_type='user' AND verification_status='unverified'"),
                issue_pending=scalar("SELECT count(*) FROM news_posts WHERE author_type='user' AND approval_status='pending' AND deleted_at IS NULL"),
                unread_inquiries=scalar("SELECT count(*) FROM contact_inquiries WHERE status='unread'"),
                suggestions=scalar("SELECT count(*) FROM place_correction_suggestions WHERE status='pending'"),
                new=scalar("SELECT count(*) FROM places p JOIN categories c ON c.id=p.category_id WHERE " + ACTIVE + " AND " + new_sql, new_args),
                picks=scalar("SELECT count(*) FROM place_pick_periods k JOIN places p ON p.id=k.place_id WHERE " + ACTIVE + " AND k.year=? AND k.month=? AND k.is_active=1", (now.year, now.month)))


def admin_context(request, conn):
    actor = identity(request)
    if not actor:
        return {}
    account = {"username":actor["username"], "name":actor["display_name"] or actor["username"],
               "role":ROLE_LABELS[actor["role"]], "dedicated":bool(actor.get("id"))}
    path = request.url.path
    if path.startswith('/admin/imports'):
        section, title, active = '장소 관리', 'CSV 일괄 등록', 'imports'
    elif path.startswith("/admin/categories") or path.startswith("/admin/classifications/"):
        section = "분류 관리"
        key = path.rsplit("/", 1)[-1] if "/classifications/" in path else request.query_params.get("type", "place")
        title, active = KINDS.get(key, "장소 카테고리"), "class-" + key
    elif path.startswith("/admin/news") or path in ("/admin/issues", "/admin/user-posts"):
        section, title, active = "콘텐츠 관리", "ISSUE", "issues"
    elif path.startswith("/admin/ads"):
        section, title, active = "콘텐츠 관리", "광고 관리", "ads"
    elif path.startswith("/admin/inquiries"):
        section, title, active = "콘텐츠 관리", "문의 관리", "inquiries"
    elif path.startswith("/admin/best"):
        section, title, active = "콘텐츠 관리", "BEST", "best"
    elif path.startswith("/admin/users"):
        section, title, active = "회원 관리", "회원 목록", "users"
    elif path == '/admin/analytics':
        section, title, active = '통계', '방문·FIND 통계', 'analytics'
    elif path.startswith("/admin/admins"):
        section, title, active = "관리자 관리", "관리자 목록", "operators"
    elif path == "/admin/admin-logs":
        section, title, active = "관리자 관리", "관리자 활동 기록", "admin-logs"
    elif path.startswith("/admin/account"):
        section, title, active = "내 계정", "내 관리자 계정", "operator-self"
    elif path.startswith("/admin/settings"):
        section, active = "운영 설정", path.rsplit("/", 1)[-1]
        title = "홈 화면" if active == "home" else "기본 설정"
    elif path == "/admin":
        section, title, active = "DASHBOARD", "대시보드", "dashboard"
    else:
        section = "장소 관리"
        title, active = {"/admin/community": ("사용자 등록", "community"), "/admin/suggestions": ("정보 바로잡기", "suggestions"),
                         "/admin/places/stale": ("오래된 정보", "stale"), "/admin/new": ("신상 장소", "new"), "/admin/picks": ("JEJU_KNOW PICK", "picks")}.get(path, ("전체 장소", "places"))
    detail = "장소 등록" if path == "/admin/places/new" else "장소 수정" if re.fullmatch(r"/admin/places/\d+/edit", path) else "중복 확인" if path.endswith("/merge") else ""
    if path.startswith("/admin/news/"):
        detail = "ISSUE 등록" if path.endswith("/new") else "ISSUE 수정"
    if path.startswith("/admin/ads/"):
        detail = "광고 등록" if path.endswith("/new") else "광고 수정"
    if path.startswith("/admin/inquiries/"):
        detail = "문의 상세"
    if path.startswith("/admin/admins/"):
        detail = "서브관리자 추가" if path.endswith("/new") else "서브관리자 수정"
    return {"is_super": actor["role"] == SUPER, "admin_account": account, "admin_counts": counters(conn), "admin_active": active,
            "admin_section": section, "admin_title": detail or title, "admin_parent_title": title,
            "admin_is_detail": bool(detail), "classification_kinds": {k: v for k, v in KINDS.items() if k != "cafe_focus"}}


class AdminUI:
    def __init__(self, app, templates, connect_db, guard, csrf, flash, place, news, news_categories, news_aliases=None):
        self.app, self.templates, self.db = app, templates, connect_db
        self.guard, self.csrf, self.flash = guard, csrf, flash
        self.place, self.news, self.news_categories = place, news, news_categories
        self.news_aliases = news_aliases or {}

    def render(self, request, name, **context):
        return self.templates.TemplateResponse(request=request, name="admin/" + name, context={"filters": dict(request.query_params), **context})

    async def checked(self, request):
        form = await request.form()
        return form if self.csrf(request, form) else None

    def dashboard(self, request):
        if response := self.guard(request):
            return response
        with closing(self.db()) as conn:
            from analytics import report
            stats = report(conn, {'period':'30d'})
            freshness = freshness_counts(conn, ACTIVE, six_month_cutoff(today()).isoformat())
            places = [decorate_check(dict(r)) for r in conn.execute("SELECT p.*,u.nickname FROM places p LEFT JOIN users u ON u.id=p.submitted_by_user_id WHERE " + ACTIVE + " AND p.source_type='user' AND p.verification_status='unverified' ORDER BY " + AUTO_ORDER + ",p.id DESC LIMIT 5")]
            suggestions = conn.execute("SELECT s.*,p.name FROM place_correction_suggestions s JOIN places p ON p.id=s.place_id WHERE s.status='pending' ORDER BY s.id DESC LIMIT 5").fetchall()
        return self.render(request, "dashboard.html", places=places, suggestions=suggestions, suggestion_fields=SUGGESTION_LABELS, analytics=stats, freshness=freshness)

    def stale_places(self, request):
        if response := self.guard(request):
            return response
        day = today()
        cutoff = six_month_cutoff(day).isoformat()
        mode = request.query_params.get("filter", "all")
        if mode not in ("all", "never", "stale"):
            mode = "all"
        predicate = {"all": f"({NEVER} OR {STALE})", "never": NEVER, "stale": STALE}[mode]
        where = [ACTIVE, predicate]
        args = [] if mode == "never" else [cutoff]
        q = request.query_params.get("q", "").strip()[:200]
        if q:
            where.append("instr(lower(p.name), lower(?)) > 0")
            args.append(q)
        with closing(self.db()) as conn:
            counts = freshness_counts(conn, ACTIVE, cutoff)
            rows, page = page_rows(conn, request,
                                   "p.id,p.name,p.region,p.category,p.published,p.checked_at,"
                                   "CAST(julianday(as_of) - julianday(p.checked_at) AS INTEGER) AS elapsed_days",
                                   "FROM places p CROSS JOIN (SELECT ? AS as_of)", " AND ".join(where),
                                   [day.isoformat(), *args], f"{CHECK_DATE} ASC, p.id ASC")
        return self.render(request, "stale_places.html", places=rows, pagination=page, freshness=counts,
                           selected=mode, q=q)

    def places(self, request, mode="all"):
        if response := self.guard(request):
            return response
        f = request.query_params
        where, args = [ACTIVE], []
        field = f.get("search_by", "all")
        q = f.get("q", "").strip()[:200]
        if q:
            expression = {"name": "p.name", "address": "p.address", "id": "CAST(p.id AS TEXT)"}.get(field, "p.name || ' ' || p.address || ' ' || p.region")
            where.append("instr(lower(" + expression + "),lower(?))>0")
            args.append(q)
        for param, column, allowed in [("source", "source_type", ("user", "jejuno")), ("verification", "verification_status", ("unverified", "jejuno_verified")), ("published", "published", ("0", "1"))]:
            if f.get(param) in allowed:
                where.append("p." + column + "=?")
                args.append(f[param])
        if f.get("category"):
            where.append("c.slug=?"); args.append(f["category"][:80])
        if f.get("region"):
            where.append("p.region=?"); args.append(f["region"][:40])
        if mode == "community":
            where.append("p.source_type='user'")
            status = f.get("status", "unverified")
            if status == "unverified": where.append("p.verification_status='unverified'")
            elif status == "verified": where.append("p.verification_status!='unverified'")
            elif status == "hidden": where.append("p.published=0")
            if f.get("auto_check") in ("normal", "review", "hold"):
                where.append("p.auto_check_status=?"); args.append(f["auto_check"])
        year = integer(f.get("year"), today().year, 1900, today().year)
        if mode == "new":
            new_sql, new_args = new_clause()
            where.append(new_sql)
            args.extend(new_args)
            if f.get("year"):
                where.append("p.opened_year=?")
                args.append(year)
        with closing(self.db()) as conn:
            select = "p.*,u.nickname,(SELECT count(*) FROM place_correction_suggestions s WHERE s.place_id=p.id AND s.status='pending') AS pending_count"
            order = "p.updated_at DESC,p.id DESC"
            if mode == "community":
                order = AUTO_ORDER + ",p.created_at DESC,p.id DESC"
            if mode == "all":
                # Imports use SQLite timestamps; admin edits also use offset-bearing ISO timestamps.
                order = "p.published ASC, julianday(p.updated_at) DESC, p.id DESC"
                select += ",COUNT(*) FILTER (WHERE p.published=0) OVER () AS private_count"
            rows, page = page_rows(conn, request, select, "FROM places p LEFT JOIN categories c ON c.id=p.category_id LEFT JOIN users u ON u.id=p.submitted_by_user_id", " AND ".join(where), args, order)
            categories = conn.execute("SELECT name,slug FROM categories WHERE type='place' ORDER BY sort_order,id").fetchall()
            regions = catalog(conn, "region", True)
            years = sorted({today().year, *[r[0] for r in conn.execute("SELECT DISTINCT opened_year FROM places WHERE opened_year IS NOT NULL")]}, reverse=True)
            pending = {}
            if mode == "community" and rows:
                ids = [row["id"] for row in rows]
                for row in conn.execute("SELECT id,place_id,new_value FROM place_correction_suggestions WHERE status='pending' AND field=? AND place_id IN (" + ",".join("?" for _ in ids) + ") ORDER BY id", (DETAIL_FIELD, *ids)):
                    pending.setdefault(row["place_id"], []).append({"id": row["id"], "summary": display_proposal(conn, row["new_value"])})
        items = [{**self.place(row), "nickname": row["nickname"], "pending_count": row["pending_count"], "detail_proposals": pending.get(row["id"], [])} for row in rows]
        private_count = rows[0]["private_count"] if mode == "all" and rows else 0
        return self.render(request, "places.html", places=items, pagination=page, mode=mode, categories=categories, regions=regions, years=years, selected_year=year, private_count=private_count)

    def suggestions(self, request):
        if response := self.guard(request): return response
        status = request.query_params.get("status", "pending")
        where, args = ["1=1"], []
        if status in ("pending", "accepted", "rejected"):
            where.append("s.status=?"); args.append(status)
        if q := request.query_params.get("q", "").strip()[:200]:
            where.append("instr(p.name,?)>0"); args.append(q)
        with closing(self.db()) as conn:
            rows, page = page_rows(conn, request, "s.*,p.name,u.nickname", "FROM place_correction_suggestions s JOIN places p ON p.id=s.place_id JOIN users u ON u.id=s.user_id", " AND ".join(where), args, "s.id DESC")
            items = []
            for row in rows:
                item = dict(row)
                p = dict(conn.execute("SELECT * FROM places WHERE id=?", (row["place_id"],)).fetchone())
                field = row["field"]
                current = (json.dumps([p["latitude"], p["longitude"]], ensure_ascii=False) if field == "coordinates" else json.dumps([p["opened_year"], p["opened_month"]], ensure_ascii=False) if field == "opening" else ("공개" if p["published"] else "숨김") if field == "closed" else str(p.get(field) or ""))
                item["current_value"] = current
                if field == "category_id":
                    for key in ("current_value", "original_value", "new_value"):
                        category = conn.execute("SELECT name FROM categories WHERE id=?", (item[key],)).fetchone()
                        if category: item[key] = category[0]
                if field == DETAIL_FIELD:
                    proposal = json.loads(row["new_value"])
                    item["current_value"] = display_proposal(conn, snapshot(conn, p, proposal))
                    item["original_value"] = display_proposal(conn, row["original_value"])
                    item["new_value"] = display_proposal(conn, row["new_value"])
                items.append(item)
        return self.render(request, "suggestions.html", suggestions=items, pagination=page, selected_status=status, suggestion_fields=SUGGESTION_LABELS)

    def issues(self, request, user_only=False):
        if response := self.guard(request): return response
        f = dict(request.query_params)
        where, args = ["1=1" if user_only else "deleted_at IS NULL"], []
        if q := f.get("q", "").strip()[:200]: where.append("instr(title,?)>0"); args.append(q)
        if f.get("category"):
            category = self.news_aliases.get(f["category"], f["category"][:40])
            f["category"] = category
            values = [category] + [old for old, new in self.news_aliases.items() if new == category]
            where.append("category IN (" + ",".join("?" for _ in values) + ")"); args.extend(values)
        if user_only or f.get("source") in ("admin", "user"):
            where.append("author_type=?"); args.append("user" if user_only else f["source"])
        if f.get("published") in ("0", "1"): where.append("published=?"); args.append(f["published"])
        calendar_day = today()
        day = calendar_day.isoformat()
        status = f.get("status")
        if status in ("upcoming", "ongoing", "ended"):
            where.append(VALID_DATES)
            expression, dates = {"upcoming": ("event_start>?", [day]),
                                 "ongoing": ("event_start<=? AND event_end>=?", [day, day]),
                                 "ended": ("event_end<?", [day])}[status]
            where.append(expression)
            args.extend(dates)
        elif status in ("pending", "rejected"):
            where.extend(["approval_status=?", "deleted_at IS NULL", "author_type='user'"]); args.append(status)
        elif status == "private":
            where.append("published=0")
        with closing(self.db()) as conn:
            rows, page = page_rows(conn, request, "*", "FROM news_posts", " AND ".join(where), args, "id DESC")
        return self.render(request, "issues.html", issues=[self.news(row, calendar_day) for row in rows], pagination=page, user_only=user_only, filters=f)

    def users(self, request):
        if response := self.guard(request): return response
        where, args = ["1=1"], []
        if q := request.query_params.get("q", "").strip()[:200]: where.append("instr(u.username || ' ' || u.nickname,?)>0"); args.append(q)
        if request.query_params.get("status") in ("active", "blocked", "deleted"):
            where.append("u.status=?"); args.append(request.query_params["status"])
        with closing(self.db()) as conn:
            select = "u.id,u.username,u.nickname,u.created_at,u.status,u.role,(SELECT count(*) FROM news_posts n WHERE n.author_id=u.id AND n.author_type='user' AND n.deleted_at IS NULL) AS post_count,(SELECT count(*) FROM places p WHERE p.submitted_by_user_id=u.id AND " + ACTIVE + ") AS place_count"
            rows, page = page_rows(conn, request, select, "FROM users u", " AND ".join(where), args, "u.id DESC")
        return self.render(request, "users.html", users=rows, pagination=page)

    def picks(self, request):
        if response := self.guard(request): return response
        year = integer(request.query_params.get("year"), today().year, 1900, 9999)
        month = integer(request.query_params.get("month"), today().month, 1, 12)
        with closing(self.db()) as conn:
            rows, page = page_rows(conn, request, "k.*,p.name,p.category,p.region,p.published,p.deleted_at,p.merged_into_id", "FROM place_pick_periods k JOIN places p ON p.id=k.place_id", "k.year=? AND k.month=?", [year, month], "k.sort_order,k.id")
            places = conn.execute("SELECT id,name,region FROM places p WHERE " + ACTIVE + " ORDER BY name").fetchall()
        return self.render(request, "picks.html", periods=rows, places=places, pagination=page, selected_year=year, selected_month=month)

    def categories(self, request, kind=None):
        from restaurant_taxonomy import usage_count
        if response := self.guard(request): return response
        kind = kind or request.query_params.get("type", "place")
        if kind not in KINDS: return HTMLResponse("분류를 찾을 수 없습니다.", status_code=404)
        with closing(self.db()) as conn:
            all_categories = [dict(r) for r in conn.execute('SELECT * FROM categories ORDER BY sort_order,id')]
            food = next((r for r in all_categories if r['slug']=='food'), None)
            restaurant_parents = ([food] + [r for r in all_categories if r['parent_id']==food['id'] and not r['is_legacy']]) if food else []
            if kind in TYPES:
                items = [dict(r) for r in conn.execute("SELECT c.*,(SELECT count(*) FROM places p WHERE p.category_id=c.id)+(SELECT count(*) FROM place_category_links l WHERE l.category_id=c.id) AS place_count,(SELECT count(*) FROM categories child WHERE child.parent_id=c.id) AS child_count FROM categories c WHERE c.type=? ORDER BY c.sort_order,c.id", (kind,))]
                by_id = {r['id']:r for r in all_categories}
                for item in items:
                    item['place_count'] = usage_count(conn,item['id'])
                    item['parent_name'] = by_id.get(item['parent_id'],{}).get('name','')
                if kind == 'restaurant_subcategory':
                    items.sort(key=lambda r: (r['is_legacy'], by_id[r['parent_id']]['sort_order'] if r['parent_id']!=food['id'] else r['sort_order'], r['parent_id'] if r['parent_id']!=food['id'] else r['id'], 0 if r['parent_id']==food['id'] else 1, r['sort_order'], r['id']))
            else:
                items = [{**i, "name": i["label"], "place_count": catalog_usage(conn, kind, i["value"]), "child_count": 0} for i in catalog(conn, kind, True)]
        q = request.query_params.get("q", "").strip()[:200]
        if q: items = [i for i in items if q.casefold() in i["name"].casefold()]
        page = paginate(request, len(items))
        return self.render(request, "classifications.html", kind=kind, kind_label=KINDS[kind], items=items[page["offset"]:page["offset"]+page["size"]], pagination=page, is_catalog=kind not in TYPES, restaurant_parents=restaurant_parents, food_id=food['id'] if food else None)

    def best(self, request):
        if response := self.guard(request): return response
        where, args = ["c.deleted_at IS NULL"], []
        if q := request.query_params.get("q", "").strip()[:200]: where.append("instr(c.title,?)>0"); args.append(q)
        if request.query_params.get("published") in ("0", "1"): where.append("c.published=?"); args.append(request.query_params["published"])
        with closing(self.db()) as conn:
            rows, page = page_rows(conn, request, "c.*,(SELECT count(*) FROM best_collection_places b JOIN places p ON p.id=b.place_id WHERE b.collection_id=c.id AND " + ACTIVE + ") AS place_count", "FROM best_collections c", " AND ".join(where), args, "c.sort_order,c.id")
        return self.render(request, "best.html", collections=rows, pagination=page)

    def register(self):
        app = self.app
        @app.get("/admin/places")
        async def admin_places_list(request: Request): return self.places(request)
        @app.get("/admin/places/stale")
        async def admin_stale_places(request: Request):
            return self.stale_places(request)

        @app.post("/admin/places/{place_id}/confirm-information")
        async def confirm_information(request: Request, place_id: int):
            if response := self.guard(request):
                return response
            if await self.checked(request) is None:
                return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
            stamp = today().isoformat()
            with closing(self.db()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT name,checked_at FROM places p WHERE p.id=? AND " + ACTIVE,
                                   (place_id,)).fetchone()
                if row is None:
                    return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
                if row["checked_at"] != stamp:
                    conn.execute("UPDATE places SET checked_at=? WHERE id=?", (stamp, place_id))
                    audit(conn, request, "장소 정보 확인 완료", "장소", place_id, row["name"])
            message = "정보 확인일이 업데이트되었습니다." if row["checked_at"] != stamp else "오늘 이미 정보를 확인했습니다."
            if "application/json" in request.headers.get("accept", ""):
                return JSONResponse({"checked_at": stamp, "message": message})
            self.flash(request, message, "success")
            return RedirectResponse(f"/admin/places/{place_id}/edit#freshness", status_code=303)

        @app.get("/admin/new")
        async def admin_new_list(request: Request): return self.places(request, "new")
        @app.get("/admin/suggestions")
        async def admin_suggestions_list(request: Request): return self.suggestions(request)
        @app.get("/admin/issues")
        async def admin_issues_list(request: Request): return self.issues(request)
        @app.get("/admin/classifications/{kind}")
        async def admin_classifications(request: Request, kind: str): return self.categories(request, kind)

        @app.post("/admin/classifications/{kind}")
        async def save_catalog(request: Request, kind: str):
            if response := self.guard(request): return response
            form = await self.checked(request)
            if form is None: return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
            if kind not in ("region", "companion", "cafe_focus"): return HTMLResponse("분류를 확인해주세요.", status_code=400)
            value, label, action = str(form.get("value", "")).strip(), str(form.get("name", "")).strip(), form.get("action")
            if action not in ("new", "edit", "delete") or not value or len(value)>40 or len(label)>40 or any(c in value for c in '\n\r,<>'):
                return HTMLResponse("분류 이름과 식별자를 확인해주세요.", status_code=400)
            if kind == "cafe_focus" and not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", value):
                return HTMLResponse("카페 성향 식별자는 영문 소문자로 입력해주세요.", status_code=400)
            with closing(self.db()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                items = catalog(conn, kind, True)
                old = next((i for i in items if i["value"] == value), None)
                if (action == "new" and old) or (action != "new" and not old):
                    return HTMLResponse("중복되거나 없는 분류입니다.", status_code=409)
                if action == "delete":
                    if catalog_usage(conn, kind, value): return HTMLResponse("사용 중인 분류는 삭제할 수 없습니다. 비활성화를 이용해주세요.", status_code=409)
                    items.remove(old)
                else:
                    if not label or label in ("전체", "상관없음") or any(i["label"] == label and i["value"] != value for i in items):
                        return HTMLResponse("중복되지 않는 표시 이름을 입력해주세요.", status_code=400)
                    order = integer(form.get("sort_order"), -1, 0, 9999)
                    if order < 0: return HTMLResponse("순서를 확인해주세요.", status_code=400)
                    item = dict(value=value, label=label, sort_order=order, is_active=form.get("is_active")=="1")
                    if old: items[items.index(old)] = item
                    else: items.append(item)
                write_setting(conn, "taxonomy:" + kind, items)
                audit(conn,request,"분류 변경","분류",None,kind)
            self.flash(request, "분류를 저장했습니다. 기존 장소의 값과 공유 URL은 유지됩니다.")
            return RedirectResponse("/admin/classifications/" + kind, 303)

        @app.get("/admin/settings/{section}")
        async def settings_get(request: Request, section: str):
            if response := self.guard(request): return response
            if section not in ("home", "general"): return HTMLResponse("설정을 찾을 수 없습니다.", status_code=404)
            return self.render(request, "settings.html", section=section)

        @app.post("/admin/settings/{section}")
        async def settings_save(request: Request, section: str):
            if response := self.guard(request): return response
            form = await self.checked(request)
            if form is None: return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
            if section == "home": values = {key: form.get(key)=="1" for key in DEFAULTS if key.startswith("home_")}
            elif section == "general":
                values = {key: str(form.get(key, "")).strip() for key in ("site_name", "instagram_url", "site_description")}
                values["user_place_auto_publish"] = form.get("user_place_auto_publish") == "1"
                if not values["site_name"] or len(values["site_name"])>40 or len(values["site_description"])>300 or len(values["instagram_url"])>2000 or not safe_external_url(values["instagram_url"]):
                    return HTMLResponse("사이트 이름·소개·링크를 확인해주세요.", status_code=400)
            else: return HTMLResponse("설정을 찾을 수 없습니다.", status_code=404)
            with closing(self.db()) as conn, conn:
                for key, value in values.items(): write_setting(conn, key, value)
                audit(conn,request,"운영 설정 변경","운영 설정",None,section)
            self.flash(request, "운영 설정을 저장했습니다.")
            return RedirectResponse("/admin/settings/" + section, 303)

        @app.get("/admin/best/new")
        async def best_new(request: Request):
            if response := self.guard(request): return response
            return self.render(request, "best_form.html", collection=None, selected_places=[], places=[])

        @app.get("/admin/best/{collection_id}/edit")
        async def best_edit(request: Request, collection_id: int):
            if response := self.guard(request): return response
            with closing(self.db()) as conn:
                c = conn.execute("SELECT * FROM best_collections WHERE id=? AND deleted_at IS NULL", (collection_id,)).fetchone()
                if not c: return HTMLResponse("모음을 찾을 수 없습니다.", status_code=404)
                selected = conn.execute("SELECT p.id,p.name,b.sort_order FROM best_collection_places b JOIN places p ON p.id=b.place_id WHERE b.collection_id=? AND " + ACTIVE + " ORDER BY b.sort_order,p.id", (collection_id,)).fetchall()
                places = conn.execute("SELECT id,name FROM places p WHERE " + ACTIVE + " ORDER BY name").fetchall()
            return self.render(request, "best_form.html", collection=c, selected_places=selected, places=places)

        @app.post("/admin/best/new")
        @app.post("/admin/best/{collection_id}/edit")
        async def best_save(request: Request, collection_id: int = 0):
            if response := self.guard(request): return response
            form = await self.checked(request)
            if form is None: return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
            title, description, image = [str(form.get(k, "")).strip() for k in ("title", "description", "image_url")]
            order = integer(form.get("sort_order"), -1, 0, 9999)
            if not title or len(title)>120 or len(description)>2000 or len(image)>2000 or not (safe_external_url(image) or re.fullmatch(r"/uploads/(places|issues)/[a-f0-9]+\.webp", image)) or order<0:
                return HTMLResponse("제목·소개·이미지 URL·순서를 확인해주세요.", status_code=400)
            with closing(self.db()) as conn, conn:
                values = (title, description, image, order, int(form.get("published")=="1"))
                creating = not collection_id
                if collection_id:
                    if not conn.execute("SELECT 1 FROM best_collections WHERE id=? AND deleted_at IS NULL", (collection_id,)).fetchone(): return HTMLResponse("모음을 찾을 수 없습니다.", status_code=404)
                    conn.execute("UPDATE best_collections SET title=?,description=?,image_url=?,sort_order=?,published=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (*values, collection_id))
                else:
                    collection_id = conn.execute("INSERT INTO best_collections(title,description,image_url,sort_order,published,slug,updated_at) VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)", (*values, "collection-"+uuid.uuid4().hex[:12])).lastrowid
                audit(conn,request,"BEST 등록" if creating else "BEST 수정","BEST",collection_id,title)
            self.flash(request, "BEST를 저장했습니다.")
            return RedirectResponse(f"/admin/best/{collection_id}/edit", 303)

        @app.post("/admin/best/{collection_id}/{action}")
        async def best_action(request: Request, collection_id: int, action: str):
            if response := self.guard(request): return response
            form = await self.checked(request)
            if form is None: return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
            if action not in ("copy", "toggle", "delete"): return HTMLResponse("지원하지 않는 작업입니다.", status_code=404)
            with closing(self.db()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                c = conn.execute("SELECT * FROM best_collections WHERE id=? AND deleted_at IS NULL", (collection_id,)).fetchone()
                if not c: return HTMLResponse("모음을 찾을 수 없습니다.", status_code=404)
                if action == "copy":
                    new_id = conn.execute("INSERT INTO best_collections(slug,title,description,image_url,emoji,sort_order,published,updated_at) VALUES (?,?,?,?,?,?,0,CURRENT_TIMESTAMP)", ("collection-"+uuid.uuid4().hex[:12], c["title"][:110]+" (복사)", c["description"], c["image_url"], c["emoji"], c["sort_order"])).lastrowid
                    conn.execute("INSERT INTO best_collection_places SELECT ?,place_id,sort_order FROM best_collection_places WHERE collection_id=?", (new_id, collection_id))
                elif action == "toggle": conn.execute("UPDATE best_collections SET published=1-published,updated_at=CURRENT_TIMESTAMP WHERE id=?", (collection_id,))
                else: conn.execute("UPDATE best_collections SET published=0,deleted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?", (collection_id,))
                audit(conn,request,{"copy":"BEST 복사","toggle":"BEST 공개 상태 변경","delete":"BEST 삭제"}[action],"BEST",new_id if action=="copy" else collection_id,c["title"])
            self.flash(request, "BEST를 복사했습니다. 비공개 상태로 저장됩니다." if action=="copy" else "BEST 상태를 변경했습니다. 연결된 장소는 유지됩니다.")
            return RedirectResponse("/admin/best", 303)
