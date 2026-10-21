"""Cafe-only editorial visit advice, deliberately separate from search filters."""
import json

OPTIONS = ("오전", "낮", "노을", "저녁")


def validate_times(values):
    if not isinstance(values, list) or len(values) > 4 or any(v not in OPTIONS for v in values):
        raise ValueError("추천 시간대는 오전·낮·노을·저녁 중 선택해주세요.")
    return [v for v in OPTIONS if v in values]


def parse_times(form, existing=None):
    return form.getlist("cafe_visit_times") if form.get("cafe_visit_times_present") == "1" else json.loads(dict(existing or {}).get("cafe_visit_times") or "[]")


def save_times(conn, place_id, data):
    conn.execute("UPDATE places SET cafe_visit_times=? WHERE id=?", (json.dumps(data["cafe_visit_times"], ensure_ascii=False), place_id))
