import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from place_migrations import backup_before_migration, migrate_place_categories

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


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256$200000${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


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

    app = FastAPI(title="JEJUNO")
    app.state.database = str(database_path)
    app.state.testing = bool(test_config.get("TESTING"))
    app.state.kakao_map_key = os.environ.get("KAKAO_MAP_JS_KEY", "").strip()
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        same_site="lax",
        https_only=os.environ.get("SESSION_COOKIE_SECURE") == "1",
    )
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    def context_processor(request: Request):
        if "csrf_token" not in request.session:
            request.session["csrf_token"] = secrets.token_urlsafe(32)
        flashes = request.session.pop("flashes", [])
        return {
            "csrf_token": request.session["csrf_token"], "flashes": flashes,
            "news_categories": NEWS_CATEGORIES, "news_category_labels": NEWS_CATEGORY_LABELS,
        }

    templates = Jinja2Templates(directory=BASE_DIR / "templates", context_processors=[context_processor])

    def connect_db():
        conn = sqlite3.connect(app.state.database)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db():
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

    init_db()

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
        if not sent or not expected or not hmac.compare_digest(sent, expected):
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
        return place

    def row_to_news(row):
        item = dict(row)
        item["stored_category"] = item["category"]
        item["category"] = NEWS_CATEGORY_ALIASES.get(item["category"], item["category"])
        item["is_featured"] = bool(item.get("is_featured"))
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
        return item

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
        form = await request.form()
        slug = unique_slug(form.get("slug"), existing["id"] if existing else None)
        return form, {
            "slug": slug,
            "name": str(form.get("name", "")).strip(),
            "region": str(form.get("region", "")).strip(),
            "category": str(form.get("category", "")).strip(),
            "category_id": str(form.get("category_id", "")).strip(),
            "latitude": str(form.get("latitude", existing["latitude"] if existing and existing["latitude"] is not None else "")).strip(),
            "longitude": str(form.get("longitude", existing["longitude"] if existing and existing["longitude"] is not None else "")).strip(),
            "emoji": str(form.get("emoji", "📍")).strip() or "📍",
            "image_url": str(form.get("image_url", "")).strip(),
            "is_pick": 1 if form.get("is_pick") == "1" else 0,
            "companions": form.getlist("companions"),
            "features": form.getlist("features"),
            "parking_status": str(form.get("parking_status", "미확인")),
            "pet_status": str(form.get("pet_status", "미확인")),
            "child_status": str(form.get("child_status", "미확인")),
            "accessible_status": str(form.get("accessible_status", "미확인")),
            "one_line": str(form.get("one_line", "")).strip(),
            "reason": str(form.get("reason", "")).strip(),
            "note": str(form.get("note", "")).strip(),
            "address": str(form.get("address", "")).strip(),
            "map_url": str(form.get("map_url", "")).strip(),
            "info_source": str(form.get("info_source", "")).strip(),
            "checked_at": str(form.get("checked_at", "")).strip(),
            "is_example": 1 if form.get("is_example") == "1" else 0,
            "published": 1 if form.get("published") == "1" else 0,
        }

    async def parse_news_form(request: Request, existing=None):
        form = await request.form()
        slug = unique_news_slug(form.get("slug"), existing["id"] if existing else None)
        return form, {
            "slug": slug,
            "title": str(form.get("title", "")).strip(),
            "category": str(form.get("category", "")).strip(),
            "summary": str(form.get("summary", "")).strip(),
            "body": str(form.get("body", "")).strip(),
            "image_url": str(form.get("image_url", "")).strip(),
            "venue": str(form.get("venue", "")).strip(),
            "event_start": str(form.get("event_start", "")).strip(),
            "event_end": str(form.get("event_end", "")).strip(),
            "external_url": str(form.get("external_url", "")).strip(),
            "source_name": str(form.get("source_name", "")).strip(),
            "checked_at": str(form.get("checked_at", "")).strip(),
            "is_featured": 1 if form.get("is_featured") == "1" else 0,
            "is_sponsored": 1 if form.get("is_sponsored") == "1" else 0,
            "sponsor_name": str(form.get("sponsor_name", "")).strip(),
            "published": 1 if form.get("published") == "1" else 0,
        }

    def place_categories(current_id=None, active_only=True):
        conn = connect_db()
        rows = conn.execute(
            "SELECT * FROM categories" + (" WHERE is_active=1 OR id=?" if active_only else "") + " ORDER BY sort_order,id",
            (current_id,) if active_only else (),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def validate_place(data, existing=None):
        if not data["name"] or not data["region"]:
            return "장소명과 지역을 확인해 주세요."
        conn = connect_db()
        if data["category_id"]:
            category = conn.execute("SELECT * FROM categories WHERE id=?", (data["category_id"],)).fetchone()
        else:  # Preserve the existing category-name POST contract.
            category = conn.execute("SELECT * FROM categories WHERE name=?", (data["category"],)).fetchone()
        conn.close()
        if not category or (not category["is_active"] and (not existing or existing["category_id"] != category["id"])):
            return "활성 카테고리를 선택해 주세요. 기존 장소의 비활성 분류는 유지할 수 있습니다."
        data["category_id"], data["category"] = category["id"], category["name"]
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

    def form_options(current_id=None):
        return {
            "regions": REGIONS,
            "categories": place_categories(current_id),
            "kakao_map_key": app.state.kakao_map_key,
            "companions": COMPANIONS,
            "features": FEATURES,
            "status_options": STATUS_OPTIONS,
        }

    def admin_guard(request: Request):
        if not request.session.get("admin_id"):
            return RedirectResponse(url=f"/admin/login?next={request.url.path}", status_code=303)
        return None

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        conn = connect_db()
        news_rows = conn.execute(
            "SELECT * FROM news_posts WHERE published=1 ORDER BY is_featured DESC, event_start DESC, updated_at DESC LIMIT 4"
        ).fetchall()
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"news_items": [row_to_news(row) for row in news_rows]},
        )

    @app.get("/place/{slug}", response_class=HTMLResponse)
    async def place_detail(request: Request, slug: str):
        conn = connect_db()
        row = conn.execute("SELECT * FROM places WHERE slug=? AND published=1", (slug,)).fetchone()
        conn.close()
        if not row:
            return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
        return templates.TemplateResponse(request=request, name="place_detail.html", context={"place": row_to_place(row)})

    @app.get("/api/places")
    async def api_places(category: str = "", q: str = ""):
        conn = connect_db()
        # Existing unfiltered response is preserved. Map search reuses this public-only API.
        clauses, params = ["p.published=1"], []
        if category:
            clauses.append("c.slug=? AND c.is_active=1")
            params.append(category)
        if q.strip():
            clauses.append("instr(lower(p.name || ' ' || p.one_line || ' ' || p.region || ' ' || p.address || ' ' || p.features),lower(?))>0")
            params.append(q.strip())
        rows = conn.execute("SELECT p.*, c.slug AS category_slug FROM places p LEFT JOIN categories c ON c.id=p.category_id WHERE " + " AND ".join(clauses) + " ORDER BY p.is_pick DESC,p.updated_at DESC,p.id DESC", params).fetchall()
        conn.close()
        return JSONResponse([row_to_place(row) for row in rows], headers={"Cache-Control": "no-store"})

    @app.get("/api/categories")
    async def api_categories():
        return JSONResponse(place_categories(), headers={"Cache-Control": "no-store"})

    @app.get("/map", response_class=HTMLResponse)
    async def map_page(request: Request):
        return templates.TemplateResponse(request=request, name="map.html", context={
            "categories": place_categories(), "kakao_map_key": app.state.kakao_map_key,
        })

    @app.get("/news", response_class=HTMLResponse)
    async def news_list(request: Request):
        category = (request.query_params.get("category") or "").strip()
        category = NEWS_CATEGORY_ALIASES.get(category, category)
        q = (request.query_params.get("q") or "").strip()
        clauses = ["published=1"]
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
        row = conn.execute("SELECT * FROM news_posts WHERE slug=? AND published=1", (slug,)).fetchone()
        conn.close()
        if not row:
            return HTMLResponse("소식을 찾을 수 없습니다.", status_code=404)
        return templates.TemplateResponse(request=request, name="news_detail.html", context={"item": row_to_news(row)})

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
        elif len(password) < 8:
            flash(request, "비밀번호는 8자 이상 입력해주세요.", "error")
        elif password != password_confirm:
            flash(request, "비밀번호 확인이 일치하지 않습니다.", "error")
        else:
            conn = connect_db()
            conn.execute("INSERT INTO admins(username, password_hash) VALUES (?, ?)", (username, hash_password(password)))
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
        conn = connect_db()
        admin = conn.execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()
        conn.close()
        if admin and verify_password(password, admin["password_hash"]):
            csrf = request.session.get("csrf_token") or secrets.token_urlsafe(32)
            request.session.clear()
            request.session["admin_id"] = admin["id"]
            request.session["admin_username"] = admin["username"]
            request.session["csrf_token"] = csrf
            next_url = request.query_params.get("next") or "/admin"
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
        guard = admin_guard(request)
        if guard:
            return guard
        conn = connect_db()
        rows = conn.execute("SELECT * FROM places ORDER BY updated_at DESC, id DESC").fetchall()
        news_rows = conn.execute("SELECT * FROM news_posts ORDER BY updated_at DESC, id DESC").fetchall()
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="admin_dashboard.html",
            context={
                "places": [row_to_place(row) for row in rows],
                "news_items": [row_to_news(row) for row in news_rows],
            },
        )

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
        if not data["title"] or data["category"] not in NEWS_CATEGORIES:
            flash(request, "제목과 카테고리는 필수입니다.", "error")
            return RedirectResponse(url="/admin/news/new", status_code=303)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn = connect_db()
        conn.execute(
            """
            INSERT INTO news_posts (
                slug, title, category, summary, body, image_url, venue,
                event_start, event_end, external_url, source_name, checked_at,
                is_featured, is_sponsored, sponsor_name, published, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["slug"], data["title"], data["category"], data["summary"], data["body"],
                data["image_url"], data["venue"], data["event_start"], data["event_end"],
                data["external_url"], data["source_name"], data["checked_at"], data["is_featured"],
                data["is_sponsored"], data["sponsor_name"], data["published"], now,
            ),
        )
        conn.commit()
        conn.close()
        flash(request, "제주 소식을 저장했습니다.", "success")
        return RedirectResponse(url="/admin#news-admin", status_code=303)

    @app.get("/admin/news/{news_id}/edit", response_class=HTMLResponse)
    async def admin_news_edit_get(request: Request, news_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        conn = connect_db()
        row = conn.execute("SELECT * FROM news_posts WHERE id=?", (news_id,)).fetchone()
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
        existing = conn.execute("SELECT * FROM news_posts WHERE id=?", (news_id,)).fetchone()
        conn.close()
        if not existing:
            return HTMLResponse("소식을 찾을 수 없습니다.", status_code=404)
        form, data = await parse_news_form(request, existing)
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        if not data["title"] or data["category"] not in NEWS_CATEGORIES:
            flash(request, "제목과 카테고리는 필수입니다.", "error")
            return RedirectResponse(url=f"/admin/news/{news_id}/edit", status_code=303)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn = connect_db()
        conn.execute(
            """
            UPDATE news_posts SET
                slug=?, title=?, category=?, summary=?, body=?, image_url=?, venue=?,
                event_start=?, event_end=?, external_url=?, source_name=?, checked_at=?,
                is_featured=?, is_sponsored=?, sponsor_name=?, published=?, updated_at=?
            WHERE id=?
            """,
            (
                data["slug"], data["title"], data["category"], data["summary"], data["body"],
                data["image_url"], data["venue"], data["event_start"], data["event_end"],
                data["external_url"], data["source_name"], data["checked_at"], data["is_featured"],
                data["is_sponsored"], data["sponsor_name"], data["published"], now, news_id,
            ),
        )
        conn.commit()
        conn.close()
        flash(request, "제주 소식을 수정했습니다.", "success")
        return RedirectResponse(url="/admin#news-admin", status_code=303)

    @app.post("/admin/news/{news_id}/toggle-published")
    async def admin_news_toggle_published(request: Request, news_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        form = await request.form()
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        conn = connect_db()
        row = conn.execute("SELECT published FROM news_posts WHERE id=?", (news_id,)).fetchone()
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
        return RedirectResponse(url="/admin#news-admin", status_code=303)

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
        conn.execute("DELETE FROM news_posts WHERE id=?", (news_id,))
        conn.commit()
        conn.close()
        flash(request, f"‘{row['title']}’ 소식을 삭제했습니다.", "success")
        return RedirectResponse(url="/admin#news-admin", status_code=303)

    @app.get("/admin/categories", response_class=HTMLResponse)
    async def admin_categories(request: Request):
        guard = admin_guard(request)
        if guard:
            return guard
        conn = connect_db()
        rows = conn.execute("SELECT c.*,COUNT(p.id) AS place_count FROM categories c LEFT JOIN places p ON p.category_id=c.id GROUP BY c.id ORDER BY c.sort_order,c.id").fetchall()
        conn.close()
        return templates.TemplateResponse(request=request, name="admin_categories.html", context={"categories": [dict(r) for r in rows]})

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
            return RedirectResponse("/admin/categories", status_code=303)
        conn = connect_db()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                if category_id is None:
                    conn.execute("INSERT INTO categories(name,slug,sort_order,is_active) VALUES (?,?,?,?)", (name, slug, order, int(form.get("is_active") == "1")))
                else:
                    if not conn.execute("SELECT id FROM categories WHERE id=?", (category_id,)).fetchone():
                        return HTMLResponse("카테고리를 찾을 수 없습니다.", status_code=404)
                    # The stable slug survives renaming so shared map URLs continue to work.
                    conn.execute("UPDATE categories SET name=?,sort_order=?,is_active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (name, order, int(form.get("is_active") == "1"), category_id))
                    conn.execute("UPDATE places SET category=? WHERE category_id=?", (name, category_id))
            flash(request, "카테고리를 저장했습니다. 방문자 화면에 바로 반영됩니다.")
        except sqlite3.IntegrityError:
            flash(request, "이미 사용 중인 카테고리 이름 또는 슬러그입니다.", "error")
        finally:
            conn.close()
        return RedirectResponse("/admin/categories", status_code=303)

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
                count = conn.execute("SELECT COUNT(*) FROM places WHERE category_id=?", (category_id,)).fetchone()[0]
                if count:
                    flash(request, f"현재 {count}개 장소에서 사용 중인 카테고리입니다. 삭제 대신 비활성화할 수 있습니다.", "error")
                else:
                    conn.execute("DELETE FROM categories WHERE id=?", (category_id,))
                    flash(request, "사용하지 않는 카테고리를 삭제했습니다.")
        finally:
            conn.close()
        return RedirectResponse("/admin/categories", status_code=303)

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
        conn = connect_db()
        conn.execute(
            """
            INSERT INTO places (
                slug, name, region, category, emoji, image_url, is_pick,
                companions, features, parking_status, pet_status, child_status,
                accessible_status, one_line, reason, note, address, map_url,
                info_source, checked_at, is_example, published, updated_at, category_id, latitude, longitude
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["slug"], data["name"], data["region"], data["category"], data["emoji"], data["image_url"],
                data["is_pick"], json.dumps(data["companions"], ensure_ascii=False),
                json.dumps(data["features"], ensure_ascii=False), data["parking_status"], data["pet_status"],
                data["child_status"], data["accessible_status"], data["one_line"], data["reason"], data["note"],
                data["address"], data["map_url"], data["info_source"], data["checked_at"], data["is_example"],
                data["published"], datetime.now(timezone.utc).isoformat(timespec="seconds"),
                data["category_id"], data["latitude"], data["longitude"],
            ),
        )
        conn.commit()
        conn.close()
        flash(request, "장소를 저장했습니다. 공개 상태라면 즉시 사이트에 반영됩니다.", "success")
        return RedirectResponse(url="/admin", status_code=303)

    @app.get("/admin/places/{place_id}/edit", response_class=HTMLResponse)
    async def admin_place_edit_get(request: Request, place_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        conn = connect_db()
        row = conn.execute("SELECT * FROM places WHERE id=?", (place_id,)).fetchone()
        conn.close()
        if not row:
            return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
        return templates.TemplateResponse(request=request, name="admin_place_form.html", context={"place": row_to_place(row), "mode": "edit", **form_options(row["category_id"])})

    @app.post("/admin/places/{place_id}/edit")
    async def admin_place_edit_post(request: Request, place_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        conn = connect_db()
        existing = conn.execute("SELECT * FROM places WHERE id=?", (place_id,)).fetchone()
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
        conn = connect_db()
        conn.execute(
            """
            UPDATE places SET
                slug=?, name=?, region=?, category=?, emoji=?, image_url=?, is_pick=?,
                companions=?, features=?, parking_status=?, pet_status=?, child_status=?,
                accessible_status=?, one_line=?, reason=?, note=?, address=?, map_url=?,
                info_source=?, checked_at=?, is_example=?, published=?, updated_at=?, category_id=?, latitude=?, longitude=?
            WHERE id=?
            """,
            (
                data["slug"], data["name"], data["region"], data["category"], data["emoji"], data["image_url"],
                data["is_pick"], json.dumps(data["companions"], ensure_ascii=False),
                json.dumps(data["features"], ensure_ascii=False), data["parking_status"], data["pet_status"],
                data["child_status"], data["accessible_status"], data["one_line"], data["reason"], data["note"],
                data["address"], data["map_url"], data["info_source"], data["checked_at"], data["is_example"],
                data["published"], datetime.now(timezone.utc).isoformat(timespec="seconds"), data["category_id"], data["latitude"], data["longitude"], place_id,
            ),
        )
        conn.commit()
        conn.close()
        flash(request, "수정사항을 저장했습니다. 승인 절차 없이 즉시 반영됩니다.", "success")
        return RedirectResponse(url="/admin", status_code=303)

    @app.post("/admin/places/{place_id}/toggle-published")
    async def admin_place_toggle_published(request: Request, place_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        form = await request.form()
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        conn = connect_db()
        row = conn.execute("SELECT published FROM places WHERE id=?", (place_id,)).fetchone()
        if not row:
            conn.close()
            return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
        new_value = 0 if row["published"] else 1
        conn.execute("UPDATE places SET published=?, updated_at=? WHERE id=?", (new_value, datetime.now(timezone.utc).isoformat(timespec="seconds"), place_id))
        conn.commit()
        conn.close()
        flash(request, f"{'공개' if new_value else '비공개'} 상태로 즉시 변경했습니다.", "success")
        return RedirectResponse(url="/admin", status_code=303)

    @app.post("/admin/places/{place_id}/delete")
    async def admin_place_delete(request: Request, place_id: int):
        guard = admin_guard(request)
        if guard:
            return guard
        form = await request.form()
        if not require_csrf(request, form):
            return HTMLResponse("CSRF 검증에 실패했습니다.", status_code=400)
        conn = connect_db()
        row = conn.execute("SELECT name FROM places WHERE id=?", (place_id,)).fetchone()
        if not row:
            conn.close()
            return HTMLResponse("장소를 찾을 수 없습니다.", status_code=404)
        conn.execute("DELETE FROM places WHERE id=?", (place_id,))
        conn.commit()
        conn.close()
        flash(request, f"‘{row['name']}’을(를) 즉시 삭제했습니다.", "success")
        return RedirectResponse(url="/admin", status_code=303)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), reload=os.environ.get("APP_RELOAD") == "1")
