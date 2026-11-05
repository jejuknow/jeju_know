"""Optional member details use the existing correction queue, never direct writes."""
import json

from admin_settings import catalog
from place_details import FIELD_LABELS, validate_detail_value
from cafe_features import DETAIL_KEYS
from cafe_visit_times import validate_times
from place_taxonomy import linked_ids, save_links, validate_terms

DETAIL_FIELD = "submission_details"
DETAIL_LABEL = "사용자 세부 정보"
USAGE_LABELS = {"parking_status": "주차", "pet_status": "반려동물", "child_status": "아이 동반",
                "accessible_status": "무장애", "reservation_status": "예약", "waiting_status": "웨이팅"}
MEMBER_USAGE_LABELS = {k: v for k, v in USAGE_LABELS.items() if k not in ("reservation_status", "waiting_status")}
MEMBER_USE_OPTIONS = ("미확인", "가능", "불가")
INPUT_FIELDS = {"term_ids", "taxonomy_present", "detail_note", *("detail_" + k for k in MEMBER_USAGE_LABELS)}
# Ignore removed controls from cached forms and forged requests. They never enter
# the fact UPDATE or the review queue; existing canonical/legacy values survive.
IGNORED_MEMBER_FIELDS = {"details_enabled", "detail_companions", "companion", "companions", "recommended_companion",
                         "reservation", "reservation_status", "detail_reservation_status",
                         "waiting", "waiting_status", "detail_waiting_status"}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_proposal(form, conn, category_id):
    data = {"category_id": category_id, "features": [], "term_ids": form.getlist("term_ids")}
    if error := validate_terms(conn, data):
        raise ValueError(error)
    cafe_terms = [dict(r) for r in conn.execute("SELECT id,member_policy FROM categories WHERE type='cafe_feature' AND parent_id=?", (category_id,))]
    if cafe_terms:
        restricted = {r["id"] for r in cafe_terms if r["member_policy"] == "admin"}
        if set(data["term_ids"]) & restricted:
            raise ValueError("관리자 전용 카페 특징은 회원이 지정할 수 없어요.")
        if len(data["term_ids"]) > 5:
            raise ValueError("카페 특징은 최대 5개까지 선택할 수 있어요.")
    usage = {}
    for key, label in MEMBER_USAGE_LABELS.items():
        value = form.get("detail_" + key, "미확인")
        if value not in MEMBER_USE_OPTIONS:
            raise ValueError(label + " 선택값을 확인해주세요.")
        if value != "미확인":
            usage[key] = value
    note = str(form.get('detail_note', '')).strip()
    if len(note) > 4000 or any(ord(c) < 32 and c not in '\n\r\t' for c in note):
        raise ValueError('꿀팁은 4,000자 이내로 입력해주세요.')
    if not data["term_ids"] and not usage and not note:
        return None
    return {"category_id": category_id, "companions": [], "term_ids": sorted(data["term_ids"]), "usage": usage, **({'note': note} if note else {})}


def snapshot(conn, place, proposal):
    data = {"category_id": place["category_id"], "usage": {key: place[key] for key in proposal["usage"]}}
    if proposal.get('note'):
        data['note'] = place['note']
    if proposal["companions"]:
        data["companions"] = sorted(json.loads(place["companions"] or "[]"))
    if proposal["term_ids"]:
        data["term_ids"] = sorted(linked_ids(conn, place["id"]))
    if proposal.get("cafe_details"):
        data["cafe_details"] = {key: json.loads(place[key] or "[]") if key == "cafe_visit_times" else place[key] for key in proposal["cafe_details"]}
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


def validate_proposal(conn, place, suggestion):
    proposal = json.loads(suggestion["new_value"])
    if not isinstance(proposal, dict) or set(proposal) - {"category_id", "companions", "term_ids", "usage", "note", "cafe_details"}:
        raise ValueError("제안 항목을 확인해주세요.")
    if not isinstance(proposal.get("companions"), list) or not isinstance(proposal.get("term_ids"), list) or not isinstance(proposal.get("usage"), dict):
        raise ValueError("제안 형식을 확인해주세요.")
    if proposal["companions"] or set(proposal["usage"]) - set(MEMBER_USAGE_LABELS):
        raise ValueError("현재 회원 제안에서 허용하지 않는 동행·예약·웨이팅 정보가 포함되어 있습니다. 장소를 직접 확인한 뒤 처리완료하거나 반려해주세요.")
    if json.loads(snapshot(conn, place, proposal)) != json.loads(suggestion["original_value"]):
        raise ValueError("이 제안이 등록된 이후 장소 정보가 수정되었습니다. 최신 정보를 확인한 뒤 처리해주세요.")
    if proposal["category_id"] != place["category_id"]:
        raise ValueError("장소 카테고리가 변경되었습니다. 새 분류를 확인해주세요.")
    data = {"category_id": place["category_id"], "term_ids": proposal["term_ids"], "features": []}
    if error := validate_terms(conn, data):
        raise ValueError(error)
    restricted = {r[0] for r in conn.execute("SELECT id FROM categories WHERE type='cafe_feature' AND member_policy='admin'")}
    if set(data["term_ids"]) & restricted:
        raise ValueError("관리자 전용 카페 특징은 회원 제안으로 반영할 수 없습니다.")
    for key, value in proposal["usage"].items():
        if key not in MEMBER_USAGE_LABELS or value not in MEMBER_USE_OPTIONS or value == "미확인":
            raise ValueError("이용 정보 선택값을 확인해주세요.")
    details = proposal.get("cafe_details", {})
    note = proposal.get('note')
    if note is not None and (not isinstance(note, str) or len(note) > 4000):
        raise ValueError('꿀팁 제안 내용을 확인해주세요.')
    if not isinstance(details, dict):
        raise ValueError("카페 제안 항목을 확인해주세요.")
    category = conn.execute("SELECT slug FROM categories WHERE id=?", (place["category_id"],)).fetchone()
    if category and category[0] == "cafe" and len(data["term_ids"]) > 5:
        raise ValueError("카페 특징은 최대 5개까지 반영할 수 있어요.")
    if details and (not category or category[0] != "cafe"):
        raise ValueError("카페 정보는 카페에서만 반영할 수 있어요.")
    for key, value in details.items():
        if key == "cafe_visit_times":
            validate_times(value)
        elif key not in DETAIL_KEYS or not isinstance(value, str):
            raise ValueError("카페 제안 항목을 확인해주세요.")
        else:
            validate_detail_value(key, value)
    return proposal


def approve_proposal(conn, place, suggestion):
    # Validate the complete batch before the first canonical write.
    proposal = validate_proposal(conn, place, suggestion)
    note, details = proposal.get("note"), proposal.get("cafe_details", {})
    if note:
        conn.execute('UPDATE places SET note=? WHERE id=?', (note, place['id']))
    for key, value in proposal["usage"].items():
        conn.execute(f"UPDATE places SET {key}=? WHERE id=?", (value, place["id"]))
    if proposal["term_ids"]:
        save_links(conn, place["id"], sorted(set(linked_ids(conn, place["id"])) | set(proposal["term_ids"])))
    for key, value in details.items():
        conn.execute(f"UPDATE places SET {key}=? WHERE id=?", (dumps(value) if key == "cafe_visit_times" else value, place["id"]))
    conn.execute("UPDATE places SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (place["id"],))


def display_proposal(conn, value):
    data = json.loads(value)
    lines = []
    if data.get('note'):
        lines.append('꿀팁: ' + data['note'])
    labels = {i["value"]: i["label"] for i in catalog(conn, "companion")}
    if data.get("companions"):
        lines.append("잘 맞는 동행: " + " / ".join(labels.get(c, c) for c in data["companions"]))
    if data.get("term_ids"):
        labels = {r["id"]: r["name"] for r in conn.execute("SELECT id,name FROM categories ORDER BY sort_order,id")}
        lines.append("장소 특징: " + " / ".join([name for i,name in labels.items() if i in data["term_ids"]] + ["이전 분류" for i in data["term_ids"] if i not in labels]))
    lines.extend(USAGE_LABELS.get(k, k) + ": " + v for k,v in data.get("usage", {}).items())
    lines.extend(("추천 시간대" if k == "cafe_visit_times" else FIELD_LABELS.get(k, k)) + ": " + (" / ".join(v) if isinstance(v, list) else v) for k,v in data.get("cafe_details", {}).items())
    return "\n".join(lines) or "미확인"
