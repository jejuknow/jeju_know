"""Read-only comparison and shared status/count rules for correction review."""
import json

from community_places import MEMBER_SUGGESTION_FIELDS
from submission_suggestions import DETAIL_FIELD, snapshot, display_proposal, validate_proposal
from user_status import CORRECTION

STATUS_LABELS = {key: badge.label for key, badge in CORRECTION.items()}
PENDING = "s.status='pending'"


def pending_count(conn):
    return conn.execute("SELECT count(*) FROM place_correction_suggestions s WHERE " + PENDING).fetchone()[0]


def original_value(place, field):
    if field == "opening":
        return json.dumps([place["opened_year"], place["opened_month"]])
    if field == "coordinates":
        return json.dumps([place["latitude"], place["longitude"]])
    if field == "closed":
        return str(place["published"])
    if field in ("business", "other"):
        return ""
    return str(place[field]) if field in place.keys() and place[field] is not None else ""


def describe(conn, row, place):
    item = dict(row)
    field = item["field"]
    item.update(current_value="", stale=False, block_reason="", place_available=bool(place and not place["deleted_at"] and not place["merged_into_id"]))
    if not item["place_available"]:
        item["block_reason"] = "삭제·병합된 장소를 먼저 확인해주세요. 제안을 처리완료하거나 반려할 수 있습니다."
    if field == DETAIL_FIELD and place:
        try:
            current = snapshot(conn, place, json.loads(row["new_value"]))
            item["stale"] = json.loads(current) != json.loads(row["original_value"])
            item["current_value"] = display_proposal(conn, current)
            item["original_value"] = display_proposal(conn, row["original_value"])
            item["new_value"] = display_proposal(conn, row["new_value"])
            if not item["block_reason"]:
                validate_proposal(conn, place, row)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            item["block_reason"] = str(exc) if isinstance(exc, ValueError) else "제안 형식을 확인해주세요. 직접 확인 후 처리완료하거나 반려할 수 있습니다."
    elif place:
        current = original_value(place, field)
        item["current_value"] = current
        item["stale"] = current != row["original_value"]
        if field not in MEMBER_SUGGESTION_FIELDS:
            item["block_reason"] = "현재 허용하지 않는 제안 항목입니다. 장소를 직접 확인한 뒤 처리완료하거나 반려해주세요."
        elif field in ("business", "other"):
            item["block_reason"] = "장소 수정에서 필요한 정보를 반영한 뒤 ‘직접 확인 후 처리완료’를 선택해주세요."
        elif item["stale"]:
            item["block_reason"] = "이 제안이 등록된 이후 장소 정보가 수정되었습니다. 최신 정보를 확인한 뒤 처리해주세요."
        if field == "category_id":
            for key in ("current_value", "original_value", "new_value"):
                category = conn.execute("SELECT name FROM categories WHERE id=?", (item[key],)).fetchone()
                if category:
                    item[key] = category[0]
        elif field == "closed":
            for key in ("current_value", "original_value"):
                item[key] = {"1": "공개", "0": "비공개"}.get(item[key], "제안 당시 공개 상태 기록 없음")
    return item
