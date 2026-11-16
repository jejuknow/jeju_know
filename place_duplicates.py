"""Conservative duplicate suggestions; never changes or merges place records."""
import math
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache
from itertools import combinations

from sqlite_migrations import verified_migration

NEAR_METERS = 75
LEVELS = {"high": "중복 의심 높음", "medium": "중복 의심", "low": "확인 필요"}
PRIORITY = {"high": 0, "medium": 1, "low": 2}
FIELDS = ("id", "name", "address", "phone", "category_id", "category", "region", "latitude", "longitude",
          "slug", "published", "is_example", "image_url")
ACTIVE = "deleted_at IS NULL AND merged_into_id IS NULL"


def migrate(database):
    with verified_migration(database, "20261116_place_duplicate_ignores", "jejuno-before-duplicate-detection") as conn:
        if conn is None:
            return
        conn.execute("""CREATE TABLE place_duplicate_ignores (
            place_low INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
            place_high INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
            ignored_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ignored_by INTEGER REFERENCES admins(id),
            ignored_by_member INTEGER REFERENCES users(id),
            PRIMARY KEY(place_low,place_high), CHECK(place_low<place_high),
            CHECK((ignored_by IS NULL)!=(ignored_by_member IS NULL)))""")


def normalize_place_name(value):
    # Whitespace/case differences only: retain branch names, punctuation and digits.
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")).casefold())


def normalize_address(value):
    value = unicodedata.normalize("NFKC", str(value or "")).strip()
    value = re.sub(r"^(?:제주특별자치도|제주도|제주)\s+(?=제주시\b|서귀포시\b)", "", value)
    return normalize_place_name(value)


def normalize_phone(value):
    digits = re.sub(r"\D", "", unicodedata.normalize("NFKC", str(value or "")))
    return digits if 7 <= len(digits) <= 20 and len(set(digits)) > 1 else ""


def calculate_place_distance(left, right):
    try:
        a, b, c, d = (float(p.get(k)) for p, k in ((left,"latitude"),(left,"longitude"),(right,"latitude"),(right,"longitude")))
        if not all(math.isfinite(v) for v in (a,b,c,d)) or not (-90<=a<=90 and -90<=c<=90 and -180<=b<=180 and -180<=d<=180):
            return None
        lat1, lat2 = math.radians(a), math.radians(c)
        h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(math.radians(d-b)/2)**2
        return 6371000 * 2 * math.asin(math.sqrt(min(1, max(0,h))))
    except (ValueError, TypeError):
        return None


def pair(left, right):
    return tuple(sorted((int(left), int(right))))


def category_key(place):
    return str(place.get("category_id") or place.get("category") or "")


def compare(left, right):
    name = normalize_place_name(left.get("name"))
    address = normalize_address(left.get("address"))
    phone = normalize_phone(left.get("phone"))
    region = normalize_place_name(left.get("region")).replace("시내", "시")
    same_name = bool(name and name == normalize_place_name(right.get("name")))
    same_address = bool(address and address == normalize_address(right.get("address")))
    same_phone = bool(phone and phone == normalize_phone(right.get("phone")))
    same_category = bool(category_key(left) and category_key(left) == category_key(right))
    same_region = bool(region and region == normalize_place_name(right.get("region")).replace("시내", "시"))
    distance = calculate_place_distance(left, right) if same_name else None
    near = distance is not None and distance <= NEAR_METERS
    if (same_name and (same_address or near)) or (same_phone and same_category):
        level = "high"
    elif same_address and same_category:
        level = "medium"
    elif same_name and same_region:
        level = "low"
    else:
        return None
    reasons = [label for flag,label in ((same_name,"장소명 동일"),(same_address,"주소 동일"),
        (same_phone,"전화번호 동일"),(same_category,"카테고리 동일"),(same_region,"지역 동일")) if flag]
    if near:
        reasons.append(f"좌표 {distance:.0f}m 이내")
    return {"level":level,"label":LEVELS[level],"reasons":reasons,"distance":distance,
            "exact":same_name and same_address}


def rows(conn):
    cursor = conn.execute("SELECT " + ",".join(FIELDS) + " FROM places WHERE " + ACTIVE + " ORDER BY id")
    return [dict(zip(FIELDS, row)) for row in cursor.fetchall()]


def ignores(conn):
    return set(tuple(row) for row in conn.execute("SELECT place_low,place_high FROM place_duplicate_ignores"))


def find_duplicate_candidates(conn, data, exclude_id=None, *, include_private=False, source=None):
    excluded = ignores(conn) if exclude_id else set()
    result = []
    for place in source if source is not None else rows(conn):
        if place["id"] == exclude_id or (exclude_id and pair(place["id"],exclude_id) in excluded):
            continue
        if not include_private and (not place["published"] or place["is_example"]):
            continue
        if match := compare(data, place):
            result.append({**place, **match})
    return sorted(result, key=lambda p:(PRIORITY[p["level"]],p["id"]))


@lru_cache(maxsize=8)
def _grouped(values, ignored):
    places = {p[0]:dict(zip(FIELDS,p)) for p in values}
    buckets = defaultdict(list)
    for p in places.values():
        for kind, key in (("name",normalize_place_name(p["name"])),("address",normalize_address(p["address"])),("phone",normalize_phone(p["phone"]))):
            if key:
                buckets[kind,key].append(p["id"])
    candidates = set()
    for ids in buckets.values():
        candidates.update(combinations(ids,2))
    excluded = set(ignored)
    edges = []
    parent = {i:i for i in places}
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a,b in sorted(candidates - excluded):
        if match := compare(places[a],places[b]):
            edges.append({"left":a,"right":b,**match})
            parent[root(b)] = root(a)
    groups = {}
    for edge in edges:
        group = groups.setdefault(root(edge["left"]), {"ids":set(),"pairs":[]})
        group["ids"].update((edge["left"],edge["right"]))
        group["pairs"].append(edge)
    result = []
    for group in groups.values():
        members = [places[i] for i in sorted(group["ids"])]
        level = min((e["level"] for e in group["pairs"]),key=PRIORITY.get)
        result.append({"id":members[0]["id"],"places":members,"pairs":group["pairs"],"level":level,
                       "label":LEVELS[level],"reasons":list(dict.fromkeys(r for e in group["pairs"] for r in e["reasons"]))})
    return sorted(result, key=lambda g:(PRIORITY[g["level"]],g["id"]))


def groups(conn):
    # O(n) content signature; compare indexed candidates only when data/decisions change.
    return _grouped(tuple(tuple(p[k] for k in FIELDS) for p in rows(conn)),tuple(sorted(ignores(conn))))


def membership(conn):
    return {p["id"]:g for g in groups(conn) for p in g["places"]}


def public_candidate(place):
    return {"name":place["name"],"image_url":place["image_url"],"region":place["region"],
            "category":place["category"],"url":"/place/"+place["slug"]}
