"""Read-only restaurant administration tree and local return locations."""
from urllib.parse import parse_qs, urlencode, urlsplit

PATH = '/admin/classifications/restaurant_subcategory'


def usage_counts(conn):
    """Same descendant semantics as usage_count, in one query for every type.

    UNION deduplicates a place linked both directly and through several subtypes.
    Include private/deleted places, just as the existing deletion guard does.
    """
    return dict(conn.execute("""
        WITH RECURSIVE tree(root, id) AS (
            SELECT id,id FROM categories WHERE type='restaurant_subcategory'
            UNION SELECT tree.root,c.id FROM categories c JOIN tree ON c.parent_id=tree.id
        ), uses(root, place_id) AS (
            SELECT t.root,p.id FROM tree t JOIN places p ON p.category_id=t.id
            UNION SELECT t.root,l.place_id FROM tree t
                JOIN place_category_links l ON l.category_id=t.id
                JOIN places p ON p.id=l.place_id
        ) SELECT root,count(*) FROM uses GROUP BY root
    """))


def view(conn, rows, query):
    counts = usage_counts(conn)
    food = next((r for r in rows if r['slug'] == 'food'), None)
    food_id = food['id'] if food else None
    items = [dict(r, place_count=counts.get(r['id'], 0),
                  child_count=sum(c['parent_id'] == r['id'] for c in rows))
             for r in rows if r['type'] == 'restaurant_subcategory']
    by_id = {r['id']: r for r in items}
    # Preserve even historical/unattached values: never silently drop a DB row.
    roots = [r for r in items if r['parent_id'] == food_id or r['parent_id'] not in by_id]
    q = query.get('q', '').strip()[:200]
    opened = set(query.get('open', '').split(','))
    groups = []
    for parent in roots:
        children = [r for r in items if r['parent_id'] == parent['id']]
        matches = children if q.casefold() in parent['name'].casefold() else [
            r for r in children if q.casefold() in r['name'].casefold()]
        if q and q.casefold() not in parent['name'].casefold() and not matches:
            continue
        groups.append(dict(parent=parent, children=matches, total=len(children),
                           can_add=parent['parent_id'] == food_id and not parent['is_legacy'],
                           opened=(bool(q) and 'open' not in query) or str(parent['id']) in opened))
    if groups and not q and 'open' not in query:
        groups[0]['opened'] = True
    return dict(groups=groups, total=len(items), group_count=len(roots), q=q,
                food_id=food_id, restaurant_parents=([food] if food else []) + [
                    r for r in roots if r['parent_id'] == food_id and not r['is_legacy']],
                new_parent=query.get('new', ''), taxonomy_path=PATH)


def saved_location(next_url, item_id, parent_id, food_id):
    """Use validated saved IDs, not a client-supplied parent, for return anchors."""
    parts = urlsplit(next_url)
    if parts.path != PATH and not (parts.path == '/admin/categories' and
                                  parse_qs(parts.query).get('type') == ['restaurant_subcategory']):
        return next_url
    group_id = item_id if parent_id == food_id else parent_id
    opened = parse_qs(parts.query, keep_blank_values=True).get('open', [''])[0].split(',')
    opened = list(dict.fromkeys([v for v in opened if v.isdecimal()][:200] + [str(group_id)]))
    # A renamed/new item must stay visible even if it no longer matches the old search.
    return PATH + '?' + urlencode({'open': ','.join(opened)}) + f'#taxonomy-{item_id}'
