"""Deterministic input checks, not editorial verification or factual validation."""
import json
import math
import re
import unicodedata
from difflib import SequenceMatcher

LABELS = {"normal": "정상", "review": "확인 필요", "hold": "공개 보류"}
REASONS = {
    "location": "위치 확인 필요", "outside": "제주 범위를 벗어난 지도 위치",
    "similar": "유사한 장소 확인 필요", "photo": "사진 미등록",
    "unsafe": "일반 텍스트가 아닌 입력 확인 필요", "spam": "반복 입력 확인 필요",
    "paused": "자동 선공개 중지 · 운영자 확인 필요",
}
ORDER = "CASE p.auto_check_status WHEN 'hold' THEN 0 WHEN 'review' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END"
BURST_LIMIT, BURST_SECONDS = 10, 600


def normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "").casefold())


def name_key(value):
    return re.sub(r"[^\w]", "", normalized(value)).replace("_", "")


def address_key(value):
    value = unicodedata.normalize("NFKC", value or "").strip()
    # Common province aliases are equivalent, but do not erase building/unit numbers.
    value = re.sub(r"^(?:제주특별자치도|제주도|제주)\s+(?=제주시\b|서귀포시\b)", "", value)
    return normalized(value)


def validate_address(data):
    address = unicodedata.normalize("NFKC", data["address"]).strip()
    if not re.match(r"^(?:(?:제주특별자치도|제주도|제주)\s+)?(?:제주시|서귀포시)\s+\S", address):
        raise ValueError("제주 지역의 장소만 등록할 수 있습니다. 제주시 또는 서귀포시가 포함된 주소를 다시 확인해주세요.")


def duplicate_matches(conn, data, exclude_id=None):
    exact, similar = [], []
    name, address = name_key(data["name"]), address_key(data["address"])
    for row in conn.execute("""SELECT id,slug,name,address,latitude,longitude,published FROM places
                              WHERE deleted_at IS NULL AND merged_into_id IS NULL"""):
        if row["id"] == exclude_id:
            continue
        same_address = address == address_key(row["address"])
        if name == name_key(row["name"]) and same_address:
            exact.append(dict(row))
            continue
        near = False
        if all(v is not None for v in (data.get("latitude"), data.get("longitude"), row["latitude"], row["longitude"])):
            dy = (data["latitude"] - row["latitude"]) * 111320
            dx = (data["longitude"] - row["longitude"]) * 111320 * math.cos(math.radians(row["latitude"]))
            near = math.hypot(dx, dy) < 100
        if (same_address or near) and SequenceMatcher(None, name, name_key(row["name"])).ratio() >= .8:
            similar.append(dict(row))
    return exact, similar


def inspect(data, *, similar=False, has_photo=False, enabled=False):
    hard, soft = [], []
    lat, lng = data.get("latitude"), data.get("longitude")
    if lat is None or lng is None:
        hard.append("location")
    elif not (math.isfinite(lat) and math.isfinite(lng) and 33.1 <= lat <= 34.05 and 126.1 <= lng <= 127.05):
        # Jeju main island, Udo and Chuja. This bounds check cannot prove an address matches a point.
        hard.append("outside")
    text = "\n".join(str(v) for v in data.values() if isinstance(v, str))
    if re.search(r"<\s*/?\s*[a-z][^>]*>|javascript\s*:|\bon[a-z]+\s*=", text, re.I):
        hard.append("unsafe")
    if re.search(r"([^\w\s])\1{11,}|(.{5,80}?)\2{5,}", text) or len(re.findall(r"https?://", text, re.I)) >= 8:
        hard.append("spam")
    if similar:
        soft.append("similar")
    if not has_photo:
        soft.append("photo")
    if not enabled:
        hard.append("paused")
    reasons = hard + soft
    return ("hold" if hard else "review" if soft else "normal"), reasons


def decorate(place):
    state = place.get("auto_check_status")
    place["auto_check_label"] = LABELS.get(state, "이전 등록 · 자동검사 기록 없음")
    place["auto_check_tone"] = {"hold": "warning", "review": "warning", "normal": "neutral"}.get(state, "neutral")
    try:
        codes = json.loads(place.get("auto_check_reason") or "[]")
    except (TypeError, ValueError):
        codes = []
    place["auto_check_reasons"] = [REASONS[c] for c in codes if isinstance(c, str) and c in REASONS] if isinstance(codes, list) else []
    return place


def counts(conn):
    values = dict.fromkeys(LABELS, 0)
    for state, count in conn.execute("""SELECT auto_check_status,count(*) FROM places WHERE source_type='user'
        AND verification_status='unverified' AND deleted_at IS NULL AND merged_into_id IS NULL GROUP BY auto_check_status"""):
        if state in values:
            values[state] = count
    return values


def record(conn, place_id, state, reasons, published):
    conn.execute("""UPDATE places SET auto_check_status=?,auto_check_reason=?,auto_checked_at=CURRENT_TIMESTAMP
                    WHERE id=?""", (state, json.dumps(reasons), place_id))
    # System action: no administrator impersonation or member/session secrets in the audit log.
    conn.execute("""INSERT INTO admin_activity_logs(actor_member_id,actor_username,actor_name,action,target_type,target_id,target_name)
                    SELECT submitted_by_user_id,'system:auto-check','자동검사',?,'장소',id,? FROM places WHERE id=?""",
                 ("사용자 장소 자동 선공개" if published else "사용자 장소 공개 보류", LABELS[state], place_id))
