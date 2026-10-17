"""Optional member details use the existing correction queue, never direct writes."""
import json

from admin_settings import catalog
from place_details import USE_OPTIONS, WAITING_OPTIONS
from place_taxonomy import linked_ids, save_links, validate_terms

DETAIL_FIELD = "submission_details"
DETAIL_LABEL = "사용자 세부 정보"
USAGE_LABELS = {"parking_status": "주차", "pet_status": "반려동물", "child_status": "아이 동반",
                "accessible_status": "무장애", "reservation_status": "예약", "waiting_status": "웨이팅"}
# Waiting has established facts (none / present / seasonal), unlike admission policies.
WAITING_MAP = {"미확인": "미확인", "가능": "있음", "불가": "없음", "제한": "주말·성수기 있음"}
INPUT_FIELDS = {"details_enabled", "detail_companions", "term_ids", "taxonomy_present",
                *("detail_" + k for k in USAGE_LABELS)}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_proposal(form, conn, category_id):
    if form.get("details_enabled") != "1":
        return None
    companions = list(dict.fromkeys(form.getlist("detail_companions")))
    allowed = {i["value"] for i in catalog(conn, "companion") if i["is_active"]}
    if len(companions) > 30 or any(c not in allowed for c in companions):
        raise ValueError("잘 맞는 동행 선택값을 확인해주세요.")
    data = {"category_id": category_id, "features": [], "term_ids": form.getlist("term_ids")}
    if error := validate_terms(conn, data):
        raise ValueError(error)
    usage = {}
    for key, label in USAGE_LABELS.items():
        value = form.get("detail_" + key, "미확인")
        if value not in USE_OPTIONS:
            raise ValueError(label + " 선택값을 확인해주세요.")
        if value != "미확인":
            usage[key] = WAITING_MAP[value] if key == "waiting_status" else value
    if not companions and not data["term_ids"] and not usage:
        return None
    return {"category_id": category_id, "companions": sorted(companions), "term_ids": sorted(data["term_ids"]), "usage": usage}


def snapshot(conn, place, proposal):
    data = {"category_id": place["category_id"], "usage": {key: place[key] for key in proposal["usage"]}}
    if proposal["companions"]:
        data["companions"] = sorted(json.loads(place["companions"] or "[]"))
    if proposal["term_ids"]:
        data["term_ids"] = sorted(linked_ids(conn, place["id"]))
    return dumps(data)


def queue_proposal(conn, place, user_id, proposal):
    if not proposal:
        return
    value, original = dumps(proposal), snapshot(conn, place, proposal)
    # Retrying a form should not create identical pending review rows.
    if conn.execute("SELECT 1 FROM place_correction_suggestions WHERE place_id=? AND user_id=? AND field=? AND status='pending' AND new_value=? AND original_value=?",
                    (place["id"], user_id, DETAIL_FIELD, value, original)).fetchone():
        return
    conn.execute("INSERT INTO place_correction_suggestions(place_id,user_id,field,new_value,original_value,reason) VALUES (?,?,?,?,?,?)",
                 (place["id"], user_id, DETAIL_FIELD, value, original, "장소 등록·수정 시 사용자가 제공한 선택 정보입니다. 확인 후 승인해주세요."))


def approve_proposal(conn, place, suggestion):
    proposal = json.loads(suggestion["new_value"])
    if snapshot(conn, place, proposal) != suggestion["original_value"]:
        raise ValueError("제안 이후 원본 정보가 변경되었습니다. 최신 정보를 확인하고 새 제안으로 처리해주세요.")
    if proposal["category_id"] != place["category_id"]:
        raise ValueError("장소 카테고리가 변경되었습니다. 새 분류를 확인해주세요.")
    allowed = {i["value"] for i in catalog(conn, "companion") if i["is_active"]}
    if any(c not in allowed for c in proposal["companions"]):
        raise ValueError("제안된 동행 분류가 변경되었습니다. 다시 확인해주세요.")
    data = {"category_id": place["category_id"], "term_ids": proposal["term_ids"], "features": []}
    if error := validate_terms(conn, data):
        raise ValueError(error)
    for key, value in proposal["usage"].items():
        if key not in USAGE_LABELS or value not in (WAITING_OPTIONS if key == "waiting_status" else USE_OPTIONS):
            raise ValueError("이용 정보 선택값을 확인해주세요.")
    # Validate the complete batch before the first canonical write.
    for key, value in proposal["usage"].items():
        conn.execute(f"UPDATE places SET {key}=? WHERE id=?", (value, place["id"]))
    if proposal["companions"]:
        combined = sorted(set(json.loads(place["companions"] or "[]")) | set(proposal["companions"]))
        conn.execute("UPDATE places SET companions=? WHERE id=?", (dumps(combined), place["id"]))
    if proposal["term_ids"]:
        save_links(conn, place["id"], sorted(set(linked_ids(conn, place["id"])) | set(proposal["term_ids"])))
    conn.execute("UPDATE places SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (place["id"],))


def display_proposal(conn, value):
    data = json.loads(value)
    lines = []
    labels = {i["value"]: i["label"] for i in catalog(conn, "companion")}
    if data.get("companions"):
        lines.append("잘 맞는 동행: " + " / ".join(labels.get(c, c) for c in data["companions"]))
    if data.get("term_ids"):
        labels = {r["id"]: r["name"] for r in conn.execute("SELECT id,name FROM categories")}
        lines.append("장소 특징: " + " / ".join(labels.get(i, "이전 분류") for i in data["term_ids"]))
    lines.extend(USAGE_LABELS.get(k, k) + ": " + v for k,v in data.get("usage", {}).items())
    return "\n".join(lines) or "미확인"
