"""Flat admin presentation and catalog writes without replacing existing stores."""
import re
import uuid

from admin_settings import catalog, catalog_usage, write_setting
from restaurant_taxonomy import usage_count
from taxonomy_order import (CATALOGS, CORE_SLUGS, catalog_id, compact, flat_location,
                            flat_parent, position, siblings, state)


def flat_view(conn, kind, query):
    parent = flat_parent(conn, kind)
    rows = siblings(conn, parent, kind)
    for row in rows:
        row['place_count'] = catalog_usage(conn, kind, row['value']) if kind in CATALOGS else usage_count(conn, row['id'])
        row['child_count'] = 0 if kind in CATALOGS else conn.execute('SELECT count(*) FROM categories WHERE parent_id=?', (row['id'],)).fetchone()[0]
        row['core_protected'] = kind == 'place' and row['slug'] in CORE_SLUGS
    q = query.get('q', '').strip()[:200]
    return dict(items=[r for r in rows if q.casefold() in r['name'].casefold()], total=len(rows), q=q,
                order_states={kind: state(conn, parent, kind)}, next_orders={parent: len(rows) + 1},
                next_order=len(rows) + 1, is_catalog=kind in CATALOGS,
                taxonomy_path='/admin/classifications/' + kind, new_parent=query.get('new', ''))


class CatalogConflict(ValueError):
    pass


def save_catalog(conn, kind, form):
    """Caller holds BEGIN IMMEDIATE; keep stable value and unrelated metadata."""
    action = form.get('action')
    label = str(form.get('name', '')).strip()
    value = str(form.get('value', '')).strip()
    if action == 'new' and not value:
        value = ('focus-' + uuid.uuid4().hex[:12]) if kind == 'cafe_focus' else label
    if (action not in ('new', 'edit', 'delete') or not value or len(value) > 40 or len(label) > 40
            or any(c in value for c in '\n\r,<>|')):
        raise ValueError('분류 이름과 식별자를 확인해주세요.')
    if kind == 'cafe_focus' and not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', value):
        raise ValueError('카페 성향 식별자는 영문 소문자로 입력해주세요.')
    items = catalog(conn, kind, True)
    old = next((r for r in items if r['value'] == value), None)
    if (action == 'new' and old) or (action != 'new' and not old):
        raise CatalogConflict('중복되거나 없는 분류입니다.')
    item_id = catalog_id(kind, value)
    if action == 'delete':
        if catalog_usage(conn, kind, value):
            raise CatalogConflict('사용 중인 분류는 삭제할 수 없습니다. 비활성화를 이용해주세요.')
        index = items.index(old)
        items.remove(old)
        write_setting(conn, 'taxonomy:' + kind, items)
        compact(conn, kind, kind)
        target = catalog_id(kind, items[min(index, len(items)-1)]['value']) if items else None
        return flat_location(kind, target)
    if (not label or label in ('전체', '상관없음') or any(c in label for c in '\n\r,<>|')
            or any(i['label'] == label and i['value'] != value for i in items)):
        raise ValueError('중복되지 않는 표시 이름을 입력해주세요.')
    raw = str(form.get('sort_order', '')).strip()
    try:
        requested = int(raw) if raw else None
        if requested is not None and not 0 <= requested <= 9999:
            raise ValueError
    except ValueError:
        raise ValueError('순서는 1~9999 사이의 숫자로 입력해주세요.') from None
    item = {**(old or {}), 'value': value, 'label': label,
            'sort_order': old['sort_order'] if old else len(items)+1, 'is_active': form.get('is_active') == '1'}
    if old:
        items[items.index(old)] = item
    else:
        items.append(item)
    write_setting(conn, 'taxonomy:' + kind, items)
    position(conn, item_id, kind, requested, {**old, 'parent_id': kind} if old else None, kind)
    return flat_location(kind, item_id)
