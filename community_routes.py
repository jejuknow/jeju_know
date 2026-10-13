"""Member place facts and administrator-only editorial workflows."""
import json
import secrets
import sqlite3
from contextlib import closing
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from auth_security import current_user, rate_limited, safe_external_url
from community_migrations import today
from place_details import validate_detail_value
from community_places import (FACT_FIELDS, SUGGESTION_FIELDS, VERIFICATION_LABELS, coordinates,
                              duplicates, editable, facts, merge_places, opening)
from image_uploads import FORM_BYTES, ImageError, ImagePlan
from place_taxonomy import linked_ids, save_links, validate_terms


def register_community_routes(app, templates, connect_db, require_csrf, flash, admin_guard,
                              row_to_place, form_options, regions, secret, image_store):
    def render(request, template, status=200, **context):
        return templates.TemplateResponse(request=request, name=template, context={"current_year": today().year,
            "current_month": today().month, "suggestion_fields": SUGGESTION_FIELDS,
            "verification_labels": VERIFICATION_LABELS, **context}, status_code=status)

    def redirect(url):
        return RedirectResponse(url, status_code=303)

    def member_guard(request):
        if not current_user(request):
            return redirect("/login?next=" + quote(request.url.path + ("?" + request.url.query if request.url.query else ""), safe=""))

    def actor(request):
        return "admin:" + str(request.session["admin_id"]) if request.session.get("admin_id") else "user:" + str(current_user(request)["id"])

    async def checked(request, files=0):
        form = await request.form(max_files=files, max_fields=60, max_part_size=FORM_BYTES)
        return form if require_csrf(request, form) else None

    def bad_csrf():
        return HTMLResponse("CSRF 검증에 실패했습니다. 새로고침 후 다시 시도해주세요.", status_code=400)

    def limited(request, namespace):
        member = current_user(request)
        return rate_limited(connect_db, secret, namespace, [("user", str(member["id"])),
            ("ip", request.client.host if request.client else "unknown")], limit=30, window=3600)

    def get_place(conn, place_id):
        return conn.execute("SELECT * FROM places WHERE id=? AND deleted_at IS NULL AND merged_into_id IS NULL", (place_id,)).fetchone()

    def collection_list(conn, public=True):
        return [dict(r) for r in conn.execute("SELECT * FROM best_collections" + (" WHERE published=1" if public else "") + " ORDER BY sort_order,id")]

    @app.get("/find")
    async def find_page(request: Request):
        return render(request, "find.html")

    @app.get("/new")
    async def new_page(request: Request, category: str = ""):
        return render(request, "new_places.html", selected_category=category if category in ("cafe","food") else "")

    @app.get("/pick")
    @app.get("/picks")
    async def legacy_pick():
        return RedirectResponse("/map?pick=current", status_code=307)

    @app.get("/best")
    async def best_page(request: Request):
        with closing(connect_db()) as conn:
            collections = collection_list(conn)
        return render(request, "best.html", collections=collections, collection=None)

    @app.get("/best/{slug}")
    async def best_detail(request: Request, slug: str):
        with closing(connect_db()) as conn:
            collection = conn.execute("SELECT * FROM best_collections WHERE slug=? AND published=1", (slug,)).fetchone()
            if not collection:
                return HTMLResponse("추천 모음을 찾을 수 없습니다.", status_code=404)
            rows = conn.execute("""SELECT p.* FROM best_collection_places b JOIN places p ON p.id=b.place_id
                WHERE b.collection_id=? AND p.published=1 AND p.deleted_at IS NULL AND p.merged_into_id IS NULL ORDER BY b.sort_order,p.id""", (collection["id"],)).fetchall()
        return render(request, "best.html", collection=dict(collection), places=[row_to_place(r) for r in rows])

    def place_form(request, data=None, *, status=200, error=None, similar=None, new_only=False):
        return render(request, "member_place_form.html", status, place=data, error=error,
                      similar=similar or [], new_only=new_only, **form_options(data.get("category_id") if data else None))

    @app.get("/places/new")
    async def legacy_submit_get(request: Request):
        query = str(request.query_params)
        return redirect("/map/register" + ("?" + query if query else ""))

    @app.get("/map/register")
    async def submit_get(request: Request):
        # Keep the established administrator form and permissions intact.
        if not current_user(request) and request.session.get("admin_id"):
            return redirect("/admin/places/new")
        if response := member_guard(request):
            return response
        query = request.query_params
        new_only = query.get("source") == "new" or query.get("from") == "new"
        with closing(connect_db()) as conn:
            category = conn.execute("SELECT id,slug FROM categories WHERE slug=? AND type='place' AND is_active=1", (query.get("category", ""),)).fetchone()
            if new_only and category and category["slug"] not in ("cafe", "food"):
                category = None
            data = {"category_id": category["id"] if category else None,
                    "region": query.get("region", "") if query.get("region") in regions else "", "term_ids": []}
            if category:
                slugs = query.get("detail", "").split(",")
                data["term_ids"] = [r["id"] for r in conn.execute("SELECT id,slug FROM categories WHERE parent_id=? AND type!='place' AND is_active=1", (category["id"],)) if r["slug"] in slugs]
        return place_form(request, data, new_only=new_only)

    @app.get("/places/{place_id}/edit")
    async def submit_edit_get(request: Request, place_id: int):
        if response := member_guard(request):
            return response
        with closing(connect_db()) as conn:
            place = get_place(conn, place_id)
            term_ids = linked_ids(conn, place_id)
        if not place:
            return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
        if not editable(place, current_user(request)["id"]):
            return HTMLResponse("본인이 등록한 미확인 장소만 수정할 수 있습니다. 확인된 장소는 수정 제안을 이용해주세요.", status_code=403)
        return place_form(request, {**row_to_place(place), "term_ids": term_ids})

    async def save_submission(request, place_id=None):
        if response := member_guard(request):
            return response
        form = await checked(request, 5)
        if form is None:
            return bad_csrf()
        # Reject every non-fact field, including forged editorial/ownership/visibility values.
        allowed = set(FACT_FIELDS) | {"csrf_token", "from_new", "duplicate_ack", "taxonomy_present", "term_ids"} | {f"image_{n}" for n in range(5)} | {f"remove_image_{n}" for n in range(5)}
        if any(k not in allowed for k in form):
            return HTMLResponse("사용자 장소 등록에서는 기본 정보와 사진만 변경할 수 있습니다.", status_code=403)
        if limited(request, "community-place"):
            return HTMLResponse("등록 요청이 많습니다. 잠시 후 다시 시도해주세요.", status_code=429)
        user_id = current_user(request)["id"]
        new_only = form.get("from_new") == "1"
        with closing(connect_db()) as conn:
            existing = get_place(conn, place_id) if place_id else None
            if place_id and (not existing or not editable(existing, user_id)):
                return HTMLResponse("본인의 미확인 장소만 수정할 수 있습니다.", status_code=403)
            try:
                data = facts(form, conn, regions, new_only=new_only, existing=existing)
                data.update(term_ids=form.getlist("term_ids") if form.get("taxonomy_present") == "1" else None, features=[])
                if error := validate_terms(conn, data, existing):
                    raise ValueError(error)
            except ValueError as exc:
                values = {k: str(form.get(k, "")) for k in FACT_FIELDS}
                values.update(id=place_id, term_ids=[int(v) for v in form.getlist("term_ids") if str(v).isascii() and str(v).isdigit() and len(str(v)) <= 10], images=image_store.list("places", place_id) if place_id else [])
                return place_form(request, values, status=400, error=str(exc), new_only=new_only)
            similar = duplicates(conn, data, place_id)
        data.update(id=place_id, images=image_store.list("places", place_id) if place_id else [])
        if similar and form.get("duplicate_ack") != "1":
            return place_form(request, data, status=409, error="비슷한 장소가 이미 등록되어 있습니다. 기존 장소를 먼저 확인해주세요. 사진은 다시 선택해주세요.", similar=similar, new_only=new_only)
        try:
            plan = await image_store.prepare(form, "places")
        except ImageError as exc:
            return place_form(request, data, status=400, error=str(exc), new_only=new_only)
        with plan.transaction() as conn:
            # Recheck under the write lock: an administrator may have verified/merged it meanwhile.
            fresh = get_place(conn, place_id) if place_id else None
            if place_id and (not fresh or not editable(fresh, user_id)):
                plan.discard()
                return HTMLResponse("정보 확인 상태가 변경되었습니다. 수정 제안을 이용해주세요.", status_code=403)
            if error := validate_terms(conn, data, fresh):
                plan.discard()
                return HTMLResponse(error + " 새로고침 후 다시 시도해주세요.", status_code=400)
            if place_id:
                fields = (*FACT_FIELDS, "category")
                conn.execute("UPDATE places SET " + ",".join(k + "=?" for k in fields) + ",updated_at=CURRENT_TIMESTAMP WHERE id=?", (*[data[k] for k in fields],place_id))
                slug = fresh["slug"]
            else:
                slug = "place-" + secrets.token_hex(12)
                fields = (*FACT_FIELDS, "category")
                place_id = conn.execute("INSERT INTO places (" + ",".join(fields) + ",slug,submitted_by_user_id,source_type,verification_status,published) VALUES (" + ",".join("?" for _ in fields) + ",?,?,'user','unverified',1)", (*[data[k] for k in fields],slug,user_id)).lastrowid
            save_links(conn, place_id, data["term_ids"])
            plan.apply(conn, place_id)
        flash(request, "장소를 저장했습니다. 사용자 등록 · 미확인으로 표시됩니다.")
        return redirect("/place/" + slug)

    @app.post("/map/register")
    @app.post("/places/new")
    async def submit_post(request: Request):
        return await save_submission(request)

    @app.post("/places/{place_id}/edit")
    async def submit_edit_post(request: Request, place_id: int):
        return await save_submission(request, place_id)

    @app.post("/places/{place_id}/delete")
    async def submit_delete(request: Request, place_id: int):
        if response := member_guard(request):
            return response
        if await checked(request) is None:
            return bad_csrf()
        with closing(connect_db()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            place = get_place(conn, place_id)
            if not place or not editable(place, current_user(request)["id"]):
                return HTMLResponse("본인의 미확인 장소만 삭제할 수 있습니다.", status_code=403)
            # Soft delete retains history/photos/relations for administrator recovery.
            conn.execute("UPDATE places SET published=0,deleted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?", (place_id,))
        flash(request, "장소를 삭제했습니다. 공개 화면에서 더 이상 표시되지 않습니다.")
        return redirect("/account/places")

    @app.get("/account/places")
    async def my_places(request: Request):
        if response := member_guard(request):
            return response
        with closing(connect_db()) as conn:
            rows = conn.execute("SELECT * FROM places WHERE submitted_by_user_id=? AND deleted_at IS NULL ORDER BY id DESC", (current_user(request)["id"],)).fetchall()
            suggestions = conn.execute("SELECT s.*,p.name,p.slug FROM place_correction_suggestions s JOIN places p ON p.id=s.place_id WHERE s.user_id=? ORDER BY s.id DESC", (current_user(request)["id"],)).fetchall()
        return render(request, "member_places.html", places=[{**row_to_place(r),"can_edit":editable(r,current_user(request)["id"])} for r in rows], suggestions=suggestions)

    def suggestion_value(conn, field, raw):
        value = raw.strip()
        if field not in SUGGESTION_FIELDS or not value or len(value) > 2000:
            raise ValueError("제안 항목과 변경 내용을 확인해주세요. 2,000자까지 입력할 수 있습니다.")
        validate_detail_value(field, value)
        if field == "category_id":
            category = conn.execute("SELECT id FROM categories WHERE id=? AND type='place' AND is_active=1", (value,)).fetchone()
            if not category:
                raise ValueError("활성 카테고리를 선택해주세요.")
            value = str(category[0])
        elif field == "opening":
            parts = value.split("-")
            if len(parts) > 2:
                raise ValueError("오픈 시기는 연도 또는 연도-월로 입력해주세요.")
            y, m = opening(parts[0], parts[1] if len(parts) == 2 else None)
            value = json.dumps([y,m])
        elif field == "coordinates":
            parts = value.split(",")
            if len(parts) != 2:
                raise ValueError("지도 위치는 위도,경도 형식으로 입력해주세요.")
            value = json.dumps(coordinates(*parts))
        elif field in ("map_url","website_url") and not safe_external_url(value):
            raise ValueError("링크는 http 또는 https URL로 입력해주세요.")
        elif field == "name" and len(value) > 120 or field == "address" and len(value) > 300:
            raise ValueError("장소명은 120자, 주소는 300자 이내로 입력해주세요.")
        return value

    def original_value(place, field):
        if field == "opening":
            return json.dumps([place["opened_year"],place["opened_month"]])
        if field == "coordinates":
            return json.dumps([place["latitude"],place["longitude"]])
        if field in ("closed","business","other"):
            return ""
        return str(place[field] or "")

    @app.get("/places/{place_id}/suggest")
    async def suggest_get(request: Request, place_id: int):
        if response := member_guard(request):
            return response
        with closing(connect_db()) as conn:
            place = get_place(conn, place_id)
        if not place or not place["published"]:
            return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
        return render(request, "place_suggestion.html", place=dict(place), **form_options(place["category_id"]))

    @app.post("/places/{place_id}/suggest")
    async def suggest_post(request: Request, place_id: int):
        if response := member_guard(request):
            return response
        form = await checked(request)
        if form is None:
            return bad_csrf()
        if limited(request, "place-suggestion"):
            return HTMLResponse("요청이 많습니다. 잠시 후 다시 시도해주세요.", status_code=429)
        with closing(connect_db()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            place = get_place(conn, place_id)
            if not place or not place["published"]:
                return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
            field, reason = str(form.get("field", "")), str(form.get("reason", "")).strip()
            try:
                value = suggestion_value(conn, field, str(form.get("new_value", "")))
                if not 5 <= len(reason) <= 2000:
                    raise ValueError("확인할 근거를 5~2,000자로 입력해주세요.")
            except ValueError as exc:
                return render(request, "place_suggestion.html", 400, place=dict(place), error=str(exc), **form_options(place["category_id"]))
            conn.execute("INSERT INTO place_correction_suggestions(place_id,user_id,field,new_value,original_value,reason) VALUES (?,?,?,?,?,?)", (place_id,current_user(request)["id"],field,value,original_value(place,field),reason))
        flash(request, "수정 제안을 보냈습니다. 관리자가 확인하기 전에는 원본 정보가 바뀌지 않습니다.")
        return redirect("/account/places")

    @app.get("/admin/community")
    async def community_admin(request: Request, status: str = "unverified"):
        if response := admin_guard(request):
            return response
        with closing(connect_db()) as conn:
            clauses = {"unverified":"p.source_type='user' AND p.verification_status='unverified'", "user":"p.source_type='user'", "verified":"p.verification_status!='unverified'", "hidden":"p.published=0", "all":"1=1", "duplicates":"1=1"}
            rows = conn.execute("SELECT p.*,u.nickname FROM places p LEFT JOIN users u ON u.id=p.submitted_by_user_id WHERE p.deleted_at IS NULL AND p.merged_into_id IS NULL AND " + clauses.get(status,clauses["unverified"]) + " ORDER BY p.id DESC").fetchall()
            items = []
            for r in rows:
                matches = duplicates(conn, dict(r), r["id"], True)
                if status != "duplicates" or matches:
                    items.append({**row_to_place(r),"nickname":r["nickname"],"similar":matches})
            suggestions = conn.execute("SELECT s.*,p.name,p.slug,u.nickname FROM place_correction_suggestions s JOIN places p ON p.id=s.place_id JOIN users u ON u.id=s.user_id ORDER BY CASE WHEN s.status='pending' THEN 0 ELSE 1 END,s.id DESC").fetchall()
        return render(request, "admin_community.html", places=items, suggestions=suggestions, selected_status=status)

    @app.post("/admin/suggestions/{suggestion_id}/review")
    async def review_suggestion(request: Request, suggestion_id: int):
        if response := admin_guard(request):
            return response
        form = await checked(request)
        if form is None:
            return bad_csrf()
        action, note = form.get("action"), str(form.get("review_note", "")).strip()
        if action not in ("accept","reject") or len(note) > 2000:
            return HTMLResponse("처리 방식과 메모를 확인해주세요.", status_code=400)
        with closing(connect_db()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            suggestion = conn.execute("SELECT * FROM place_correction_suggestions WHERE id=? AND status='pending'", (suggestion_id,)).fetchone()
            if not suggestion:
                return HTMLResponse("이미 처리되었거나 없는 제안입니다.", status_code=409)
            place = get_place(conn, suggestion["place_id"])
            if action == "accept":
                if not place:
                    return HTMLResponse("삭제·병합된 장소를 먼저 확인해주세요.", status_code=409)
                field, value = suggestion["field"], suggestion["new_value"]
                if field not in SUGGESTION_FIELDS:
                    return HTMLResponse("지원하지 않는 제안 항목입니다.", status_code=400)
                if original_value(place, field) != suggestion["original_value"]:
                    return HTMLResponse("제안 이후 원본 정보가 변경되었습니다. 최신 장소를 확인하고 이 제안을 거절하거나 새 제안으로 처리해주세요.", status_code=409)
                if field in ("opening","coordinates"):
                    parts = json.loads(value)
                    values = opening(*parts) if field == "opening" else coordinates(*parts)
                    columns = ("opened_year","opened_month") if field == "opening" else ("latitude","longitude")
                    conn.execute(f"UPDATE places SET {columns[0]}=?,{columns[1]}=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (*values,place["id"]))
                elif field == "closed":
                    conn.execute("UPDATE places SET published=0,updated_at=CURRENT_TIMESTAMP WHERE id=?", (place["id"],))
                elif field in ("business", "other"):
                    if not note:
                        return HTMLResponse("영업 관련 정보·기타 제안은 장소 수정에서 반영한 내용을 처리 메모에 기록해주세요.", status_code=400)
                else:
                    try:
                        value = suggestion_value(conn, field, value)
                    except ValueError as exc:
                        return HTMLResponse(str(exc), status_code=400)
                    conn.execute(f"UPDATE places SET {field}=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (value,place["id"]))
                    if field == "category_id":
                        conn.execute("UPDATE places SET category=(SELECT name FROM categories WHERE id=?) WHERE id=?", (value,place["id"]))
                        conn.execute("DELETE FROM place_category_links WHERE place_id=?", (place["id"],))
            conn.execute("UPDATE place_correction_suggestions SET status=?,reviewed_at=CURRENT_TIMESTAMP,reviewed_by=?,review_note=? WHERE id=?", ("accepted" if action == "accept" else "rejected",actor(request),note,suggestion_id))
        flash(request, "제안을 반영했습니다." if action == "accept" else "제안을 거절했습니다.")
        return redirect("/admin/community#suggestions")

    @app.get("/admin/places/{place_id}/merge")
    async def merge_get(request: Request, place_id: int, target_id: int = 0):
        if response := admin_guard(request):
            return response
        with closing(connect_db()) as conn:
            source = get_place(conn, place_id)
            if not source:
                return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
            candidates = conn.execute("SELECT id,name,address FROM places WHERE id<>? AND merged_into_id IS NULL AND deleted_at IS NULL ORDER BY name", (place_id,)).fetchall()
            target = get_place(conn, target_id) if target_id else None
        return render(request, "admin_place_merge.html", source=row_to_place(source), target=row_to_place(target) if target else None, candidates=candidates)

    @app.post("/admin/places/{place_id}/merge")
    async def merge_post(request: Request, place_id: int):
        if response := admin_guard(request):
            return response
        form = await checked(request)
        if form is None:
            return bad_csrf()
        if form.get("confirm_merge") != "1":
            return HTMLResponse("병합할 원본과 유지할 장소를 확인해주세요.", status_code=400)
        try:
            target_id = int(form.get("target_id", ""))
            with closing(connect_db()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                merge_places(conn, place_id, target_id, actor(request))
        except (ValueError, sqlite3.IntegrityError) as exc:
            return HTMLResponse("병합할 장소를 다시 확인해주세요." if isinstance(exc,sqlite3.IntegrityError) else str(exc), status_code=400)
        flash(request, "장소를 병합했습니다. 이전 링크·사진·제안·PICK 기록과 추천 모음 연결은 보존됩니다.")
        return redirect(f"/admin/places/{target_id}/edit")

    @app.get("/admin/picks")
    async def picks_admin(request: Request):
        if response := admin_guard(request):
            return response
        with closing(connect_db()) as conn:
            places = conn.execute("SELECT id,name,region FROM places WHERE merged_into_id IS NULL AND deleted_at IS NULL ORDER BY name").fetchall()
            periods = conn.execute("SELECT k.*,p.name,p.slug,p.merged_into_id,p.deleted_at FROM place_pick_periods k JOIN places p ON p.id=k.place_id ORDER BY year DESC,month DESC,sort_order,k.id").fetchall()
        return render(request, "admin_picks.html", places=places, periods=periods)

    @app.post("/admin/picks")
    async def picks_save(request: Request):
        if response := admin_guard(request):
            return response
        form = await checked(request)
        if form is None:
            return bad_csrf()
        try:
            place_id, year, month, order = [int(str(form.get(k,""))) for k in ("place_id","year","month","sort_order")]
            note = str(form.get("pick_note", "")).strip()
            if not 1900 <= year <= 9999 or not 1 <= month <= 12 or not 0 <= order <= 9999 or len(note) > 2000:
                raise ValueError
        except ValueError:
            return HTMLResponse("적용 연도·월, 순서(0~9999), 설명(2,000자)을 확인해주세요.", status_code=400)
        with closing(connect_db()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if not get_place(conn, place_id):
                return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
            active = int(form.get("is_active") == "1")
            conn.execute("""INSERT INTO place_pick_periods(place_id,year,month,sort_order,pick_note,is_active) VALUES (?,?,?,?,?,?)
                ON CONFLICT(place_id,year,month) DO UPDATE SET sort_order=excluded.sort_order,pick_note=excluded.pick_note,is_active=excluded.is_active""", (place_id,year,month,order,note,active))
            if (year,month) == (today().year,today().month):
                conn.execute("UPDATE places SET is_pick=? WHERE id=?", (active,place_id))
        flash(request, "월별 PICK을 저장했습니다. 과거 기록은 유지됩니다.")
        return redirect("/admin/picks")

    @app.get("/admin/best")
    async def best_admin(request: Request):
        if response := admin_guard(request):
            return response
        with closing(connect_db()) as conn:
            collections = collection_list(conn, False)
            places = conn.execute("SELECT id,name FROM places WHERE merged_into_id IS NULL AND deleted_at IS NULL ORDER BY name").fetchall()
            for collection in collections:
                collection["places"] = conn.execute("SELECT p.id,p.name,b.sort_order FROM best_collection_places b JOIN places p ON p.id=b.place_id WHERE b.collection_id=? AND p.merged_into_id IS NULL ORDER BY b.sort_order,p.id", (collection["id"],)).fetchall()
        return render(request, "admin_best.html", collections=collections, places=places)

    @app.post("/admin/best/{collection_id}/places")
    async def best_membership(request: Request, collection_id: int):
        if response := admin_guard(request):
            return response
        form = await checked(request)
        if form is None:
            return bad_csrf()
        try:
            place_id, order = int(form.get("place_id", "")), int(form.get("sort_order", "0"))
            if not 0 <= order <= 9999 or form.get("action") not in ("add","remove"):
                raise ValueError
        except ValueError:
            return HTMLResponse("장소와 순서를 확인해주세요.", status_code=400)
        with closing(connect_db()) as conn, conn:
            place_exists = (get_place(conn, place_id) if form.get("action") == "add" else conn.execute("SELECT id FROM places WHERE id=?", (place_id,)).fetchone())
            if not conn.execute("SELECT 1 FROM best_collections WHERE id=?", (collection_id,)).fetchone() or not place_exists:
                return HTMLResponse("장소 또는 모음을 찾을 수 없습니다.", status_code=404)
            if form.get("action") == "remove":
                conn.execute("DELETE FROM best_collection_places WHERE collection_id=? AND place_id=?", (collection_id,place_id))
            else:
                conn.execute("INSERT INTO best_collection_places VALUES (?,?,?) ON CONFLICT(collection_id,place_id) DO UPDATE SET sort_order=excluded.sort_order", (collection_id,place_id,order))
        flash(request, "추천 모음 구성을 저장했습니다.")
        return redirect("/admin/best")
