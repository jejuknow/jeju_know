"""Optional visit facts, kept separate from editorial recommendations and filters."""
from datetime import date
import json
import re
import unicodedata

TEXT_LIMITS = {
    "business_hours": 200, "closed_days": 200, "last_order": 100,
    "signature_menu": 1000, "recommended_menu": 1000,
    "dessert_note": 300, "food_taste_note": 300,
}
# Keep the historical detail migration's field list stable.
CONTACT_TEXT_LIMITS = {"phone": 80}
BASIC_INFO_LIMITS = {k: TEXT_LIMITS[k] for k in ("business_hours", "closed_days", "signature_menu")}
BASIC_INFO_LIMITS.update(CONTACT_TEXT_LIMITS)
SUBMISSION_CHOICES = {"tamnaneunjeon_status": ("미확인", "가능", "불가")}
FIELD_LABELS = {
    "phone": "전화번호",
    "representative_price": "가격", "tamnaneunjeon_status": "탐나는전",
    "business_hours": "영업시간", "closed_days": "휴무일", "last_order": "라스트오더",
    "signature_menu": "대표 메뉴", "recommended_menu": "추천 메뉴",
    "dessert_note": "디저트·베이커리 특징", "food_taste_note": "음식/맛 특징",
    "reservation_status": "예약", "waiting_status": "웨이팅", "coffee_taste": "커피 맛 특징",
}
USE_OPTIONS = ("미확인", "가능", "불가", "제한")
WAITING_OPTIONS = ("미확인", "없음", "있음", "주말·성수기 있음")
COFFEE_OPTIONS = ("미확인", "산미 있음", "산미 적음", "고소한 편", "밸런스형", "기타")
CHOICE_OPTIONS = {"reservation_status": USE_OPTIONS, "waiting_status": WAITING_OPTIONS, "coffee_taste": COFFEE_OPTIONS}
DETAIL_FIELDS = (*TEXT_LIMITS, *CONTACT_TEXT_LIMITS, *CHOICE_OPTIONS, "representative_price", *SUBMISSION_CHOICES)


def normalize_phone(value):
    value = " ".join(str(value or "").split())
    if re.fullmatch(r"0[0-9]{8,10}", value):
        prefix = 2 if value.startswith("02") else 3
        return value[:prefix] + "-" + value[prefix:-4] + "-" + value[-4:]
    return value


def parse_price(value):
    value = str(value if value is not None else "").strip().replace(",", "").removesuffix("원").strip()
    if not value:
        return None
    if not re.fullmatch(r"[0-9]{1,8}", value):
        raise ValueError("가격은 0~99,999,999원 사이의 숫자로 입력해주세요.")
    return int(value)


def phone_link(value):
    # Loose input is retained as text; only ordinary numbers become dial links.
    number = unicodedata.normalize("NFKC", normalize_phone(value))
    if not re.fullmatch(r"\+?[0-9][0-9\s().-]*", number):
        return ""
    digits = re.sub(r"[^0-9+]", "", number)
    return "tel:" + digits if len(digits.lstrip("+")) >= 3 else ""


def parse_details(form, existing=None):
    previous = dict(existing or {})
    data = {field: str(form.get(field, previous.get(field) if previous.get(field) is not None else ("미확인" if field in CHOICE_OPTIONS or field in SUBMISSION_CHOICES else ""))).strip()
            for field in DETAIL_FIELDS}
    data["phone"] = normalize_phone(data["phone"])
    return data


def validate_detail_value(field, value):
    if field == "representative_price":
        return parse_price(value)
    limits = {**TEXT_LIMITS, **CONTACT_TEXT_LIMITS}
    if field == "phone":
        value = normalize_phone(value)
    if field in limits and len(value) > limits[field]:
        raise ValueError(f"{FIELD_LABELS[field]}은 {limits[field]:,}자 이내로 입력해주세요.")
    if field in CHOICE_OPTIONS and value not in CHOICE_OPTIONS[field]:
        raise ValueError(f"{FIELD_LABELS[field]} 선택값을 확인해주세요.")
    if field in SUBMISSION_CHOICES and value not in SUBMISSION_CHOICES[field]:
        raise ValueError(f"{FIELD_LABELS[field]} 선택값을 확인해주세요.")
    if field == "parking_status" and value not in USE_OPTIONS:
        raise ValueError("주차는 미확인·가능·불가·제한 중 선택해주세요.")
    return value


def validate_details(data):
    for field in DETAIL_FIELDS:
        if field in CHOICE_OPTIONS and not data[field]:
            data[field] = "미확인"
        data[field] = validate_detail_value(field, data[field])
    checked = data["checked_at"]
    if checked:
        try:
            if len(checked) != 10 or date.fromisoformat(checked).isoformat() != checked:
                raise ValueError
        except ValueError:
            raise ValueError("정보 확인일은 실제 확인한 날짜를 YYYY-MM-DD 형식으로 입력해주세요.") from None
    if len(data["info_source"]) > 500:
        raise ValueError("정보 출처는 500자 이내로 입력해주세요.")


def save_details(conn, place_id, data):
    # Column identifiers come only from this module's allowlist.
    assignments = ",".join(f"{field}=?" for field in DETAIL_FIELDS)
    conn.execute(f"UPDATE places SET {assignments} WHERE id=?", (*[data[field] for field in DETAIL_FIELDS], place_id))


def decorate_details(conn, place):
    place["phone_href"] = phone_link(place.get("phone"))
    price = place.get("representative_price")
    place["price_label"] = f"{price:,}원" if price is not None else "미확인"
    place["tamnaneunjeon_status"] = place.get("tamnaneunjeon_status") or "미확인"
    category = conn.execute("SELECT slug FROM categories WHERE id=?", (place.get("category_id"),)).fetchone()
    place["category_slug"] = category[0] if category else ""
    for field in (*CHOICE_OPTIONS, "parking_status", "pet_status", "child_status", "accessible_status"):
        place[field] = place.get(field) or "미확인"
    for field in ("signature_menu", "recommended_menu"):
        place[field + "_items"] = [line.strip() for line in (place.get(field) or "").splitlines() if line.strip()]
    from restaurant_taxonomy import classification_terms, form_selection, official_terms
    place["classification_terms"] = classification_terms(conn, place['id'], place.get('category_id'))
    if place['category_slug'] == 'cafe':
        current = {r['id'] for r in official_terms(conn, place.get('category_id'))}
        place["classification_terms"] = [r for r in place["classification_terms"] if r['id'] in current]
    if place['category_slug'] == 'food':
        # Disabled official food paths remain readable on existing records.
        # Legacy associations stay in storage and the admin review form only.
        place['classification_terms'] = [r for r in place['classification_terms']
                                         if r['type'] == 'restaurant_subcategory' and not r['is_legacy']]
        place.update(form_selection(conn, place['id'], place.get('category_id')))
    terms = [r["name"] for r in place["classification_terms"]]
    # Cafe presentation uses the same managed terms as FIND/MAP. Legacy tags stay stored.
    place["restaurant_label"] = ' · '.join(terms) if place['category_slug'] == 'food' else ''
    place["cafe_visit_times"] = json.loads(place.get("cafe_visit_times") or "[]")
    place["detail_features"] = terms if place["category_slug"] in ("cafe", "food") else list(dict.fromkeys(place.get("features_public", [])))
    if place["category_slug"] == "cafe":
        # Shared PICK cards also use official features; raw legacy tags remain stored.
        usage = [label for field, label in (("parking_status", "주차"), ("accessible_status", "무장애")) if place.get(field) == "가능"]
        place["features_public"] = list(dict.fromkeys(terms + usage))
    return place
