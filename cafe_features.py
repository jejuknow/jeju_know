"""Shared cafe choice permissions. Official features live only in categories."""
from place_details import validate_detail_value
from cafe_visit_times import validate_times

DETAIL_KEYS = ("recommended_menu", "coffee_taste", "dessert_note")
FIELDS = {"cafe_features_present", "cafe_feature_ids", "cafe_value_suggestion", "detail_cafe_visit_times", *("detail_" + k for k in DETAIL_KEYS)}
MAX_MEMBER_FEATURES = 5


def parse_member_features(conn, form, category_id, existing=None):
    category = conn.execute("SELECT slug FROM categories WHERE id=?", (category_id,)).fetchone()
    if not category or category[0] != "cafe":
        if form.getlist("cafe_feature_ids") or form.get("cafe_value_suggestion") == "1" or any(form.get("detail_" + k) for k in DETAIL_KEYS) or form.getlist("detail_cafe_visit_times"):
            raise ValueError("카페 특징은 카페에서만 선택할 수 있어요.")
        return None, None
    if form.get("cafe_features_present") != "1":
        if form.getlist("cafe_feature_ids") or form.get("cafe_value_suggestion") == "1":
            raise ValueError("카페 특징 입력을 새로고침 후 다시 확인해주세요.")
        return None, None
    choices = {str(r["id"]): dict(r) for r in conn.execute(
        "SELECT * FROM categories WHERE parent_id=? AND type='cafe_feature' AND is_active=1 AND is_legacy=0", (category_id,))}
    selected = list(dict.fromkeys(form.getlist("cafe_feature_ids")))
    # Accept the prior cached form's value checkbox, but always queue it for review.
    if form.get("cafe_value_suggestion") == "1":
        value = next((r for r in choices.values() if r["slug"] == "cafe-value" or r["name"] == "가성비"), None)
        if not value:
            raise ValueError("가성비 제안 항목이 변경되었습니다. 새로고침해주세요.")
        selected = list(dict.fromkeys(selected + [str(value["id"])]))
    legacy_proposal = form.getlist("term_ids") if form.get("details_enabled") == "1" else []
    if len(set(selected + legacy_proposal)) > MAX_MEMBER_FEATURES:
        raise ValueError("카페 특징은 최대 5개까지 선택할 수 있어요.")
    if any(value not in choices or choices[value]["member_policy"] == "admin" for value in selected):
        raise ValueError("일반 회원이 선택할 수 있는 카페 특징을 확인해주세요.")
    previous = [r[0] for r in conn.execute("SELECT category_id FROM place_category_links WHERE place_id=?", (existing["id"],))] if existing else []
    details = {}
    for key in DETAIL_KEYS:
        value = str(form.get("detail_" + key, "")).strip()
        if value and value != "미확인":
            details[key] = validate_detail_value(key, value)
    if form.get("details_enabled") == "1" and form.getlist("detail_cafe_visit_times"):
        details["cafe_visit_times"] = validate_times(form.getlist("detail_cafe_visit_times"))
    proposed = sorted({int(v) for v in selected} - set(previous))
    pending = {"category_id": category_id, "companions": [], "term_ids": proposed, "usage": {}, "cafe_details": details} if proposed or details else None
    # Never replace canonical associations in a member request.
    return None, pending


def combine_proposals(proposal, feature_proposal):
    if not proposal:
        return feature_proposal
    if feature_proposal:
        proposal["term_ids"] = sorted(set(proposal["term_ids"] + feature_proposal["term_ids"]))
        proposal["cafe_details"] = feature_proposal.get("cafe_details", {})
    return proposal
