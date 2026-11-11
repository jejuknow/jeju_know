"""Activation safety for place categories; deletion retains its stricter policy."""
from restaurant_taxonomy import descendants

# Audited Home/FIND/MAP/NEW/BEST, forms and CSV need stable core rows/IDs,
# not is_active=1. Those references are protected by the separate delete/slug
# rules. Only a feature that truly requires an ACTIVE category belongs here.
REQUIRED_ACTIVE_CATEGORIES = {}


def usage_filter(conn, category_id):
    """Same live-place predicate for the admin count, link and OFF guard."""
    ids = [r['id'] for r in descendants(conn, category_id)]
    if not ids:
        return '0=1', []
    slots = ','.join('?' for _ in ids)
    return ("p.deleted_at IS NULL AND p.merged_into_id IS NULL AND "
            f"(p.category_id IN ({slots}) OR EXISTS (SELECT 1 FROM place_category_links l "
            f"WHERE l.place_id=p.id AND l.category_id IN ({slots})))", ids + ids)


def activation_policy(conn, category):
    clause, args = usage_filter(conn, category['id'])
    count = conn.execute('SELECT count(*) FROM places p WHERE ' + clause, args).fetchone()[0]
    dependency = REQUIRED_ACTIVE_CATEGORIES.get(category['slug'], '')
    reason = (f'현재 {count}곳의 장소에서 사용 중인 카테고리입니다. 연결된 장소가 있어 비활성화할 수 없습니다.'
              if count else ('필수 시스템 기능에서 사용 중이므로 비활성화할 수 없습니다. ' + dependency if dependency else ''))
    return dict(place_count=count, activation_locked=bool(category['is_active'] and reason),
                activation_reason=reason, activation_label='사용 중' if count else '시스템 필수')


def protect_active(conn, category, active):
    # Recalculate after BEGIN IMMEDIATE, including a place added since page load.
    if category.get('type') == 'place' and not active:
        reason = activation_policy(conn, category)['activation_reason']
        if reason:
            raise ValueError(reason)


def selection_error(conn, category_id, previous_id=None):
    row = conn.execute("SELECT is_active FROM categories WHERE id=? AND type='place'", (category_id,)).fetchone()
    if not row or (not row['is_active'] and category_id != previous_id):
        return '활성 카테고리를 선택해 주세요. 카테고리 상태가 변경되었다면 새로고침 후 다시 시도해주세요.'
    return None
