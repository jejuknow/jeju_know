"""One hierarchy shared by editing, rendering and search; no parallel taxonomy."""


def descendants(conn, root_id, active=False):
    condition = " AND is_active=1" if active else ""
    return [dict(r) for r in conn.execute(
        "WITH RECURSIVE tree AS (SELECT * FROM categories WHERE id=?" + condition +
        " UNION ALL SELECT c.* FROM categories c JOIN tree p ON c.parent_id=p.id" +
        (" WHERE c.is_active=1" if active else "") + ") SELECT * FROM tree", (root_id,))]


def branch_terms(conn, category_id, active=False):
    return [r for r in descendants(conn, category_id, active) if r['id'] != category_id]


def official_terms(conn, category_id, *, member=False):
    """Current choices for every form, import and search, including ancestor policy.

    Raw branch_terms remains available for preserving historical associations.
    Never infer an official classification from old labels or seed definitions.
    """
    rows = descendants(conn, category_id)
    root = next((r for r in rows if r['id'] == category_id), None)
    if not root or not root['is_active']:
        return []
    kind = {'cafe': 'cafe_feature', 'food': 'restaurant_subcategory'}.get(root['slug'])
    eligible = {r['id']: r for r in rows if r['id'] != category_id and r['is_active']
                and not r['is_legacy'] and (kind is None or r['type'] == kind)}
    def reachable(row):
        seen = {row['id']}
        parent = row['parent_id']
        while parent != category_id:
            if parent in seen or parent not in eligible:
                return False
            seen.add(parent)
            parent = eligible[parent]['parent_id']
        return True
    return sorted((r for r in eligible.values() if reachable(r)
                   and (not member or r['member_policy'] != 'admin')),
                  key=lambda r: (r['sort_order'], r['id']))


def classification_terms(conn, place_id, category_id):
    rows = branch_terms(conn, category_id)
    by_id = {r['id']: r for r in rows}
    linked = {r[0] for r in conn.execute("SELECT category_id FROM place_category_links WHERE place_id=?", (place_id,))}
    for term_id in list(linked):
        parent = by_id.get(term_id, {}).get('parent_id')
        if parent in by_id:
            linked.add(parent)
    def order(row):
        parent = by_id.get(row['parent_id'])
        return (parent['sort_order'] if parent else row['sort_order'], parent['id'] if parent else row['id'], 1 if parent else 0, row['sort_order'], row['id'])
    return sorted([r for r in rows if r['id'] in linked], key=order)


def usage_count(conn, category_id):
    ids = [r['id'] for r in descendants(conn, category_id)]
    if not ids:
        return 0
    slots = ','.join('?' for _ in ids)
    return conn.execute(f"SELECT count(DISTINCT id) FROM places WHERE category_id IN ({slots}) OR id IN (SELECT place_id FROM place_category_links WHERE category_id IN ({slots}))", (*ids,*ids)).fetchone()[0]


def form_selection(conn, place_id, category_id):
    rows = classification_terms(conn, place_id, category_id) if place_id else []
    majors = [r for r in rows if r['parent_id'] == category_id and not r['is_legacy']]
    main = majors[0] if len(majors) == 1 else None
    children = [r for r in rows if main and r['parent_id'] == main['id']]
    return {'restaurant_main_id': str(main['id']) if main else '',
            'restaurant_subtype_id': str(children[0]['id']) if len(children) == 1 else '',
            'restaurant_subtype_ids': [str(r['id']) for r in children] if len(children) <= 3 else [],
            'restaurant_existing_labels': ' · '.join(r['name'] for r in rows),
            'restaurant_ambiguous': len(majors) > 1 or len(children) > 3 or any(r['is_legacy'] for r in rows)}


FIELDS = {'restaurant_main_id', 'restaurant_subtype_id', 'restaurant_subtype_ids', 'restaurant_taxonomy_present', 'restaurant_replace'}


def values(form, key):
    raw = form.getlist(key) if hasattr(form, 'getlist') else form.get(key, [])
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    return list(dict.fromkeys(str(v).strip() for v in raw if str(v).strip()))


def form_values(form):
    """Keep repeated fields through admin validation and error redisplay."""
    mains = values(form, 'restaurant_main_id')
    return {**{k: form.get(k, '') for k in FIELDS},
            'restaurant_main_id': mains[0] if len(mains) == 1 else mains if mains else '',
            'restaurant_subtype_ids': values(form, 'restaurant_subtype_ids') + values(form, 'restaurant_subtype_id')}


def validate_link_selection(conn, category_id, selected, previous=()):
    """Guard generic term_ids clients too; unchanged historical sets stay intact."""
    if set(selected) == set(previous):
        return
    rows = {r['id']: r for r in official_terms(conn, category_id)}
    if not set(selected).issubset(rows):
        raise ValueError('활성 맛집 분류를 선택해주세요. 이전 분류는 변경 없이 유지할 수 있습니다.')
    mains = {r['id'] if r['parent_id'] == category_id else r['parent_id'] for i in selected for r in [rows[i]]}
    if len(mains) > 1:
        raise ValueError('맛집 대분류는 최대 1개만 선택할 수 있습니다.')
    if sum(rows[i]['parent_id'] != category_id for i in selected) > 3:
        raise ValueError('세부유형은 최대 3개까지 선택할 수 있습니다.')


def parse_selection(conn, form, category_id, existing=None, required=False, *, member=False):
    """Only food inputs replace food links; unrepresented legacy links require opt-in."""
    previous = [r[0] for r in conn.execute("SELECT category_id FROM place_category_links WHERE place_id=?", (existing['id'],))] if existing else []
    category = conn.execute("SELECT slug FROM categories WHERE id=?", (category_id,)).fetchone()
    if not category or category[0] != 'food':
        return None
    if form.get('restaurant_taxonomy_present') != '1':
        if required and not existing:
            raise ValueError('맛집 대분류를 선택해주세요.')
        return None  # Legacy admin clients keep their existing taxonomy contract.
    mains = values(form, 'restaurant_main_id')
    children = list(dict.fromkeys(values(form, 'restaurant_subtype_ids') + values(form, 'restaurant_subtype_id')))
    if len(mains) > 1:
        raise ValueError('맛집 대분류는 최대 1개만 선택할 수 있습니다.')
    if len(children) > 3:
        raise ValueError('세부유형은 최대 3개까지 선택할 수 있습니다.')
    main = mains[0] if mains else ''
    if not main:
        if children or (required and not existing):
            raise ValueError('맛집 대분류를 선택해주세요.')
        return previous if existing and existing['category_id'] == category_id else []
    rows = {str(r['id']): r for r in branch_terms(conn, category_id)}
    selectable = {r['id'] for r in official_terms(conn, category_id, member=member)}
    old_path = {r['id'] for r in classification_terms(conn, existing['id'], category_id)} if existing else set()
    def usable(row):
        return row and not row['is_legacy'] and (row['id'] in selectable or row['id'] in old_path)
    parent = rows.get(main)
    if not usable(parent) or parent['parent_id'] != category_id:
        raise ValueError('활성 맛집 대분류를 선택해주세요.')
    selected = []
    for child in children:
        subtype = rows.get(child)
        if not usable(subtype):
            raise ValueError('활성 세부유형을 선택해주세요: ' + (subtype['name'] if subtype else child))
        if subtype['parent_id'] != parent['id']:
            raise ValueError(f"'{subtype['name']}'은 선택한 대분류 '{parent['name']}'의 세부유형이 아닙니다.")
        selected.append(subtype['id'])
    selected = selected or [parent['id']]
    if existing and existing['category_id'] == category_id and form.get('restaurant_replace') != '1':
        if form_selection(conn, existing['id'], category_id)['restaurant_ambiguous']:
            if not set(selected).issubset(old_path):
                raise ValueError('기존 분류를 변경하려면 교체 항목을 선택해주세요.')
            return previous
    validate_link_selection(conn, category_id, selected, previous)
    return selected


def validate_parent(conn, kind, raw_parent, original=None):
    if kind == 'place':
        return None
    root = conn.execute("SELECT id FROM categories WHERE slug=? AND type='place'", ('food' if kind == 'restaurant_subcategory' else 'cafe',)).fetchone()
    if not root:
        raise ValueError('상위 카테고리를 확인해주세요.')
    if kind != 'restaurant_subcategory':
        return root[0]
    try:
        parent_id = int(raw_parent) if raw_parent not in (None,'') else (original['parent_id'] if original else root[0])
    except (ValueError, TypeError):
        raise ValueError('상위 분류를 확인해주세요.') from None
    parent = conn.execute("SELECT * FROM categories WHERE id=?", (parent_id,)).fetchone()
    if not parent or (parent_id != root[0] and (parent['type'] != kind or parent['parent_id'] != root[0] or parent['is_legacy'])):
        raise ValueError('맛집 또는 맛집 대분류를 상위 분류로 선택해주세요.')
    if original and (parent_id == original['id'] or (parent_id != root[0] and conn.execute('SELECT 1 FROM categories WHERE parent_id=?', (original['id'],)).fetchone())):
        raise ValueError('자기 자신 또는 하위 분류가 있는 항목은 세부유형의 상위 분류로 이동할 수 없습니다.')
    return parent_id
