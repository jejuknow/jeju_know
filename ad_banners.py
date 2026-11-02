"""Scheduled advertising placements for the homepage."""
from contextlib import closing
from datetime import date

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from admin_ui import page_rows
from auth_security import current_user, safe_external_url
from community_migrations import today
from image_limits import FORM_BYTES
from image_uploads import ImageError
from sqlite_migrations import verified_migration

SLOTS = {"SIDE-01": "홈 우측 세로", "HOME-01": "홈 내부 가로"}
TYPES = {"ad": "광고", "partnership": "제휴"}
SCHEDULE_LABELS = {"upcoming": "예정", "ongoing": "진행중", "ended": "종료"}
LIVE = "is_public=1 AND start_date<=:day AND end_date>=:day"


def migrate(database):
    with verified_migration(database, "20261102_home_ads", "jejuno-before-ad-banner") as conn:
        if conn is None:
            return
        conn.execute("""CREATE TABLE ads (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, advertiser_name TEXT NOT NULL,
            ad_type TEXT NOT NULL CHECK(ad_type IN ('ad','partnership')),
            slot TEXT NOT NULL CHECK(slot IN ('SIDE-01','HOME-01')), title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '', image_path TEXT NOT NULL UNIQUE,
            image_width INTEGER NOT NULL CHECK(image_width>0), image_height INTEGER NOT NULL CHECK(image_height>0),
            target_url TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL CHECK(end_date>=start_date),
            priority INTEGER NOT NULL DEFAULT 1 CHECK(priority BETWEEN 1 AND 9999),
            is_public INTEGER NOT NULL DEFAULT 0 CHECK(is_public IN (0,1)), revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("CREATE INDEX ads_display_order ON ads(slot,priority,id) WHERE is_public=1")


def active_banners(conn, day):
    banners = {}
    for slot in SLOTS:
        row = conn.execute(f"SELECT * FROM ads WHERE slot=:slot AND {LIVE} ORDER BY priority,id LIMIT 1",
                           {"slot": slot, "day": day.isoformat()}).fetchone()
        if row:
            banners[slot] = {**dict(row), "label": TYPES[row["ad_type"]]}
    return banners


def banner_image(request, store, filename):
    relative = "ads/" + filename
    try:
        path = store.path(relative)
    except ImageError:
        return Response(status_code=404)
    member = current_user(request)
    admin = request.session.get("admin_id") or (member and member["role"] == "admin")
    where = "image_path=:path" if admin else "image_path=:path AND " + LIVE
    with closing(store.connect_db()) as conn:
        allowed = conn.execute("SELECT 1 FROM ads WHERE " + where,
                               {"path": relative, "day": today().isoformat()}).fetchone()
    if not allowed or not path.is_file():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})


def validate(values):
    if not values["name"] or not values["advertiser_name"]:
        return "광고명과 광고주명을 입력해주세요."
    if values["ad_type"] not in TYPES or values["slot"] not in SLOTS:
        return "광고 유형과 위치를 선택해주세요."
    if any(len(values[key]) > limit for key, limit in (("name",100), ("advertiser_name",100), ("title",120), ("description",500))):
        return "광고명과 광고주명은 100자, 제목은 120자, 설명은 500자까지 입력할 수 있습니다."
    if not values["target_url"] or not safe_external_url(values["target_url"]):
        return "연결 URL은 올바른 http 또는 https 주소를 입력해주세요."
    try:
        for key in ("start_date", "end_date"):
            if date.fromisoformat(values[key]).isoformat() != values[key]:
                raise ValueError
    except ValueError:
        return "시작일과 종료일을 날짜 형식으로 입력해주세요."
    if values["end_date"] < values["start_date"]:
        return "종료일은 시작일과 같거나 그 이후여야 합니다."
    try:
        values["priority"] = int(values["priority"])
        if not 1 <= values["priority"] <= 9999:
            raise ValueError
    except ValueError:
        return "우선순위는 1~9999 사이의 정수를 입력해주세요."


def register_ad_routes(app, templates, connect_db, admin_guard, require_csrf, flash, store):
    def render(request, name, status=200, **context):
        return templates.TemplateResponse(request=request, name="admin/" + name,
            context={"slots":SLOTS, "ad_types":TYPES, **context}, status_code=status)

    def get_ad(ad_id):
        with closing(connect_db()) as conn:
            row = conn.execute("SELECT * FROM ads WHERE id=?", (ad_id,)).fetchone()
        if not row:
            raise HTTPException(404, "광고를 찾을 수 없습니다.")
        return dict(row)

    @app.get("/admin/ads")
    def ad_list(request: Request):
        if guard := admin_guard(request):
            return guard
        day = today().isoformat()
        source = """FROM (SELECT ads.*,
            CASE WHEN start_date > ? THEN 'upcoming' WHEN end_date < ? THEN 'ended'
                 ELSE 'ongoing' END AS schedule FROM ads) AS inventory"""
        args = [day, day]
        filters = {}
        where = []
        for key, column, allowed in (("status", "schedule", SCHEDULE_LABELS),
                                     ("slot", "slot", SLOTS), ("public", "is_public", ("0", "1"))):
            value = request.query_params.get(key)
            if value in allowed:
                filters[key] = value
                where.append(column + "=?")
                args.append(value)
        order = """CASE schedule WHEN 'ongoing' THEN 0 WHEN 'upcoming' THEN 1 ELSE 2 END,
                   priority ASC, julianday(updated_at) DESC, id DESC"""
        with closing(connect_db()) as conn:
            rows, pagination = page_rows(conn, request, "*", source, " AND ".join(where) or "1=1", args, order)
        return render(request, "ads.html", ads=[{**dict(r), "status": SCHEDULE_LABELS[r["schedule"]]} for r in rows],
                      pagination=pagination, filters=filters, schedules=SCHEDULE_LABELS)

    @app.get("/admin/ads/new")
    def ad_new(request: Request):
        if guard := admin_guard(request):
            return guard
        return render(request, "ad_form.html", item={"priority":1,"ad_type":"ad","slot":"HOME-01"})

    @app.get("/admin/ads/{ad_id}/edit")
    def ad_edit(request: Request, ad_id: int):
        if guard := admin_guard(request):
            return guard
        return render(request, "ad_form.html", item=get_ad(ad_id))

    @app.post("/admin/ads/new")
    @app.post("/admin/ads/{ad_id}/edit")
    async def ad_save(request: Request):
        if guard := admin_guard(request):
            return guard
        # Read the id only from the matched route, never from query or form parameters.
        ad_id = request.path_params.get("ad_id")
        existing = get_ad(ad_id) if ad_id is not None else {}
        form = await request.form(max_files=1, max_fields=20, max_part_size=FORM_BYTES)
        if not require_csrf(request, form):
            raise HTTPException(400, "CSRF 검증에 실패했습니다.")
        fields = ("name","advertiser_name","ad_type","slot","title","description","target_url","start_date","end_date","priority")
        values = {key:str(form.get(key, "")).strip() for key in fields}
        values["is_public"] = int(form.get("is_public") == "1")
        def invalid(message):
            return render(request, "ad_form.html", 400, item={**existing, **values}, error=message)
        if existing and str(existing["revision"]) != form.get("revision"):
            raise HTTPException(409, "다른 곳에서 광고를 수정했습니다. 새로고침 후 다시 확인해주세요.")
        if error := validate(values):
            return invalid(error)
        try:
            plan = await store.prepare(form, "ads")
            with plan.transaction() as conn:
                photo = plan.new.get(0)
                if photo:
                    values.update(image_path=photo["file_path"], image_width=photo["width"], image_height=photo["height"])
                elif not existing or 0 in plan.remove:
                    raise ImageError("광고 이미지 1장을 선택해주세요.")
                if existing:
                    sets = ",".join(key + "=?" for key in values)
                    changed = conn.execute(f"UPDATE ads SET {sets},updated_at=CURRENT_TIMESTAMP,revision=revision+1 WHERE id=? AND revision=?",
                                           (*values.values(), ad_id, existing["revision"]))
                    if not changed.rowcount:
                        raise HTTPException(409, "광고가 변경되었습니다. 새로고침 후 다시 확인해주세요.")
                    if photo:
                        conn.execute("INSERT OR IGNORE INTO image_file_gc(file_path) VALUES (?)", (existing["image_path"],))
                else:
                    marks = ",".join("?" for _ in values)
                    conn.execute(f"INSERT INTO ads ({','.join(values)}) VALUES ({marks})", tuple(values.values()))
        except ImageError as exc:
            return invalid(str(exc))
        flash(request, "광고를 저장했습니다.")
        return RedirectResponse("/admin/ads", status_code=303)
