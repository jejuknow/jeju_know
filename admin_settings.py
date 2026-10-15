"""Small operating settings; stable taxonomy values never rewrite place data."""
import json
from taxonomy_migrations import json_list

REGIONS = ["제주시", "애월", "한림", "한경", "구좌", "조천", "성산", "표선", "남원", "서귀포시", "대정", "안덕", "기타"]
COMPANIONS = ["혼자", "연인", "친구", "부모님", "아이", "반려동물", "단체"]
FOCUS = [("taste", "맛 중심"), ("space", "공간 중심"), ("experience", "이색 경험")]
DEFAULTS = {"site_name": "JEJU_KNOW", "instagram_url": "https://www.instagram.com/jeju_know/",
            "site_description": "JEJU_KNOW가 기록하는 이유 있는 제주 추천",
            "home_pick": True, "home_new": True, "home_issue": True, "home_best": True}


def read_settings(conn):
    settings = dict(DEFAULTS)
    for key, value in conn.execute("SELECT key,value FROM site_settings WHERE key NOT LIKE 'taxonomy:%'"):
        if key in DEFAULTS:
            settings[key] = json.loads(value)
    return settings


def write_setting(conn, key, value):
    conn.execute("INSERT INTO site_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
                 (key, json.dumps(value, ensure_ascii=False)))


def catalog(conn, kind, include_inactive=False):
    row = conn.execute("SELECT value FROM site_settings WHERE key=?", ("taxonomy:" + kind,)).fetchone()
    if row:
        items = json.loads(row[0])
    else:
        pairs = FOCUS if kind == "cafe_focus" else [(v, {"제주시": "제주시내", "서귀포시": "서귀포시내"}.get(v, v)) for v in (REGIONS if kind == "region" else COMPANIONS)]
        items = [dict(value=value, label=label, is_active=True, sort_order=i) for i, (value, label) in enumerate(pairs, 1)]
    # Preserve old values that were saved before the catalog existed.
    if kind != "cafe_focus":
        known = {i["value"] for i in items}
        for row in conn.execute("SELECT region,companions FROM places"):
            values = [row[0]] if kind == "region" else json_list(row[1])
            for value in values:
                if value and value not in known:
                    items.append(dict(value=value, label=value, is_active=True, sort_order=len(items)+1))
                    known.add(value)
    return sorted([i for i in items if include_inactive or i["is_active"]], key=lambda i: (i["sort_order"], i["value"]))


def catalog_usage(conn, kind, value):
    if kind == "region":
        return conn.execute("SELECT count(*) FROM places WHERE region=?", (value,)).fetchone()[0]
    column = "cafe_focus" if kind == "cafe_focus" else "companions"
    return sum(value in json_list(r[0]) for r in conn.execute("SELECT " + column + " FROM places"))
