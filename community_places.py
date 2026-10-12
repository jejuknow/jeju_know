"""Shared place facts, editorial attributes, duplicate detection and safe merging."""
import math
import re
import unicodedata
from difflib import SequenceMatcher

from auth_security import safe_external_url
from community_migrations import today

FACT_LIMITS = {"name": 120, "region": 40, "address": 300, "one_line": 600,
               "map_url": 2000, "website_url": 2000}
FACT_FIELDS = (*FACT_LIMITS, "category_id", "opened_year", "opened_month", "latitude", "longitude")
VERIFICATION_LABELS = {"unverified": "정보 미확인", "community_confirmed": "정보 확인됨", "jejuno_verified": "JEJUNO 확인"}
SUGGESTION_FIELDS = {"name": "장소명 변경", "address": "주소 변경·이전", "closed": "폐업",
                     "category_id": "카테고리 오류", "opening": "오픈 정보", "coordinates": "지도 위치",
                     "map_url": "지도 링크", "website_url": "홈페이지·Instagram", "other": "기타"}


def normalize(value):
    return re.sub(r"[^\w가-힣]", "", unicodedata.normalize("NFKC", value or "").casefold()).replace("_", "")


def opening(year, month):
    try:
        y = int(year) if year not in (None, "") else None
        m = int(month) if month not in (None, "") else None
        if y is not None and not 1900 <= y <= today().year:
            raise ValueError
        if m is not None and (y is None or not 1 <= m <= 12 or (y == today().year and m > today().month)):
            raise ValueError
        return y, m
    except (ValueError, TypeError):
        raise ValueError("오픈 시기는 현재까지의 연도·월로 입력해주세요. 월을 모르면 비워두세요.")


def coordinates(lat, lng):
    if lat in (None, "") and lng in (None, ""):
        return None, None
    try:
        lat, lng = float(lat), float(lng)
        if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError
        return lat, lng
    except (TypeError, ValueError):
        raise ValueError("위도와 경도를 모두 올바르게 입력하거나 두 칸을 비워주세요.")


def facts(form, conn, regions, *, new_only=False, existing=None):
    data = {key: str(form.get(key, "")).strip() for key in FACT_LIMITS}
    if any(len(data[k]) > limit or any(ord(c) < 32 and c not in '\n\r\t' for c in data[k]) for k, limit in FACT_LIMITS.items()):
        raise ValueError("입력 길이를 확인해주세요. 장소명 120자, 주소 300자, 소개 600자까지 가능합니다.")
    if not data["name"] or not data["address"] or data["region"] not in regions:
        raise ValueError("장소명, 지역, 주소는 필수입니다.")
    category = conn.execute("SELECT * FROM categories WHERE id=? AND type='place'", (str(form.get("category_id", "")),)).fetchone()
    if not category or (not category["is_active"] and (not existing or category["id"] != existing["category_id"])):
        raise ValueError("활성 장소 카테고리를 선택해주세요.")
    if new_only and category["slug"] not in ("food", "cafe"):
        raise ValueError("신상 장소는 카페 또는 맛집으로 등록해주세요.")
    data["category_id"], data["category"] = category["id"], category["name"]
    data["opened_year"], data["opened_month"] = opening(form.get("opened_year"), form.get("opened_month"))
    if new_only and data["opened_year"] is None:
        raise ValueError("신상 장소의 오픈 연도를 입력해주세요.")
    data["latitude"], data["longitude"] = coordinates(form.get("latitude"), form.get("longitude"))
    if not all(safe_external_url(data[k]) for k in ("map_url", "website_url")):
        raise ValueError("지도·홈페이지 링크는 올바른 http 또는 https URL로 입력해주세요.")
    return data


def duplicates(conn, data, exclude_id=None, include_private=False):
    name, address = normalize(data.get("name")), normalize(data.get("address"))
    if not name:
        return []
    rows = conn.execute("SELECT id,slug,name,address,region,latitude,longitude,published FROM places WHERE deleted_at IS NULL AND merged_into_id IS NULL" + ("" if include_private else " AND published=1")).fetchall()
    found = []
    for row in rows:
        if row["id"] == exclude_id:
            continue
        same_name = name == normalize(row["name"])
        similar = SequenceMatcher(None, name, normalize(row["name"])).ratio() >= .55
        same_address = bool(address and address == normalize(row["address"]))
        near = False
        if all(v is not None for v in (data.get("latitude"), data.get("longitude"), row["latitude"], row["longitude"])):
            dy = (float(data["latitude"]) - row["latitude"]) * 111320
            dx = (float(data["longitude"]) - row["longitude"]) * 111320 * math.cos(math.radians(row["latitude"]))
            near = math.hypot(dx, dy) < 75
        if same_name or (similar and (same_address or near)):
            found.append(dict(row))
    return found


def editable(place, user_id):
    return (place["source_type"] == "user" and place["submitted_by_user_id"] == user_id
            and place["verification_status"] == "unverified" and not place["deleted_at"] and not place["merged_into_id"])


def decorate_facts(conn, place):
    now = today()
    period = conn.execute("SELECT * FROM place_pick_periods WHERE place_id=? AND year=? AND month=? AND is_active=1", (place["id"],now.year,now.month)).fetchone()
    place["is_pick"] = bool(period)
    place["pick_note"] = period["pick_note"] if period else ""
    place["pick_order"] = period["sort_order"] if period else 0
    place["status_label"] = ("사용자 등록 · " if place["source_type"] == "user" else "제주노 등록 · ") + VERIFICATION_LABELS[place["verification_status"]]
    place["opening_label"] = (f'{place["opened_year"]}년' + (f' {place["opened_month"]}월' if place["opened_month"] else "") + " 오픈") if place["opened_year"] else ""
    return place


def save_current_pick(conn, place_id, active, note=""):
    now = today()
    if not active and not conn.execute("SELECT 1 FROM place_pick_periods WHERE place_id=? AND year=? AND month=?", (place_id,now.year,now.month)).fetchone():
        return
    conn.execute("""INSERT INTO place_pick_periods(place_id,year,month,pick_note,is_active) VALUES (?,?,?,?,?)
        ON CONFLICT(place_id,year,month) DO UPDATE SET is_active=excluded.is_active""", (place_id,now.year,now.month,note,int(active)))


def save_admin_facts(conn, place_id, data, actor):
    conn.execute("UPDATE places SET opened_year=?,opened_month=?,website_url=?,new_override=?,verification_status=?,verified_at=CASE WHEN ?='jejuno_verified' THEN COALESCE(verified_at,CURRENT_TIMESTAMP) ELSE NULL END,verified_by=CASE WHEN ?='jejuno_verified' THEN ? ELSE NULL END WHERE id=?",
                 (data["opened_year"],data["opened_month"],data["website_url"],data["new_override"],data["verification_status"],data["verification_status"],data["verification_status"],actor,place_id))
    save_current_pick(conn, place_id, data["is_pick"], data["reason"])


def merge_places(conn, source_id, target_id, actor):
    source = conn.execute("SELECT * FROM places WHERE id=?", (source_id,)).fetchone()
    target = conn.execute("SELECT * FROM places WHERE id=?", (target_id,)).fetchone()
    if not source or not target or source_id == target_id or any(r["merged_into_id"] or r["deleted_at"] for r in (source,target)):
        raise ValueError("병합할 두 개의 서로 다른 현재 장소를 선택해주세요.")
    # Both original rows and their photos remain. Old slugs redirect to the canonical row.
    conn.execute("UPDATE places SET merged_into_id=?,published=0,updated_at=CURRENT_TIMESTAMP WHERE id=? OR merged_into_id=?", (target_id,source_id,source_id))
    conn.execute("UPDATE place_correction_suggestions SET place_id=? WHERE place_id=?", (target_id,source_id))
    conn.execute("INSERT OR IGNORE INTO best_collection_places SELECT collection_id,?,sort_order FROM best_collection_places WHERE place_id=?", (target_id,source_id))
    conn.execute("""INSERT OR IGNORE INTO place_pick_periods(place_id,year,month,sort_order,pick_note,is_active,created_at)
        SELECT ?,year,month,sort_order,pick_note,is_active,created_at FROM place_pick_periods WHERE place_id=?""", (target_id,source_id))
    if source["category_id"] == target["category_id"]:
        conn.execute("INSERT OR IGNORE INTO place_category_links SELECT ?,category_id FROM place_category_links WHERE place_id=?", (target_id,source_id))
    conn.execute("INSERT INTO place_merge_log(source_id,target_id,merged_by) VALUES (?,?,?)", (source_id,target_id,actor))
