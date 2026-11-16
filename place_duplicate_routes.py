"""Administrator decisions and minimal member-facing duplicate hints."""
from contextlib import closing

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from admin_roles import audit, identity
from auth_security import current_user, rate_limited
from place_duplicates import (ACTIVE, LEVELS, groups, compare, pair, ignores, public_candidate,
                              find_duplicate_candidates, calculate_place_distance)

COMPARE_FIELDS = [("name","장소명"),("category","카테고리"),("region_label","지역"),("address","주소"),
    ("phone","전화번호"),("coordinates","좌표"),("restaurant_types","맛집 대분류 / 세부유형"),
    ("cafe_types","카페 특징"),("business_hours","영업시간"),("closed_days","휴무일"),
    ("signature_menu","대표메뉴"),("parking_status","주차"),("pet_status","반려동물"),
    ("child_status","아이동반"),("accessible_status","무장애"),("note","꿀팁"),("source_label","등록주체"),
    ("verification_label","JEJU_KNOW 확인"),("public_label","공개여부"),("pick_label","현재 PICK"),
    ("opening_label","오픈일"),("created_at","등록일"),("updated_at","수정일")]


def comparison_rows(left, right, fields=COMPARE_FIELDS):
    return [{"label":label,"left":left.get(key) or "—","right":right.get(key) or "—",
             "different":(left.get(key) or "")!=(right.get(key) or "")} for key,label in fields]


def register(app, templates, db, guard, csrf, flash, decorate, secret):
    def get_place(conn, place_id):
        if not 0 < place_id < 2**63: return None
        row = conn.execute("SELECT * FROM places WHERE id=? AND " + ACTIVE,(place_id,)).fetchone()
        return dict(row) if row else None

    def display(conn, row):
        place = decorate(row)
        terms = conn.execute("SELECT c.name,c.type FROM categories c JOIN place_category_links l ON l.category_id=c.id WHERE l.place_id=? ORDER BY c.sort_order,c.id",(row["id"],)).fetchall()
        place.update(restaurant_types=" · ".join(r["name"] for r in terms if r["type"]=="restaurant_subcategory"),
            cafe_types=" · ".join(r["name"] for r in terms if r["type"]=="cafe_feature"),
            coordinates=f'{row["latitude"]}, {row["longitude"]}' if row["latitude"] is not None and row["longitude"] is not None else "",
            source_label="사용자" if row["source_type"]=="user" else "관리자",
            verification_label="JEJU_KNOW 확인" if row["verification_status"]=="jejuno_verified" else "미확인",
            public_label="공개" if row["published"] else "비공개",pick_label="PICK" if place["is_pick"] else "—")
        return place

    @app.get("/admin/places/duplicates")
    def duplicates_page(request: Request):
        if response := guard(request): return response
        level = request.query_params.get("level", "all")
        with closing(db()) as conn:
            items = groups(conn)
            filtered = [g for g in items if level not in LEVELS or g["level"]==level]
            excluded = [dict(r) for r in conn.execute("""SELECT i.*,a.name AS left_name,b.name AS right_name,
                COALESCE(ad.display_name,u.nickname,'이전 관리자') AS actor
                FROM place_duplicate_ignores i JOIN places a ON a.id=i.place_low JOIN places b ON b.id=i.place_high
                LEFT JOIN admins ad ON ad.id=i.ignored_by LEFT JOIN users u ON u.id=i.ignored_by_member
                WHERE a.deleted_at IS NULL AND b.deleted_at IS NULL AND a.merged_into_id IS NULL AND b.merged_into_id IS NULL
                ORDER BY i.ignored_at DESC""")]
        return templates.TemplateResponse(request=request,name="admin/duplicates.html",context={"groups":filtered,
            "levels":LEVELS,"selected":level,"group_count":len(items),"ignored":excluded})

    @app.get("/admin/places/duplicates/compare/{left}/{right}")
    def comparison(request: Request, left:int, right:int):
        if response := guard(request): return response
        with closing(db()) as conn:
            a,b = get_place(conn,left),get_place(conn,right)
            if not a or not b or left==right:
                return HTMLResponse("비교할 두 현재 장소를 확인해주세요.",404)
            match = compare(a,b)
            ignored = pair(left,right) in ignores(conn)
            relations = []
            for table,field,label in (("images","place_id","직접 업로드 사진"),("place_image_references","place_id","사진 참조"),
                ("best_collection_places","place_id","BEST 연결"),("place_pick_periods","place_id","PICK 기록"),
                ("place_correction_suggestions","place_id","수정 제안"),("restaurant_ranking_items","place_id","맛집 랭킹")):
                values = [conn.execute(f"SELECT count(*) FROM {table} WHERE {field}=?",(pid,)).fetchone()[0] for pid in (left,right)]
                relations.append({"label":label,"left":str(values[0]),"right":str(values[1]),"different":values[0]!=values[1]})
            distance = calculate_place_distance(a,b)
            a,b = display(conn,a),display(conn,b)
        return templates.TemplateResponse(request=request,name="admin/duplicate_compare.html",context={
            "left":a,"right":b,"fields":comparison_rows(a,b),"relations":relations,"match":match,
            "ignored":ignored,"distance":distance})

    @app.post("/admin/places/duplicates/compare/{left}/{right}")
    async def decision(request:Request,left:int,right:int):
        if response := guard(request): return response
        form = await request.form(max_files=0,max_fields=5)
        if not csrf(request,form): return HTMLResponse("CSRF 검증에 실패했습니다.",400)
        action = form.get("decision")
        if action not in ("separate","restore","keep_left","keep_right") or left==right:
            return HTMLResponse("판단 항목을 확인해주세요.",400)
        with closing(db()) as conn,conn:
            conn.execute("BEGIN IMMEDIATE")
            a,b = get_place(conn,left),get_place(conn,right)
            if not a or not b: return HTMLResponse("현재 장소를 찾을 수 없습니다.",404)
            lo,hi=pair(left,right)
            actor=identity(request)
            label={"separate":"별개 장소 확인","restore":"중복 제외 해제","keep_left":"유지할 장소 선택","keep_right":"유지할 장소 선택"}[action]
            audit(conn,request,label,"장소",right if action=="keep_right" else left,f"#{left} / #{right}")
            if action=="separate":
                conn.execute("INSERT OR IGNORE INTO place_duplicate_ignores(place_low,place_high,ignored_by,ignored_by_member) VALUES(?,?,?,?)",(lo,hi,actor.get("id"),actor.get("member_id")))
            elif action=="restore":
                conn.execute("DELETE FROM place_duplicate_ignores WHERE place_low=? AND place_high=?",(lo,hi))
        flash(request,"별개 장소로 기록했습니다. 이 조합은 다시 경고하지 않습니다." if action=="separate" else
              "중복 후보에서 다시 확인할 수 있습니다." if action=="restore" else "유지할 장소 선택을 기록했습니다. 두 장소의 정보·공개 여부는 변경하지 않았습니다. 필요한 정리는 기존 수정 화면에서 직접 진행해주세요.","success")
        return RedirectResponse(f"/admin/places/duplicates/compare/{left}/{right}",303)

    async def check(request, admin):
        if admin:
            if response := guard(request): return response
        elif not current_user(request):
            return JSONResponse({"error":"로그인이 필요합니다."},401)
        form=await request.form(max_files=0,max_fields=12)
        if not csrf(request,form): return JSONResponse({"error":"화면을 새로고침해주세요."},400)
        actor=identity(request) if admin else current_user(request)
        if rate_limited(db,secret,"duplicate-check-admin" if admin else "duplicate-check-member",[("actor",str(actor.get("id") or actor.get("member_id")))],limit=120,window=300):
            return JSONResponse({"error":"요청이 많습니다. 잠시 후 다시 시도해주세요."},429)
        data={k:str(form.get(k,"")) for k in ("name","address","phone","region","category_id","latitude","longitude")}
        if any(len(v)>limit for v,limit in zip(data.values(),(120,300,80,40,20,30,30))):
            return JSONResponse({"error":"입력 길이를 확인해주세요."},400)
        try: exclude=int(form.get("exclude_id") or 0) or None
        except ValueError: return JSONResponse({"error":"장소 정보를 확인해주세요."},400)
        if exclude is not None and not 0 < exclude < 2**63:
            return JSONResponse({"error":"장소 정보를 확인해주세요."},400)
        with closing(db()) as conn:
            if exclude and not admin:
                p=get_place(conn,exclude)
                if not p or p["submitted_by_user_id"]!=current_user(request)["id"]:
                    return JSONResponse({"error":"접근할 수 없습니다."},403)
            matches=find_duplicate_candidates(conn,data,exclude,include_private=admin)[:12]
        items=[{"name":p["name"],"address":p["address"],"category":p["category"],"label":p["label"],
                "reasons":p["reasons"],"url":f'/admin/places/{p["id"]}/edit'} for p in matches] if admin else [public_candidate(p) for p in matches]
        return JSONResponse({"candidates":items},headers={"Cache-Control":"no-store"})

    @app.post("/admin/places/duplicate-check")
    async def admin_check(request:Request): return await check(request,True)

    @app.post("/places/duplicate-check")
    async def member_check(request:Request): return await check(request,False)
