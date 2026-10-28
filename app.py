import hmac
import json
import math
import mimetypes
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, PlainTextResponse
from public_seo import page_metadata, place_metadata, issue_metadata, sitemap, private_page
from new_visibility import new_clause
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from place_migrations import backup_before_migration, migrate_place_categories
from place_detail_migrations import backup_before_place_detail_migration, migrate_place_details
from place_details import parse_details, validate_details, save_details, decorate_details, COFFEE_OPTIONS, WAITING_OPTIONS
from user_migrations import backup_before_user_migration, migrate_users
from home_migrations import backup_before_home_migration, migrate_home
from taxonomy_migrations import backup_before_taxonomy_migration, migrate_taxonomy, TYPES
from restaurant_migrations import backup_before_restaurants, migrate_restaurants
from restaurant_taxonomy import FIELDS as RESTAURANT_FIELDS, parse_selection, validate_parent, usage_count
from image_migrations import backup_before_image_migration, migrate_images
from image_uploads import ImageStore, ImagePlan, ImageError, FORM_BYTES, ClosedFormRoute
from place_taxonomy import taxonomy, navigation_order, linked_ids, validate_terms, save_links, public_search
from cafe_focus import parse_focus, validate_focus, save_focus, decorate_focus
from cafe_focus_migrations import backup_before_cafe_focus, migrate_cafe_focus
from cafe_flow_migrations import backup_before_cafe_flow, migrate_cafe_flow
from cafe_visit_times import OPTIONS as CAFE_VISIT_TIMES, parse_times, validate_times, save_times
from cafe_feature_migrations import backup_before_cafe_features, migrate_cafe_features
from auth_security import ServerSessionMiddleware, current_user, hash_password, verify_password, safe_external_url, rate_limited, DUMMY_HASH
from member_routes import register_member_routes, validate_news
from community_migrations import backup_before_community_migration, migrate_community, today as community_today
from community_places import decorate_facts, opening, save_admin_facts, VERIFICATION_LABELS
from community_routes import register_community_routes
from admin_ui import AdminUI, admin_context, admin_return
from admin_settings import read_settings, catalog
from admin_ui_migrations import backup_before_admin_ui, migrate_admin_ui
from basic_place_info_migrations import backup_before_basic_place_info, migrate_basic_place_info
from submission_migrations import backup_before_submission, migrate_submission
from admin_place_cleanup_migrations import backup_before_admin_place_cleanup, migrate_admin_place_cleanup
from submission_fields import normalize_controls, controls, TIME_OPTIONS, WEEKDAYS, CONTROL_FIELDS
from starlette.concurrency import run_in_threadpool

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "data"

REGIONS = [
    "제주시", "애월", "한림", "한경", "구좌", "조천", "성산", "표선",
    "남원", "서귀포시", "대정", "안덕", "기타",
]
COMPANIONS = ["혼자", "연인", "친구", "부모님", "아이", "반려동물", "단체"]
FEATURES = ["감성", "뷰", "비오는날", "사진", "힐링", "로컬", "전통", "이색", "가성비", "포장", "예약"]
STATUS_OPTIONS = ["미확인", "가능", "불가", "제한"]
NEWS_CATEGORY_LABELS = {
    "events": "행사•축제",
    "exhibitions": "전시•팝업",
    "activities": "체험•교육",
    "local": "지역소식",
    "opportunities": "공모•지원",
}
NEWS_CATEGORIES = list(NEWS_CATEGORY_LABELS.values())
# Read compatibility only: never rewrite existing rows on startup or during reads.
# Ambiguous legacy values such as 마켓 remain unchanged until an editor chooses.
NEWS_CATEGORY_ALIASES = {
    "행사·축제": NEWS_CATEGORY_LABELS["events"],
    "전시": NEWS_CATEGORY_LABELS["exhibitions"],
    "체험·교육": NEWS_CATEGORY_LABELS["activities"],
    "공모·지원": NEWS_CATEGORY_LABELS["opportunities"],
}

SEED_PLACES = [
    {
        "slug": "gudeum", "name": "구듬", "region": "한림", "category": "카페", "emoji": "☕",
        "is_pick": 1, "companions": ["연인", "부모님", "친구"], "features": ["감성", "사진"],
        "parking_status": "가능", "one_line": "옛 고구마 공장을 개조한 감성 공간.",
        "reason": "기존 노션에 정리된 내용을 바탕으로 구성한 예시 문구입니다. 실제 공개 전 제주노의 추천 이유를 최종 확정합니다.",
        "note": "세부 정보는 실제 공개 전 다시 확인하세요.", "is_example": 1,
    },
    {
        "slug": "coffee-finder", "name": "커피파인더", "region": "제주시", "category": "카페", "emoji": "🫘",
        "is_pick": 1, "companions": ["혼자", "친구", "연인"], "features": ["감성"],
        "one_line": "필터커피를 중심으로 보기 좋은 카페.",
        "reason": "기존 노션의 한줄평을 웹 카드 구조로 옮긴 예시입니다.",
        "note": "영업시간·가격 등 변동 정보는 출처와 확인일을 별도로 관리하는 것을 권장합니다.", "is_example": 1,
    },
    {
        "slug": "grain-cookie", "name": "그레인스쿠키 제주점", "region": "애월", "category": "카페", "emoji": "🍪",
        "is_pick": 1, "companions": ["부모님", "아이", "연인", "친구"], "features": ["사진"],
        "parking_status": "가능", "one_line": "쿠키를 중심으로 즐기기 좋은 대형 카페.",
        "reason": "가족·부모님 동행 등 기존 태그를 화면에서 이해하기 쉽게 재구성한 예시입니다.",
        "note": "아이 동반·주차 여부 등은 확인 상태로 관리합니다.", "is_example": 1,
    },
    {
        "slug": "nuri-friends", "name": "누리프렌즈카페 제주", "region": "애월", "category": "카페", "emoji": "🧸",
        "companions": ["혼자", "연인", "아이"], "features": ["사진"], "parking_status": "가능",
        "one_line": "피규어와 공간 구성이 특징인 카페.",
        "reason": "기존 노션의 태그와 한줄평을 카드용 문장으로 옮긴 예시입니다.",
        "note": "상세 정보는 실제 등록 단계에서 확인 후 보완합니다.", "is_example": 1,
    },
    {
        "slug": "kohidong", "name": "코히동 시오리", "region": "제주시", "category": "카페", "emoji": "🥐",
        "companions": ["혼자", "연인", "친구"], "features": ["감성"],
        "one_line": "일본 감성의 소금빵 카페.",
        "reason": "웹 상세 화면에서 제주노의 추천 이유가 별도 영역으로 강조되는 형태를 보여주는 예시입니다.",
        "note": "추천 메뉴·방문 시간대 등은 선택 항목으로 확장 가능합니다.", "is_example": 1,
    },
    {
        "slug": "tangerine-house", "name": "굴메달 하우스", "region": "제주시", "category": "카페", "emoji": "🍊",
        "companions": ["연인", "친구"], "features": ["감성", "사진"],
        "one_line": "감귤 감성이 살아 있는 포토존 카페.",
        "reason": "사진·감성 태그를 장소 특징으로 분리해 활용하는 화면 예시입니다.",
        "note": "실제 위치·지도 링크는 정확한 값으로 보완하세요.", "is_example": 1,
    },
    {
        "slug": "viral-espresso", "name": "바이러닉 에스프레소", "region": "제주시", "category": "카페", "emoji": "🌊",
        "companions": ["연인", "부모님", "친구"], "features": ["뷰"], "parking_status": "가능",
        "one_line": "바다 전망을 즐기기 좋은 카페.",
        "reason": "뷰 추천 태그를 사용자가 이해하기 쉬운 장소 특징으로 표시한 예시입니다.",
        "note": "주차 등 사실 정보는 미확인 상태와 구분해서 관리합니다.", "is_example": 1,
    },
    {
        "slug": "rain-example", "name": "비 오는 날 예시 장소", "region": "제주시", "category": "가볼 곳", "emoji": "🌧️",
        "companions": ["혼자", "친구", "부모님"], "features": ["비오는날"],
        "one_line": "비 오는 날 조건 테스트용 예시 데이터.",
        "reason": "이 항목은 기능 검증을 위한 예시 데이터이며 실제 제주노 추천이 아닙니다.",
        "note": "실서비스에서는 실제 추천과 예시 데이터를 명확히 구분합니다.", "is_example": 1,
    },
    {
        "slug": "food-example", "name": "맛집 예시 장소", "region": "애월", "category": "맛집", "emoji": "🍚",
        "companions": ["연인", "부모님", "친구"], "features": [], "parking_status": "가능",
        "one_line": "맛집 카테고리 필터 확인용 예시 데이터.",
        "reason": "아직 실제 음식점 데이터가 충분하지 않아 인터랙션 검증을 위해 넣은 예시입니다.",
        "note": "실제 개발 시 실제 등록 데이터로 교체하세요.", "is_example": 1,
    },
]


def create_app(test_config=None):
    test_config = test_config or {}
    data_dir = Path(os.environ.get("JEJUNO_DATA_DIR", DEFAULT_DATA_DIR))
    data_dir.mkdir(parents=True, exist_ok=True)
    database_path = Path(test_config.get("DATABASE") or os.environ.get("JEJUNO_DATABASE", data_dir / "jejuno.db"))
    secret_path = data_dir / "secret_key.txt"

    if test_config.get("SECRET_KEY"):
        secret_key = test_config["SECRET_KEY"]
    elif os.environ.get("SECRET_KEY"):
        secret_key = os.environ["SECRET_KEY"]
    elif secret_path.exists():
        secret_key = secret_path.read_text(encoding="utf-8").strip()
    else:
        secret_key = secrets.token_urlsafe(48)
        secret_path.write_text(secret_key, encoding="utf-8")

    app = FastAPI(title="JEJU_KNOW")
    app.router.route_class = ClosedFormRoute
    app.state.database = str(database_path)
    app.state.testing = bool(test_config.get("TESTING"))
    app.state.kakao_map_key = os.environ.get("KAKAO_MAP_JS_KEY", "").strip()
    app.add_middleware(
        ServerSessionMiddleware,
        database=str(database_path),
        secure=not app.state.testing,
    )
    # Windows registry mappings can label web assets as text/plain.
    mimetypes.add_type("font/woff2", ".woff2")
    mimetypes.add_type("image/webp", ".webp")
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    @app.middleware('http')
    async def indexing_policy(request, call_next):
        response = await call_next(request)
        if private_page(request.url.path) or response.status_code >= 400:
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    def context_processor(request: Request):
        if "csrf_token" not in request.session:
            request.session["csrf_token"] = secrets.token_urlsafe(32)
        flashes = request.session.pop("flashes", [])
        with closing(connect_db()) as conn:
            settings = read_settings(conn)
            admin_data = admin_context(request, conn) if request.url.path.startswith('/admin') else {}
            region_labels = {i['value']: i['label'] for i in catalog(conn, 'region', True)}
            companion_labels = {i['value']: i['label'] for i in catalog(conn, 'companion', True)}
            active_regions = [i['value'] for i in catalog(conn, 'region')]
            active_companions = [i['value'] for i in catalog(conn, 'companion')]
        return {
            "default_seo": page_metadata(request, settings['site_description']),
            "site_settings": settings, "region_labels": region_labels, "companion_labels": companion_labels, **admin_data,
            "active_regions": active_regions, "active_companions": active_companions,
            "csrf_token": request.session["csrf_token"], "flashes": flashes,
            "news_categories": NEWS_CATEGORIES, "news_category_labels": NEWS_CATEGORY_LABELS,
            "member": current_user(request),
            "verification_labels": VERIFICATION_LABELS,
            "current_year": community_today().year, "current_month": community_today().month,
        }

    templates = Jinja2Templates(directory=BASE_DIR / "templates", context_processors=[context_processor])

    def connect_db():
        conn = sqlite3.connect(app.state.database, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db():
        cafe_flow_backup = backup_before_cafe_flow(app.state.database)
        restaurant_backup = backup_before_restaurants(app.state.database)
        cleanup_backup_path = backup_before_admin_place_cleanup(app.state.database)
        submission_backup_path = backup_before_submission(app.state.database)
        basic_info_backup_path = backup_before_basic_place_info(app.state.database)
        admin_backup_path = backup_before_admin_ui(app.state.database)
        focus_backup_path = backup_before_cafe_focus(app.state.database)
        cafe_feature_backup = backup_before_cafe_features(app.state.database)
        detail_backup_path = backup_before_place_detail_migration(app.state.database)
        community_backup_path = backup_before_community_migration(app.state.database)
        image_backup_path = backup_before_image_migration(app.state.database)
        taxonomy_backup_path = backup_before_taxonomy_migration(app.state.database)
        home_backup_path = backup_before_home_migration(app.state.database)
        user_backup_path = backup_before_user_migration(app.state.database)
        backup_path = backup_before_migration(app.state.database)
        conn = connect_db()
        had_places_table = conn.execute("SELECT 1 FROM sqlite_master WHERE name='places'").fetchone()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS places (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                region TEXT NOT NULL,
                category TEXT NOT NULL,
                emoji TEXT NOT NULL DEFAULT '📍',
                image_url TEXT NOT NULL DEFAULT '',
                is_pick INTEGER NOT NULL DEFAULT 0,
                companions TEXT NOT NULL DEFAULT '[]',
                features TEXT NOT NULL DEFAULT '[]',
                parking_status TEXT NOT NULL DEFAULT '미확인',
                pet_status TEXT NOT NULL DEFAULT '미확인',
                child_status TEXT NOT NULL DEFAULT '미확인',
                accessible_status TEXT NOT NULL DEFAULT '미확인',
                one_line TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                map_url TEXT NOT NULL DEFAULT '',
                info_source TEXT NOT NULL DEFAULT '',
                checked_at TEXT NOT NULL DEFAULT '',
                is_example INTEGER NOT NULL DEFAULT 0,
                published INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS news_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                venue TEXT NOT NULL DEFAULT '',
                event_start TEXT NOT NULL DEFAULT '',
                event_end TEXT NOT NULL DEFAULT '',
                external_url TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '',
                checked_at TEXT NOT NULL DEFAULT '',
                is_featured INTEGER NOT NULL DEFAULT 0,
                is_sponsored INTEGER NOT NULL DEFAULT 0,
                sponsor_name TEXT NOT NULL DEFAULT '',
                published INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        if count == 0 and not had_places_table and os.environ.get("JEJUNO_SKIP_SEED") != "1":
            for item in SEED_PLACES:
                conn.execute(
                    """
                    INSERT INTO places (
                        slug, name, region, category, emoji, image_url, is_pick,
                        companions, features, parking_status, pet_status,
                        child_status, accessible_status, one_line, reason, note,
                        address, map_url, info_source, checked_at, is_example, published
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.get("slug"), item.get("name"), item.get("region"), item.get("category"),
                        item.get("emoji", "📍"), item.get("image_url", ""), item.get("is_pick", 0),
                        json.dumps(item.get("companions", []), ensure_ascii=False),
                        json.dumps(item.get("features", []), ensure_ascii=False),
                        item.get("parking_status", "미확인"), item.get("pet_status", "미확인"),
                        item.get("child_status", "미확인"), item.get("accessible_status", "미확인"),
                        item.get("one_line", ""), item.get("reason", ""), item.get("note", ""),
                        item.get("address", ""), item.get("map_url", ""), item.get("info_source", ""),
                        item.get("checked_at", ""), item.get("is_example", 0), item.get("published", 1),
                    ),
                )
        conn.commit()
        conn.close()
        migrate_place_categories(app.state.database, backup_path)
        migrate_users(app.state.database, user_backup_path)
        migrate_home(app.state.database, home_backup_path)
        migrate_taxonomy(app.state.database, taxonomy_backup_path)
        migrate_images(app.state.database, image_backup_path)
        migrate_community(app.state.database, community_backup_path)
        migrate_place_details(app.state.database, detail_backup_path)
        migrate_cafe_focus(app.state.database, focus_backup_path)
        migrate_admin_ui(app.state.database, admin_backup_path)
        migrate_basic_place_info(app.state.database, basic_info_backup_path)
        migrate_submission(app.state.database, submission_backup_path)
        migrate_admin_place_cleanup(app.state.database, cleanup_backup_path)
        migrate_restaurants(app.state.database, restaurant_backup)
        migrate_cafe_features(app.state.database, cafe_feature_backup)
        migrate_cafe_flow(app.state.database, cafe_flow_backup)
        from csv_import_migrations import migrate as migrate_csv_import
        migrate_csv_import(app.state.database)

    init_db()
    image_store = ImageStore(database_path.parent / "uploads", connect_db)
    app.state.image_store = image_store
    image_store.collect()

    @app.get("/uploads/{kind}/{filename}")
    def uploaded_image(request: Request, kind: str, filename: str):
        return image_store.response(request, kind, filename)

    @app.get("/health")
    def healthcheck():
        try:
            conn = connect_db()
            conn.execute("SELECT 1").fetchone()
            conn.close()
            return {"status": "ok"}
        except Exception:
            return JSONResponse({"status": "error"}, status_code=500)

    def flash(request: Request, message: str, category: str = "success"):
        flashes = request.session.get("flashes", [])
        flashes.append({"message": message, "category": category})
        request.session["flashes"] = flashes

    def require_csrf(request: Request, form):
        sent = str(form.get("csrf_token", ""))
        expected = str(request.session.get("csrf_token", ""))
        if not sent or len(sent) > 256 or not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
            return False
        return True

    def admin_exists():
        conn = connect_db()
        exists = conn.execute("SELECT EXISTS(SELECT 1 FROM admins)").fetchone()[0] == 1
        conn.close()
        return exists

    def parse_json_list(value):
        if isinstance(value, list):
            return value
        try:
            loaded = json.loads(value or "[]")
            return loaded if isinstance(loaded, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    def row_to_place(row):
        place = dict(row)
        place["has_coordinates"] = (
            place.get("latitude") is not None and place.get("longitude") is not None
            and math.isfinite(place["latitude"]) and math.isfinite(place["longitude"])
            and -90 <= place["latitude"] <= 90 and -180 <= place["longitude"] <= 180
        )
        place["companions"] = parse_json_list(place.get("companions"))
        place["features"] = parse_json_list(place.get("features"))
        place["is_pick"] = bool(place.get("is_pick"))
        place["is_example"] = bool(place.get("is_example"))
        place["published"] = bool(place.get("published"))
        public_features = list(place["features"])
        if place.get("parking_status") == "가능" and "주차" not in public_features:
            public_features.append("주차")
        if place.get("accessible_status") == "가능" and "무장애" not in public_features:
            public_features.append("무장애")
        place["features_public"] = public_features
        conn = connect_db()
        try:
            decorate_facts(conn, place)
            decorate_details(conn, place)
            decorate_focus(place, conn)
            place['region_label'] = {i['value']:i['label'] for i in catalog(conn,'region',True)}.get(place['region'],place['region'])
            clause, values = new_clause()
            place['is_new'] = bool(conn.execute('SELECT 1 FROM places p JOIN categories c ON c.id=p.category_id WHERE p.id=? AND '+clause, [place['id'], *values]).fetchone())
        finally:
            conn.close()
        return image_store.decorate(place, "places")

    def row_to_news(row):
        item = dict(row)
        item["author_label"] = "사용자 등록" if item.get("author_type") == "user" else "JEJU_KNOW 등록"
        item["author_name"] = "JEJU_KNOW"
        if item.get("author_type") == "user":
            conn = connect_db()
            author = conn.execute("SELECT nickname,status FROM users WHERE id=?", (item.get("author_id"),)).fetchone()
            conn.close()
            item["author_name"] = author["nickname"] if author and author["status"] != "deleted" else "탈퇴한 사용자"
        for key in ("image_url", "external_url"):
            if not safe_external_url(item.get(key, "")):
                item[key] = ""
        item["stored_category"] = item["category"]
        item["category"] = NEWS_CATEGORY_ALIASES.get(item["category"], item["category"])
        item["is_featured"] = bool(item.get("is_featured"))
        item["is_promoted"] = bool(item.get("is_promoted"))
        item["is_sponsored"] = bool(item.get("is_sponsored"))
        item["published"] = bool(item.get("published"))
        start = item.get("event_start") or ""
        end = item.get("event_end") or ""
        if start and end and start != end:
            item["date_label"] = f"{start} ~ {end}"
        elif start:
            item["date_label"] = start
        else:
            item["date_label"] = "상시/일정 미정"
        if end and end < date.today().isoformat():
            item["event_status"] = "종료"
        elif start and start > date.today().isoformat():
            item["event_status"] = "예정"
        else:
            item["event_status"] = "진행/접수중"
        return image_store.decorate(item, "issues")

    def unique_slug(candidate, exclude_id=None):
        candidate = (candidate or "").strip().lower()
        safe = "".join(ch for ch in candidate if ch.isalnum() or ch in "-_")
        if not safe:
            safe = f"place-{uuid.uuid4().hex[:8]}"
        conn = connect_db()
        base = safe
        index = 2
        while True:
            if exclude_id:
                exists = conn.execute("SELECT 1 FROM places WHERE slug=? AND id<>?", (safe, exclude_id)).fetchone()
            else:
                exists = conn.execute("SELECT 1 FROM places WHERE slug=?", (safe,)).fetchone()
            if not exists:
                conn.close()
                return safe
            safe = f"{base}-{index}"
            index += 1

    def unique_news_slug(candidate, exclude_id=None):
        candidate = (candidate or "").strip().lower()
        safe = "".join(ch for ch in candidate if ch.isalnum() or ch in "-_")
        if not safe:
            safe = f"news-{uuid.uuid4().hex[:8]}"
        conn = connect_db()
        base = safe
        index = 2
        while True:
            if exclude_id:
                exists = conn.execute("SELECT 1 FROM news_posts WHERE slug=? AND id<>?", (safe, exclude_id)).fetchone()
            else:
                exists = conn.execute("SELECT 1 FROM news_posts WHERE slug=?", (safe,)).fetchone()
            if not exists:
                conn.close()
                return safe
            safe = f"{base}-{index}"
            index += 1

    async def parse_place_form(request: Request, existing=None):
        form = await request.form(max_files=5, max_fields=100, max_part_size=FORM_BYTES)
        control_error = None
        try:
            form = normalize_controls(form)
        except ValueError as exc:
            control_error = str(exc)
        slug = unique_slug(form.get("slug"), existing["id"] if existing else None)
        return form, {
            **{key: str(form.get(key, '')) for key in RESTAURANT_FIELDS},
            **parse_details(form, existing),
            "cafe_visit_times": parse_times(form, existing),
            "_control_error": control_error,
            "_focus_submitted": "cafe_focus_present" in form or "cafe_focus" in form,
            **{key: form.getlist(key) if key == "closed_weekdays" else form.get(key, "") for key in CONTROL_FIELDS if key in form or (key in ("hours_same", "hours_unknown") and form.get("hours_present") == "1")},
            "slug": slug,
            "name": str(form.get("name", "")).strip(),
            "region": str(form.get("region", "")).strip(),
            "category": str(form.get("category", "")).strip(),
            "category_id": str(form.get("category_id", "")).strip(),
            "latitude": str(form.get("latitude", existing["latitude"] if existing and existing["latitude"] is not None else "")).strip(),
            "longitude": str(form.get("longitude", existing["longitude"] if existing and existing["longitude"] is not None else "")).strip(),
            "emoji": str(form.get("emoji", "📍")).strip() or "📍",
            "image_url": str(form.get("image_url", existing["image_url"] if existing else "")).strip(),
            "is_pick": 1 if form.get("is_pick") == "1" else 0,
            "companions": form.getlist("companions"),
            # Disabled/omitted legacy controls must not clear existing JSON tags.
            # The presence marker still allows an explicit clear in the visible editor.
            "features": form.getlist("features") if "features" in form or form.get("legacy_features_present") == "1" else parse_json_list(existing["features"] if existing else None),
            "term_ids": form.getlist("term_ids") if form.get("taxonomy_present") == "1" else None,
            "cafe_focus": parse_focus(form, existing),
            "parking_status": str(form.get("parking_status", "미확인")),
            "pet_status": str(form.get("pet_status", "미확인")),
            "child_status": str(form.get("child_status", "미확인")),
            "accessible_status": str(form.get("accessible_status", "미확인")),
            "one_line": str(form.get("one_line", "")).strip(),
            # PICK still reads the historical reason; the editor never writes it.
            "reason": existing["reason"] if existing else "",
            "note": str(form.get("note", "")).strip(),
            "address": str(form.get("address", "")).strip(),
            "map_url": str(form.get("map_url", "")).strip(),
            "info_source": str(form.get("info_source", "")).strip(),
            "checked_at": str(form.get("checked_at", "")).strip(),
            "is_example": 1 if form.get("is_example") == "1" else 0,
            "published": 1 if form.get("published") == "1" else 0,
            "opened_year": form.get("opened_year", existing["opened_year"] if existing else None),
            "opened_month": form.get("opened_month", existing["opened_month"] if existing else None),
            "website_url": str(form.get("website_url", existing["website_url"] if existing else "")).strip(),
            "new_override": str(form.get("new_override", existing["new_override"] if existing else "auto")),
            "verification_status": str(form.get("verification_status", existing["verification_status"] if existing else "unverified")),
        }

    async def parse_news_form(request: Request, existing=None):
        form = await request.form(max_files=1, max_fields=40, max_part_size=FORM_BYTES)
        slug = unique_news_slug(form.get("slug"), existing["id"] if existing else None)
        return form, {
            "slug": slug,
            "title": str(form.get("title", "")).strip(),
            "category": str(form.get("category", "")).strip(),
            "summary": str(form.get("summary", "")).strip(),
            "body": str(form.get("body", "")).strip(),
            "image_url": str(form.get("image_url", existing["image_url"] if existing else "")).strip(),
            "venue": str(form.get("venue", "")).strip(),
            "event_start": str(form.get("event_start", "")).strip(),
            "event_end": str(form.get("event_end", "")).strip(),
            "external_url": str(form.get("external_url", "")).strip(),
            "source_name": str(form.get("source_name", "")).strip(),
            "checked_at": str(form.get("checked_at", "")).strip(),
            "is_featured": 1 if form.get("is_featured") == "1" else 0,
            "is_promoted": 1 if form.get("is_promoted") == "1" else 0,
            "is_sponsored": 1 if form.get("is_sponsored") == "1" else 0,
            "sponsor_name": str(form.get("sponsor_name", "")).strip(),
            "published": 1 if form.get("published") == "1" else 0,
        }

    def place_categories(current_id=None, active_only=True):
        conn = connect_db()
        rows = conn.execute(
            "SELECT * FROM categories WHERE type='place'" + (" AND (is_active=1 OR id=?)" if active_only else "") + " ORDER BY sort_order,id",
            (current_id,) if active_only else (),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def validate_place(data, existing=None):
        if data.get("_control_error"):
            return data["_control_error"]
        try:
            validate_details(data)
            data["cafe_visit_times"] = validate_times(data["cafe_visit_times"])
            data["opened_year"], data["opened_month"] = opening(data["opened_year"], data["opened_month"])
        except ValueError as exc:
            return str(exc)
        if data["verification_status"] not in VERIFICATION_LABELS or data["new_override"] not in ("auto","show","hide"):
            return "확인 상태와 NEW 노출 설정을 확인해주세요."
        if not safe_external_url(data["website_url"]) or len(data["website_url"]) > 2000:
            return "홈페이지는 올바른 http 또는 https URL로 입력해주세요."
        if not data["name"] or not data["region"]:
            return "장소명과 지역을 확인해 주세요."
        conn = connect_db()
        if data["category_id"]:
            category = conn.execute("SELECT * FROM categories WHERE id=?", (data["category_id"],)).fetchone()
        else:  # Preserve the existing category-name POST contract.
            category = conn.execute("SELECT * FROM categories WHERE name=? AND type='place'", (data["category"],)).fetchone()
        conn.close()
        if not category or category["type"] != "place" or (not category["is_active"] and (not existing or existing["category_id"] != category["id"])):
            return "활성 카테고리를 선택해 주세요. 기존 장소의 비활성 분류는 유지할 수 있습니다."
        data["category_id"], data["category"] = category["id"], category["name"]
        if category["slug"] in ("food", "cafe") and existing:
            # Cafe and food hide legacy tags. Preserve saved values even when
            # a stale form submits changes to the retired editor.
            data["features"] = parse_json_list(existing["features"])
        data["emoji"] = {"food": "🍚", "cafe": "☕", "attraction": "📍"}.get(category["slug"], "📍")
        conn = connect_db()
        try:
            restaurant_ids = parse_selection(conn, data, data['category_id'], existing)
            if restaurant_ids is not None:
                data['term_ids'] = restaurant_ids
            error = validate_terms(conn, data, existing)
            if data.get("_focus_submitted"):
                validate_focus(conn, data, existing)
            region_values = {i['value'] for i in catalog(conn, 'region')}
            companion_values = {i['value'] for i in catalog(conn, 'companion')}
            if existing:
                region_values.add(existing['region'])
                companion_values.update(parse_json_list(existing['companions']))
            if data['region'] not in region_values or not set(data['companions']).issubset(companion_values):
                return "활성 지역과 동행을 선택해주세요. 기존 장소의 비활성 분류는 유지할 수 있습니다."
        except ValueError as exc:
            return str(exc)
        finally:
            conn.close()
        if error:
            return error
        lat, lng = data["latitude"], data["longitude"]
        if not lat and not lng:
            data["latitude"] = data["longitude"] = None
        else:
            try:
                lat, lng = float(lat), float(lng)
                if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
                    raise ValueError
            except (TypeError, ValueError):
                return "위도(-90~90)와 경도(-180~180)를 모두 입력하거나 두 칸을 비워 주세요."
            data["latitude"], data["longitude"] = lat, lng
        return None

    def get_taxonomy(include_inactive=False):
        conn = connect_db()
        try:
            return taxonomy(conn, REGIONS, COMPANIONS, include_inactive)
        finally:
            conn.close()

    def form_options(current_id=None):
        options = get_taxonomy(True)
        return {
            "taxonomy": options,
            "regions": options["regions"],
            "categories": place_categories(current_id),
            "kakao_map_key": app.state.kakao_map_key,
            "companions": options["companions"],
            "features": FEATURES,
            "status_options": STATUS_OPTIONS,
            "coffee_options": COFFEE_OPTIONS,
            "cafe_visit_time_options": CAFE_VISIT_TIMES,
            "waiting_options": WAITING_OPTIONS,
            "submission_controls": controls,
            "admin_time_options": TIME_OPTIONS,
            "admin_weekdays": WEEKDAYS,
        }

    def admin_guard(request: Request):
        member = current_user(request)
        if not request.session.get("admin_id") and not (member and member["role"] == "admin"):
            return RedirectResponse(url=f"/admin/login?next={request.url.path}", status_code=303)
        return None

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        conn = connect_db()
        news_rows = conn.execute(
            "SELECT * FROM news_posts WHERE published=1 AND deleted_at IS NULL ORDER BY is_promoted DESC, datetime(created_at) DESC, id DESC LIMIT 5"
        ).fetchall()
        collections = [dict(r) for r in conn.execute("SELECT * FROM best_collections WHERE published=1 ORDER BY sort_order,id")]
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"news_items": [row_to_news(row) for row in news_rows], "collections": collections},
        )

    @app.get("/place/{slug}", response_class=HTMLResponse)
    async def place_detail(request: Request, slug: str):
        conn = connect_db()
        row = conn.execute("SELECT * FROM places WHERE slug=? AND deleted_at IS NULL", (slug,)).fetchone()
        if row and row["merged_into_id"]:
            target = conn.execute("SELECT slug FROM places WHERE id=? AND published=1 AND deleted_at IS NULL", (row["merged_into_id"],)).fetchone()
            conn.close()
            return RedirectResponse("/place/" + target["slug"], status_code=301) if target else HTMLResponse("장소를 찾을 수 없습니다.",status_code=404)
        conn.close()
        if not row or not row["published"]:
            return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
        place = row_to_place(row)
        from urllib.parse import quote
        place['internal_map_url'] = '/map?place=' + quote(place['slug'], safe='')
        place['directions_url'] = (f"https://map.kakao.com/link/to/{quote(place['name'], safe='')},{place['latitude']},{place['longitude']}"
                                   if place['has_coordinates'] else place.get('map_url') or '')
        return templates.TemplateResponse(request=request, name="place_detail.html", context={"place": place, "seo": place_metadata(place)})

    @app.get('/sitemap.xml', include_in_schema=False)
    async def public_sitemap():
        with closing(connect_db()) as conn:
            return Response(sitemap(conn), media_type='application/xml')

    @app.get('/robots.txt', include_in_schema=False)
    async def robots():
        return PlainTextResponse('User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /account\nDisallow: /api/\nSitemap: https://www.jeju-know.com/sitemap.xml\n')

    @app.get("/api/places")
    async def api_places(category: str = "", q: str = "", region: str = "", companion: str = "", detail: str = "", condition: str = "", pick: str = "", new: str = "", focus: str = "", cuisine: str = ""):
        conn = connect_db()
        try:
            rows = public_search(conn, category, q, region, companion, detail, condition, pick, new, focus, cuisine)
            places = [row_to_place(row) for row in rows]
            if pick == "current":
                places.sort(key=lambda p: (p["pick_order"],p["id"]))
            # Private submitter IDs and internal moderator identities are never public API fields.
            for place in places:
                for key in ("submitted_by_user_id","verified_by","deleted_at","merged_into_id","admin_import_note"):
                    place.pop(key, None)
            return JSONResponse(places, headers={"Cache-Control": "no-store"})
        finally:
            conn.close()

    @app.get("/api/place-taxonomy")
    async def api_place_taxonomy():
        return JSONResponse(get_taxonomy(), headers={"Cache-Control": "no-store"})

    @app.get('/api/place-search/suggestions')
    async def search_relaxations(request: Request):
        from search_suggestions import suggestions
        with closing(connect_db()) as conn:
            return JSONResponse(suggestions(conn, request.query_params, get_taxonomy()), headers={'Cache-Control':'no-store'})

    @app.get("/places", response_class=HTMLResponse)
    async def place_list(request: Request):
        return templates.TemplateResponse(request=request, name="place_list.html", context={})

    @app.get("/api/categories")
    async def api_categories():
        return JSONResponse(place_categories(), headers={"Cache-Control": "no-store"})

    @app.get("/map", response_class=HTMLResponse)
    async def map_page(request: Request):
        if request.query_params.get("register") == "1":
            return RedirectResponse("/map/register?" + str(request.query_params), status_code=303)
        return templates.TemplateResponse(request=request, name="map.html", context={
            "categories": navigation_order(place_categories()), "taxonomy": get_taxonomy(), "kakao_map_key": app.state.kakao_map_key,
        })

    @app.get("/news", response_class=HTMLResponse)
    async def news_list(request: Request):
        category = (request.query_params.get("category") or "").strip()
        category = NEWS_CATEGORY_ALIASES.get(category, category)
        q = (request.query_params.get("q") or "").strip()
        clauses = ["published=1", "deleted_at IS NULL"]
        params = []
        if category:
            category_values = [category] + [old for old, new in NEWS_CATEGORY_ALIASES.items() if new == category]
            clauses.append(f"category IN ({','.join('?' for _ in category_values)})")
            params.extend(category_values)
        if q:
            clauses.append("(title LIKE ? OR summary LIKE ? OR venue LIKE ?)")
            token = f"%{q}%"
            params.extend([token, token, token])
        conn = connect_db()
        rows = conn.execute(
            f"SELECT * FROM news_posts WHERE {' AND '.join(clauses)} ORDER BY is_featured DESC, event_start DESC, updated_at DESC",
            params,
        ).fetchall()
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="news_list.html",
            context={
                "news_items": [row_to_news(row) for row in rows],
                "active_category": category,
                "q": q,
            },
        )

    @app.get("/news/{slug}", response_class=HTMLResponse)
    async def news_detail(request: Request, slug: str):
        conn = connect_db()
        row = conn.execute("SELECT * FROM news_posts WHERE slug=? AND published=1 AND deleted_at IS NULL", (slug,)).fetchone()
        conn.close()
        if not row:
            return HTMLResponse("소식을 찾을 수 없습니다.", status_code=404)
        item = row_to_news(row)
        return templates.TemplateResponse(request=request, name="news_detail.html", context={"item": item, "seo": issue_metadata(item)})

    @app.get("/partnership", response_class=HTMLResponse)
    async def partnership(request: Request):
        return templates.TemplateResponse(request=request, name="partnership.html", context={})

    @app.get("/admin/setup", response_class=HTMLResponse)
    async def admin_setup_get(request: Request):
        if admin_exists():
            return RedirectResponse(url="/admin/login", status_code=303)
        return templates.TemplateResponse(request=request, name="admin_setup.html", context={})

    @app.post("/admin/setup")
    async def admin_setup_post(request: Request):
        if admin_exists():
            return RedirectResponse(url="/admin/login", status_code=303)
        form = await request.form()
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        password_confirm = str(form.get("password_confirm", ""))
        if len(username) < 3:
            flash(request, "아이디는 3자 이상 입력해주세요.", "error")
        elif not 8 <= len(password) <= 128:
            flash(request, "비밀번호는 8자 이상 입력해주세요.", "error")
        elif password != password_confirm:
            flash(request, "비밀번호 확인이 일치하지 않습니다.", "error")
        else:
            conn = connect_db()
            password_hash = await run_in_threadpool(hash_password, password)
            conn.execute("INSERT INTO admins(username, password_hash) VALUES (?, ?)", (username, password_hash))
            conn.commit()
            conn.close()
            flash(request, "관리자 계정을 만들었습니다. 로그인해주세요.", "success")
            return RedirectResponse(url="/admin/login", status_code=303)
        return RedirectResponse(url="/admin/setup", status_code=303)

    @app.get("/admin/login", response_class=HTMLResponse)
    async def admin_login_get(request: Request):
        if not admin_exists():
            return RedirectResponse(url="/admin/setup", status_code=303)
        if request.session.get("admin_id"):
            return RedirectResponse(url="/admin", status_code=303)
        return templates.TemplateResponse(request=request, name="admin_login.html", context={})

    @app.post("/admin/login")
    async def admin_login_post(request: Request):
        if not admin_exists():
            return RedirectResponse(url="/admin/setup", status_code=303)
        form = await request.form()
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        if rate_limited(connect_db, secret_key, "admin-login", [("ip", request.client.host if request.client else "unknown"), ("username", username[:200])]):
            return HTMLResponse("요청이 너무 많습니다. 잠시 후 다시 시도해주세요.", status_code=429, headers={"Retry-After": "900"})
        conn = connect_db()
        admin = conn.execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()
        conn.close()
        valid = await run_in_threadpool(verify_password, password, admin["password_hash"] if admin else DUMMY_HASH)
        if admin and valid:
            if not admin["password_hash"].startswith("$argon2id$"):
                replacement = await run_in_threadpool(hash_password, password)
                conn = connect_db()
                conn.execute("UPDATE admins SET password_hash=? WHERE id=?", (replacement, admin["id"]))
                conn.commit()
                conn.close()
            request.session.clear()
            request.session["admin_id"] = admin["id"]
            request.session["csrf_token"] = secrets.token_urlsafe(32)
            request.scope["rotate_session"] = True
            next_url = request.query_params.get("next") or "/admin"
            if not re.fullmatch(r"/admin(?:/[a-zA-Z0-9_/-]*)?", next_url):
                next_url = "/admin"
            return RedirectResponse(url=next_url, status_code=303)
        flash(request, "아이디 또는 비밀번호가 올바르지 않습니다.", "error")
        return RedirectResponse(url="/admin/login", status_code=303)

    @app.post("/admin/logout")
    async def admin_logout(request: Request):
        guard = admin_guard(request)
        if guard:
            return guard
        form = await request.form()
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        request.session.clear()
        return RedirectResponse(url="/", status_code=303)

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_dashboard(request: Request):
        return app.state.admin_ui.dashboard(request)

    @app.get("/admin/news/new", response_class=HTMLResponse)
    async def admin_news_new_get(request: Request):
        guard = admin_guard(request)
        if guard:
            return guard
        return templates.TemplateResponse(
            request=request,
            name="admin_news_form.html",
            context={"item": None, "mode": "new", **form_options()},
        )

    @app.post("/admin/news/new")
    async def admin_news_new_post(request: Request):
        guard = admin_guard(request)
        if guard:
            return guard
        form, data = await parse_news_form(request)
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        if error := validate_news(data, NEWS_CATEGORIES):
            flash(request, error, "error")
            return RedirectResponse(url="/admin/news/new", status_code=303)
        try:
            plan = await image_store.prepare(form, "issues")
        except ImageError as exc:
            flash(request, str(exc), "error")
            return templates.TemplateResponse(request=request, name="admin_news_form.html", context={"item": {**data, "id": None, "legacy_image_url": data["image_url"], "images": []}, "mode": "new", **form_options()}, status_code=400)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with plan.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO news_posts (
                    slug, title, category, summary, body, image_url, venue,
                    event_start, event_end, external_url, source_name, checked_at,
                    is_featured, is_sponsored, sponsor_name, published, updated_at, is_promoted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["slug"], data["title"], data["category"], data["summary"], data["body"],
                    data["image_url"], data["venue"], data["event_start"], data["event_end"],
                    data["external_url"], data["source_name"], data["checked_at"], data["is_featured"],
                    data["is_sponsored"], data["sponsor_name"], data["published"], now, data["is_promoted"],
                ),
            )
            plan.apply(conn, cursor.lastrowid)
        flash(request, "제주 소식을 저장했습니다.", "success")
        return RedirectResponse(url=admin_return(form, "/admin/issues"), status_code=303)

    @app.get("/admin/news/{news_id}/edit", response_class=HTMLResponse)
    async def admin_news_edit_get(request: Request, news_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        conn = connect_db()
        row = conn.execute("SELECT * FROM news_posts WHERE id=? AND deleted_at IS NULL", (news_id,)).fetchone()
        conn.close()
        if not row:
            return HTMLResponse("소식을 찾을 수 없습니다.", status_code=404)
        return templates.TemplateResponse(
            request=request,
            name="admin_news_form.html",
            context={"item": row_to_news(row), "mode": "edit", **form_options()},
        )

    @app.post("/admin/news/{news_id}/edit")
    async def admin_news_edit_post(request: Request, news_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        conn = connect_db()
        existing = conn.execute("SELECT * FROM news_posts WHERE id=? AND deleted_at IS NULL", (news_id,)).fetchone()
        conn.close()
        if not existing:
            return HTMLResponse("소식을 찾을 수 없습니다.", status_code=404)
        form, data = await parse_news_form(request, existing)
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        if error := validate_news(data, NEWS_CATEGORIES):
            flash(request, error, "error")
            return RedirectResponse(url=f"/admin/news/{news_id}/edit", status_code=303)
        try:
            plan = await image_store.prepare(form, "issues")
        except ImageError as exc:
            flash(request, str(exc), "error")
            return templates.TemplateResponse(request=request, name="admin_news_form.html", context={"item": {**data, "id": news_id, "legacy_image_url": data["image_url"], "images": image_store.list("issues", news_id)}, "mode": "edit", **form_options()}, status_code=400)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with plan.transaction() as conn:
            conn.execute(
                """
                UPDATE news_posts SET
                    slug=?, title=?, category=?, summary=?, body=?, image_url=?, venue=?,
                    event_start=?, event_end=?, external_url=?, source_name=?, checked_at=?,
                    is_featured=?, is_sponsored=?, sponsor_name=?, published=?, updated_at=?, is_promoted=?
                WHERE id=?
                """,
                (
                    data["slug"], data["title"], data["category"], data["summary"], data["body"],
                    data["image_url"], data["venue"], data["event_start"], data["event_end"],
                    data["external_url"], data["source_name"], data["checked_at"], data["is_featured"],
                    data["is_sponsored"], data["sponsor_name"], data["published"], now, data["is_promoted"], news_id,
                ),
            )
            plan.apply(conn, news_id)
        flash(request, "제주 소식을 수정했습니다.", "success")
        return RedirectResponse(url=admin_return(form, "/admin/issues"), status_code=303)

    @app.post("/admin/news/{news_id}/toggle-published")
    async def admin_news_toggle_published(request: Request, news_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        form = await request.form()
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        conn = connect_db()
        row = conn.execute("SELECT published FROM news_posts WHERE id=? AND deleted_at IS NULL", (news_id,)).fetchone()
        if not row:
            conn.close()
            return HTMLResponse("소식을 찾을 수 없습니다.", status_code=404)
        new_value = 0 if row["published"] else 1
        conn.execute(
            "UPDATE news_posts SET published=?, updated_at=? WHERE id=?",
            (new_value, datetime.now(timezone.utc).isoformat(timespec="seconds"), news_id),
        )
        conn.commit()
        conn.close()
        flash(request, f"소식을 {'공개' if new_value else '비공개'}로 변경했습니다.", "success")
        return RedirectResponse(url=admin_return(form, "/admin/issues"), status_code=303)

    @app.post("/admin/news/{news_id}/delete")
    async def admin_news_delete(request: Request, news_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        form = await request.form()
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        conn = connect_db()
        row = conn.execute("SELECT title FROM news_posts WHERE id=?", (news_id,)).fetchone()
        if not row:
            conn.close()
            return HTMLResponse("소식을 찾을 수 없습니다.", status_code=404)
        conn.close()
        plan = ImagePlan(image_store, "issues")
        with plan.transaction() as conn:
            plan.delete_owner(conn, news_id)
            conn.execute("DELETE FROM news_posts WHERE id=?", (news_id,))
        flash(request, f"‘{row['title']}’ 소식을 삭제했습니다.", "success")
        return RedirectResponse(url=admin_return(form, "/admin/issues"), status_code=303)

    @app.get("/admin/categories", response_class=HTMLResponse)
    async def admin_categories(request: Request):
        return app.state.admin_ui.categories(request)

    async def save_category(request, category_id=None):
        guard = admin_guard(request)
        if guard:
            return guard
        form = await request.form()
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        name = str(form.get("name", "")).strip()
        slug = str(form.get("slug", "")).strip().lower()
        try:
            order = int(str(form.get("sort_order", "0")))
            if not 0 <= order <= 9999:
                raise ValueError
        except ValueError:
            order = -1
        if not name or len(name) > 40 or name == "전체" or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or len(slug) > 80 or order < 0:
            flash(request, "이름(1~40자, ‘전체’ 제외), 영문 슬러그(소문자·숫자·하이픈), 순서(0~9999)를 확인해 주세요.", "error")
            return RedirectResponse(admin_return(form, "/admin/categories"), status_code=303)
        kind = str(form.get("type", "place"))
        conn = connect_db()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                original = conn.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone() if category_id else None
                if category_id and not original:
                    return HTMLResponse("카테고리를 찾을 수 없습니다.", status_code=404)
                if original:
                    kind = original['type']
                if kind not in TYPES:
                    return HTMLResponse("분류 유형을 확인해 주세요.", status_code=400)
                parent_id = validate_parent(conn, kind, form.get('parent_id'), original)
                policy = str(form.get('member_policy', original['member_policy'] if original else 'review'))
                if kind == 'cafe_feature' and policy not in ('review', 'admin'):
                    raise ValueError('회원 선택 권한은 제안 가능 또는 관리자 전용으로 선택해주세요.')
                if category_id is None:
                    conn.execute("INSERT INTO categories(name,slug,sort_order,is_active,type,parent_id,member_policy) VALUES (?,?,?,?,?,?,?)", (name, slug, order, int(form.get("is_active") == "1"), kind, parent_id, policy))
                else:
                    # The stable slug survives renaming so shared map URLs continue to work.
                    conn.execute("UPDATE categories SET name=?,sort_order=?,is_active=?,parent_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (name, order, int(form.get("is_active") == "1"), parent_id, category_id))
                    if kind == 'cafe_feature':
                        conn.execute('UPDATE categories SET member_policy=? WHERE id=?', (policy, category_id))
                    if original['is_legacy'] and form.get('adopt_legacy') == '1':
                        conn.execute('UPDATE categories SET is_legacy=0 WHERE id=?', (category_id,))
                    if original["type"] != "place" and original["name"] != name:
                        aliases = list(dict.fromkeys([*parse_json_list(original["legacy_aliases"]), original["name"]]))
                        conn.execute("UPDATE categories SET legacy_aliases=? WHERE id=?", (json.dumps(aliases, ensure_ascii=False), category_id))
                    conn.execute("UPDATE places SET category=? WHERE category_id=?", (name, category_id))
            flash(request, "카테고리를 저장했습니다. 방문자 화면에 바로 반영됩니다.")
        except sqlite3.IntegrityError:
            flash(request, "같은 상위 분류에서 사용 중인 이름 또는 중복 슬러그입니다.", "error")
        except ValueError as exc:
            flash(request, str(exc), "error")
        finally:
            conn.close()
        return RedirectResponse(admin_return(form, "/admin/categories"), status_code=303)

    @app.post("/admin/categories/new")
    async def admin_category_new(request: Request):
        return await save_category(request)

    @app.post("/admin/categories/{category_id}/edit")
    async def admin_category_edit(request: Request, category_id: int):
        return await save_category(request, category_id)

    @app.post("/admin/categories/{category_id}/delete")
    async def admin_category_delete(request: Request, category_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        form = await request.form()
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        conn = connect_db()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                count = usage_count(conn, category_id)
                children = conn.execute("SELECT COUNT(*) FROM categories WHERE parent_id=?", (category_id,)).fetchone()[0]
                if count:
                    flash(request, f"현재 {count}개 장소에서 사용 중인 카테고리입니다. 삭제 대신 비활성화할 수 있습니다.", "error")
                elif children:
                    flash(request, "하위 분류가 있는 카테고리입니다. 삭제 대신 비활성화할 수 있습니다.", "error")
                else:
                    conn.execute("DELETE FROM categories WHERE id=?", (category_id,))
                    flash(request, "사용하지 않는 카테고리를 삭제했습니다.")
        finally:
            conn.close()
        return RedirectResponse(admin_return(form, "/admin/categories"), status_code=303)

    @app.get("/admin/places/new", response_class=HTMLResponse)
    async def admin_place_new_get(request: Request):
        guard = admin_guard(request)
        if guard:
            return guard
        return templates.TemplateResponse(request=request, name="admin_place_form.html", context={"place": None, "mode": "new", **form_options()})

    @app.post("/admin/places/new")
    async def admin_place_new_post(request: Request):
        guard = admin_guard(request)
        if guard:
            return guard
        form, data = await parse_place_form(request)
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        error = validate_place(data)
        if error:
            flash(request, error, "error")
            return templates.TemplateResponse(request=request, name="admin_place_form.html", context={"place": data, "mode": "new", **form_options()}, status_code=400)
        try:
            plan = await image_store.prepare(form, "places")
        except ImageError as exc:
            flash(request, str(exc), "error")
            return templates.TemplateResponse(request=request, name="admin_place_form.html", context={"place": {**data, "id": None, "legacy_image_url": data["image_url"], "images": []}, "mode": "new", **form_options()}, status_code=400)
        with plan.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO places (
                    slug, name, region, category, emoji, image_url, is_pick,
                    companions, features, parking_status, pet_status, child_status,
                    accessible_status, one_line, note, address, map_url,
                    info_source, checked_at, is_example, published, updated_at, category_id, latitude, longitude
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["slug"], data["name"], data["region"], data["category"], data["emoji"], data["image_url"],
                    data["is_pick"], json.dumps(data["companions"], ensure_ascii=False),
                    json.dumps(data["features"], ensure_ascii=False), data["parking_status"], data["pet_status"],
                    data["child_status"], data["accessible_status"], data["one_line"], data["note"],
                    data["address"], data["map_url"], data["info_source"], data["checked_at"], data["is_example"],
                    data["published"], datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    data["category_id"], data["latitude"], data["longitude"],
                ),
            )
            save_links(conn, cursor.lastrowid, data["term_ids"])
            save_focus(conn, cursor.lastrowid, data["cafe_focus"])
            save_details(conn, cursor.lastrowid, data)
            save_times(conn, cursor.lastrowid, data)
            plan.apply(conn, cursor.lastrowid)
            save_admin_facts(conn, cursor.lastrowid, data, ("admin:" + str(request.session["admin_id"]) if request.session.get("admin_id") else "user:" + str(current_user(request)["id"])))
        flash(request, "장소를 저장했습니다. 공개 상태라면 즉시 사이트에 반영됩니다.", "success")
        return RedirectResponse(url=admin_return(form), status_code=303)

    @app.get("/admin/places/{place_id}/edit", response_class=HTMLResponse)
    async def admin_place_edit_get(request: Request, place_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        conn = connect_db()
        row = conn.execute("SELECT * FROM places WHERE id=? AND deleted_at IS NULL", (place_id,)).fetchone()
        if row and row["merged_into_id"]:
            conn.close()
            return RedirectResponse(f"/admin/places/{row['merged_into_id']}/edit", status_code=303)
        place = row_to_place(row) if row else None
        if place:
            place["term_ids"] = linked_ids(conn, place_id)
        conn.close()
        if not row:
            return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
        return templates.TemplateResponse(request=request, name="admin_place_form.html", context={"place": place, "mode": "edit", **form_options(row["category_id"])})

    @app.post("/admin/places/{place_id}/edit")
    async def admin_place_edit_post(request: Request, place_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        conn = connect_db()
        existing = conn.execute("SELECT * FROM places WHERE id=? AND deleted_at IS NULL AND merged_into_id IS NULL", (place_id,)).fetchone()
        conn.close()
        if not existing:
            return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
        form, data = await parse_place_form(request, existing)
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        error = validate_place(data, existing)
        if error:
            flash(request, error, "error")
            return templates.TemplateResponse(request=request, name="admin_place_form.html", context={"place": data, "mode": "edit", **form_options(existing["category_id"])}, status_code=400)
        try:
            plan = await image_store.prepare(form, "places")
        except ImageError as exc:
            flash(request, str(exc), "error")
            return templates.TemplateResponse(request=request, name="admin_place_form.html", context={"place": {**data, "id": place_id, "legacy_image_url": data["image_url"], "images": image_store.list("places", place_id)}, "mode": "edit", **form_options()}, status_code=400)
        with plan.transaction() as conn:
            conn.execute(
                """
                UPDATE places SET
                    slug=?, name=?, region=?, category=?, emoji=?, image_url=?, is_pick=?,
                    companions=?, features=?, parking_status=?, pet_status=?, child_status=?,
                    accessible_status=?, one_line=?, note=?, address=?, map_url=?,
                    info_source=?, checked_at=?, is_example=?, published=?, updated_at=?, category_id=?, latitude=?, longitude=?
                WHERE id=?
                """,
                (
                    data["slug"], data["name"], data["region"], data["category"], data["emoji"], data["image_url"],
                    data["is_pick"], json.dumps(data["companions"], ensure_ascii=False),
                    json.dumps(data["features"], ensure_ascii=False), data["parking_status"], data["pet_status"],
                    data["child_status"], data["accessible_status"], data["one_line"], data["note"],
                    data["address"], data["map_url"], data["info_source"], data["checked_at"], data["is_example"],
                    data["published"], datetime.now(timezone.utc).isoformat(timespec="seconds"), data["category_id"], data["latitude"], data["longitude"], place_id,
                ),
            )
            save_links(conn, place_id, data["term_ids"])
            save_focus(conn, place_id, data["cafe_focus"])
            save_details(conn, place_id, data)
            save_times(conn, place_id, data)
            plan.apply(conn, place_id)
            save_admin_facts(conn, place_id, data, ("admin:" + str(request.session["admin_id"]) if request.session.get("admin_id") else "user:" + str(current_user(request)["id"])))
        flash(request, "수정사항을 저장했습니다. 승인 절차 없이 즉시 반영됩니다.", "success")
        return RedirectResponse(url=admin_return(form), status_code=303)

    @app.post("/admin/places/{place_id}/toggle-published")
    async def admin_place_toggle_published(request: Request, place_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        form = await request.form()
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        conn = connect_db()
        row = conn.execute("SELECT published FROM places WHERE id=? AND deleted_at IS NULL AND merged_into_id IS NULL", (place_id,)).fetchone()
        if not row:
            conn.close()
            return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
        new_value = 0 if row["published"] else 1
        conn.execute("UPDATE places SET published=?, updated_at=? WHERE id=?", (new_value, datetime.now(timezone.utc).isoformat(timespec="seconds"), place_id))
        conn.commit()
        conn.close()
        flash(request, f"{'공개' if new_value else '비공개'} 상태로 즉시 변경했습니다.", "success")
        return RedirectResponse(url=admin_return(form), status_code=303)

    @app.post("/admin/places/{place_id}/delete")
    async def admin_place_delete(request: Request, place_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        form = await request.form()
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        conn = connect_db()
        row = conn.execute("SELECT name FROM places WHERE id=? AND deleted_at IS NULL AND merged_into_id IS NULL", (place_id,)).fetchone()
        if not row:
            conn.close()
            return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
        conn.close()
        plan = ImagePlan(image_store, "places")
        with plan.transaction() as conn:
            related = conn.execute("SELECT (SELECT count(*) FROM place_pick_periods WHERE place_id=?) + (SELECT count(*) FROM place_correction_suggestions WHERE place_id=?) + (SELECT count(*) FROM best_collection_places WHERE place_id=?) + (SELECT count(*) FROM place_merge_log WHERE source_id=? OR target_id=?)", (place_id,place_id,place_id,place_id,place_id)).fetchone()[0]
            if related:
                for owner in conn.execute("SELECT id FROM places WHERE id=? OR merged_into_id=?", (place_id,place_id)).fetchall():
                    plan.delete_owner(conn, owner[0])
                conn.execute("UPDATE places SET published=0,deleted_at=CURRENT_TIMESTAMP WHERE id=?", (place_id,))
            else:
                plan.delete_owner(conn, place_id)
                conn.execute("DELETE FROM places WHERE id=?", (place_id,))
        flash(request, f"‘{row['name']}’을(를) 즉시 삭제했습니다.", "success")
        return RedirectResponse(url=admin_return(form), status_code=303)

    register_member_routes(app, templates, connect_db, require_csrf, flash,
                           parse_news_form, row_to_news, admin_guard, NEWS_CATEGORIES, secret_key, image_store)
    register_community_routes(app, templates, connect_db, require_csrf, flash, admin_guard,
                              row_to_place, form_options, REGIONS, secret_key, image_store)
    app.state.admin_ui = AdminUI(app, templates, connect_db, admin_guard, require_csrf, flash, row_to_place, row_to_news, NEWS_CATEGORIES, NEWS_CATEGORY_ALIASES)
    from csv_import_routes import register_csv_routes
    register_csv_routes(app, templates, connect_db, admin_guard, require_csrf, flash)
    app.state.admin_ui.register()
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), reload=os.environ.get("APP_RELOAD") == "1")
