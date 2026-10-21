"""Shared DB classifications and public search used by home, list and map."""
from taxonomy_migrations import json_list, legacy_term_ids
from community_migrations import today
from admin_settings import catalog
from new_visibility import new_clause
from restaurant_taxonomy import branch_terms, descendants

CORE_ORDER = {"food": 0, "cafe": 1, "attraction": 2}


def navigation_order(categories):
    return sorted(categories, key=lambda c: (0 if c["slug"] in CORE_ORDER else 1, c["sort_order"], c["id"]))


def taxonomy(conn, regions, companions, include_inactive=False):
    rows = [dict(r) for r in conn.execute("SELECT * FROM categories ORDER BY sort_order,id")]
    parents = navigation_order([r for r in rows if r["type"] == "place" and (include_inactive or r["is_active"])])
    visible_ids = {r['id'] for parent in parents for r in branch_terms(conn, parent['id'], active=not include_inactive)}
    terms = [r for r in rows if r['id'] in visible_ids and (include_inactive or r['type'] != 'cafe_feature' or not r['is_legacy'])]
    region_items, companion_items = catalog(conn, 'region', include_inactive), catalog(conn, 'companion', include_inactive)
    return {"categories": parents, "terms": terms, "cafe_focus": catalog(conn, 'cafe_focus', include_inactive),
            "cafe_focus_labels": {i['value']:i['label'] for i in catalog(conn, 'cafe_focus', True)},
            "region_labels": {i['value']:i['label'] for i in catalog(conn, 'region', True)},
            "companion_labels": {i['value']:i['label'] for i in catalog(conn, 'companion', True)},
            "regions": [i['value'] for i in region_items], "companions": [i['value'] for i in companion_items]}


def linked_ids(conn, place_id):
    return [r[0] for r in conn.execute("SELECT category_id FROM place_category_links WHERE place_id=?", (place_id,))]


def validate_terms(conn, data, existing=None):
    previous = linked_ids(conn, existing["id"]) if existing else []
    cafe = conn.execute("SELECT slug FROM categories WHERE id=?", (data["category_id"],)).fetchone()
    is_cafe = cafe and cafe[0] == "cafe"
    if data["term_ids"] is None:  # Old clients keep associations and may still submit legacy tags.
        candidates = set(previous + ([] if is_cafe else legacy_term_ids(conn, data["category_id"], data["features"])))
        data["term_ids"] = [r['id'] for r in branch_terms(conn, data['category_id']) if r['id'] in candidates]
        return None
    try:
        selected = list(dict.fromkeys(int(v) for v in data["term_ids"]))
    except (ValueError, TypeError):
        return "세부 분류를 확인해 주세요."
    branch = {r['id'] for r in branch_terms(conn, data['category_id'])}
    allowed = {r['id'] for r in branch_terms(conn, data['category_id'], active=True) if not r.get('is_legacy')} | (set(previous) & branch)
    if len(selected) > 100 or not set(selected).issubset(allowed):
        return "선택한 상위 카테고리의 활성 세부 분류를 선택해 주세요."
    data["term_ids"] = selected
    if is_cafe:
        # Hidden legacy/inactive values must survive an unrelated admin form save.
        hidden = {r['id'] for r in branch_terms(conn, data['category_id']) if r['is_legacy'] or not r['is_active']}
        data["term_ids"] = sorted(set(selected) | (set(previous) & hidden))
    return None


def save_links(conn, place_id, term_ids):
    conn.execute("DELETE FROM place_category_links WHERE place_id=?", (place_id,))
    conn.executemany("INSERT INTO place_category_links(place_id,category_id) VALUES (?,?)", [(place_id, i) for i in term_ids])


def public_search(conn, category="", q="", region="", companion="", detail="", condition="", pick="", new="", focus="", cuisine=""):
    if any(len(v) > limit for v, limit in ((category, 80), (q, 200), (region, 100), (companion, 1000), (detail, 8100), (condition, 40))):
        return []
    # Retired orientation filters are never reinterpreted as specific cafe features.
    if focus:
        return []
    clauses, params = ["p.published=1", "p.deleted_at IS NULL", "p.merged_into_id IS NULL"], []
    if pick not in ("", "current") or new not in ("", "current"):
        return []
    now = today()
    if pick:
        clauses.append("EXISTS(SELECT 1 FROM place_pick_periods k WHERE k.place_id=p.id AND k.year=? AND k.month=? AND k.is_active=1)")
        params.extend([now.year,now.month])
    if new:
        clause, values = new_clause(now)
        clauses.append(clause)
        params.extend(values)
    if category:
        clauses.append("c.slug=? AND c.type='place' AND c.is_active=1")
        params.append(category)
    if q.strip():
        clauses.append("(instr(lower(p.name || ' ' || p.one_line || ' ' || p.region || ' ' || p.address || ' ' || CASE WHEN c.slug='cafe' THEN '' ELSE p.features END || ' ' || COALESCE(p.signature_menu,'') || ' ' || COALESCE(p.recommended_menu,'')),lower(?))>0 OR EXISTS(SELECT 1 FROM place_category_links ql JOIN categories qt ON qt.id=ql.category_id WHERE ql.place_id=p.id AND qt.parent_id=p.category_id AND qt.type='cafe_feature' AND qt.is_active=1 AND qt.is_legacy=0 AND instr(lower(qt.name),lower(?))>0))")
        params.extend([q.strip(), q.strip()])
    details = list(dict.fromkeys(v for v in detail.split(",") if v))
    if details or cuisine:
        # Unknown, inactive and mismatched terms produce no results; never silently relax filters.
        root = conn.execute("SELECT id FROM categories WHERE slug=? AND type='place' AND is_active=1", (category,)).fetchone()
        allowed = {r['slug']:r for r in branch_terms(conn, root[0], active=True) if r['type'] != 'cafe_feature' or not r['is_legacy']} if root else {}
        if len(details) > 100 or not set(details).issubset(allowed) or len(cuisine)>80:
            return []
        if cuisine:
            main = allowed.get(cuisine)
            if category != 'food' or not main or main['parent_id'] != root[0] or main['is_legacy']:
                return []
            valid = {r['slug'] for r in descendants(conn, main['id'], active=True)}
            if not set(details).issubset(valid):
                return []
        ids = {r['id'] for slug in (details or [cuisine]) for r in descendants(conn, allowed[slug]['id'], active=True)}
        clauses.append("EXISTS(SELECT 1 FROM place_category_links l WHERE l.place_id=p.id AND l.category_id IN (" + ",".join("?" for _ in ids) + "))")
        params.extend(sorted(ids))
    rows = conn.execute("SELECT p.*,c.slug AS category_slug FROM places p LEFT JOIN categories c ON c.id=p.category_id WHERE " + " AND ".join(clauses) + " ORDER BY p.is_pick DESC,p.updated_at DESC,p.id DESC", params).fetchall()
    region_label = lambda v: {"제주시": "제주시내", "서귀포시": "서귀포시내"}.get(v, v)
    companions = [v for v in companion.split(",") if v]
    results = []
    for row in rows:
        if region and region_label(row["region"]) != region_label(region):
            continue
        if not all(v in json_list(row["companions"]) for v in companions):
            continue
        if condition:
            if condition != "rain" or not any(v in ("비오는날", "비올때", "비 오는 날", "비올때 추천") for v in json_list(row["features"])):
                continue
        results.append(row)
    return results
