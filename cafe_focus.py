"""Optional cafe orientations shared by forms, taxonomy, search and place cards."""
import json
from taxonomy_migrations import json_list
from admin_settings import catalog

CAFE_FOCUS = (
    {"value": "taste", "label": "맛 중심"},
    {"value": "space", "label": "공간 중심"},
    {"value": "experience", "label": "이색 경험"},
)
FOCUS_LABELS = {item["value"]: item["label"] for item in CAFE_FOCUS}


def parse_focus(form, existing=None):
    if form.get("cafe_focus_present") == "1" or "cafe_focus" in form:
        return form.getlist("cafe_focus")
    return json_list(dict(existing or {}).get("cafe_focus"))


def validate_focus(conn, data, existing=None):
    values = list(dict.fromkeys(data.get("cafe_focus", [])))
    allowed = {i['value'] for i in catalog(conn, 'cafe_focus')}
    allowed.update(json_list(dict(existing or {}).get('cafe_focus')))
    if len(values) > 2 or any(v not in allowed for v in values):
        raise ValueError("카페 성향은 안내된 항목 중 최대 2개까지 선택해주세요.")
    category = conn.execute("SELECT slug FROM categories WHERE id=?", (data["category_id"],)).fetchone()
    data["cafe_focus"] = values if category and category[0] == "cafe" else []


def save_focus(conn, place_id, values):
    conn.execute("UPDATE places SET cafe_focus=? WHERE id=?", (json.dumps(values), place_id))


def decorate_focus(place, conn=None):
    labels = {i['value']: i['label'] for i in catalog(conn, 'cafe_focus', True)} if conn else FOCUS_LABELS
    values = json_list(place.get("cafe_focus")) if place.get("category_slug") == "cafe" else []
    place["cafe_focus"] = [v for v in values if v in labels]
    place["cafe_focus_labels"] = [labels[v] for v in place["cafe_focus"]]
