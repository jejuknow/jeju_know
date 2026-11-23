"""Admin-only BEST discovery and atomic, optimistic collection editing.

No migration: existing memberships and their relative order remain authoritative.
"""
import hashlib
import json
import math
import sqlite3
from contextlib import closing

from fastapi import Request
from fastapi.responses import JSONResponse

from admin_roles import audit
from admin_settings import catalog
from restaurant_taxonomy import official_terms
from taxonomy_order import Conflict, check_version

PAGE_SIZE = 20
ACTIVE = "p.deleted_at IS NULL AND p.merged_into_id IS NULL"


def filters(conn):
    categories = [dict(id=r['id'], name=r['name'], slug=r['slug']) for r in conn.execute(
        "SELECT id,name,slug FROM categories WHERE type='place' AND is_active=1 ORDER BY sort_order,id")]
    terms = [dict(id=r['id'], name=r['name'], parent_id=r['parent_id'], category_id=c['id'])
             for c in categories for r in official_terms(conn, c['id'])]
    return dict(categories=categories, terms=terms,
                regions=[dict(value=r['value'], label=r['label']) for r in catalog(conn, 'region')])


def membership(conn, collection_id):
    rows = [tuple(r) for r in conn.execute(
        'SELECT place_id,sort_order FROM best_collection_places WHERE collection_id=? ORDER BY sort_order,place_id',
        (collection_id,))]
    return dict(ids=[r[0] for r in rows],
                version=hashlib.sha256(json.dumps(rows).encode()).hexdigest())


def summaries(conn, rows, image_store):
    rows = [dict(r) for r in rows]
    if not rows:
        return []
    slots = ','.join('?' for _ in rows)
    labels = {}
    for r in conn.execute(f'''SELECT l.place_id,c.name FROM place_category_links l
            JOIN categories c ON c.id=l.category_id WHERE l.place_id IN ({slots})
            ORDER BY c.sort_order,c.id''', [r['id'] for r in rows]):
        labels.setdefault(r['place_id'], []).append(r['name'])
    region_labels = {r['value']: r['label'] for r in catalog(conn, 'region', True)}
    for row in rows:
        row['summary'] = ' · '.join(labels.get(row['id'], []))
        row['region'] = region_labels.get(row['region'], row['region'])
        # Use the existing primary / reference / merged-photo fallback rules.
        row['thumbnail'] = image_store.decorate(dict(id=row['id'], image_url=row.pop('image_url')), 'places')['image_url']
    return rows


SELECT = '''SELECT p.id,p.name,p.region,p.category_id,COALESCE(c.name,'분류 없음') AS category,
    COALESCE(c.slug,'') AS category_slug,p.image_url,
    CASE WHEN p.published=1 AND COALESCE(p.is_example,0)=0
      AND p.deleted_at IS NULL AND p.merged_into_id IS NULL THEN 1 ELSE 0 END AS is_public,
    CASE WHEN p.deleted_at IS NOT NULL OR p.merged_into_id IS NOT NULL THEN 1 ELSE 0 END AS unavailable
    FROM places p LEFT JOIN categories c ON c.id=p.category_id'''


def editor_state(conn, collection_id, image_store):
    state = membership(conn, collection_id)
    rows = conn.execute(SELECT + ''' JOIN best_collection_places b ON b.place_id=p.id
        WHERE b.collection_id=? ORDER BY b.sort_order,p.id''', (collection_id,)).fetchall()
    return dict(version=state['version'], items=summaries(conn, rows, image_store))


def search(conn, collection_id, params, image_store):
    options = filters(conn)
    where, args = [ACTIVE], []
    category = str(params.get('category', ''))
    root = next((c for c in options['categories'] if str(c['id']) == category), None)
    if category and not root:
        raise ValueError('활성 카테고리를 선택해주세요.')
    if root:
        where.append('p.category_id=?'); args.append(root['id'])
    region = str(params.get('region', ''))
    if region:
        if region not in {r['value'] for r in options['regions']}:
            raise ValueError('활성 지역을 선택해주세요.')
        where.append('p.region=?'); args.append(region)
    major, term = str(params.get('major', '')), str(params.get('term', ''))
    choices = [r for r in options['terms'] if root and r['category_id'] == root['id']]
    if major:
        parent = next((r for r in choices if str(r['id']) == major and r['parent_id'] == root['id']), None)
        if not parent:
            raise ValueError('활성 대분류를 선택해주세요.')
    if term:
        selected = next((r for r in choices if str(r['id']) == term), None)
        if not selected or (major and str(selected['parent_id']) != major):
            raise ValueError('선택한 분류의 세부 조건을 확인해주세요.')
        ids = [selected['id']]
    elif major:
        ids = [parent['id']] + [r['id'] for r in choices if r['parent_id'] == parent['id']]
    else:
        ids = []
    if ids:
        where.append('EXISTS(SELECT 1 FROM place_category_links l WHERE l.place_id=p.id AND l.category_id IN (' + ','.join('?' for _ in ids) + '))')
        args.extend(ids)
    query = str(params.get('q', '')).strip()
    if len(query) > 120:
        raise ValueError('검색어는 120자 이내로 입력해주세요.')
    for word in query.split()[:8]:
        where.append("""(instr(lower(p.name || ' ' || p.region || ' ' || COALESCE(c.name,'')),lower(?))>0
            OR EXISTS(SELECT 1 FROM place_category_links l JOIN categories t ON t.id=l.category_id
                WHERE l.place_id=p.id AND instr(lower(t.name),lower(?))>0))""")
        args.extend((word, word))
    clause = ' WHERE ' + ' AND '.join(where)
    total = conn.execute('SELECT count(*) FROM places p LEFT JOIN categories c ON c.id=p.category_id' + clause, args).fetchone()[0]
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(pages, int(params.get('page', 1))))
    rows = conn.execute(SELECT + clause + ' ORDER BY p.name,p.id LIMIT ? OFFSET ?', (*args, PAGE_SIZE, (page-1)*PAGE_SIZE)).fetchall()
    items = summaries(conn, rows, image_store)
    current = membership(conn, collection_id)
    included = set(current['ids'])
    for item in items:
        item['included'] = item['id'] in included
    return dict(items=items, total=total, page=page, pages=pages, page_size=PAGE_SIZE, version=current['version'])


def change(conn, request, collection_id, action, ids, version=None):
    """Caller holds BEGIN IMMEDIATE; validate the entire batch before any write."""
    if not conn.execute('SELECT 1 FROM best_collections WHERE id=? AND deleted_at IS NULL', (collection_id,)).fetchone():
        raise LookupError('모음을 찾을 수 없습니다.')
    current = membership(conn, collection_id)
    if version is not None:
        check_version(current, version)
    if (not isinstance(ids, list) or len(ids) > 2000 or any(type(i) is not int or i < 1 for i in ids)
            or len(ids) != len(set(ids))):
        raise ValueError('장소를 중복 없이 선택해주세요.')
    ordered = current['ids'][:]
    if action == 'add':
        if not ids or len(ids) > 100:
            raise ValueError('한 번에 1~100곳을 선택해주세요.')
        if set(ids) & set(ordered):
            raise Conflict('이미 모음에 포함된 장소가 있습니다. 최신 목록을 다시 불러와주세요.')
        count = conn.execute('SELECT count(*) FROM places p WHERE ' + ACTIVE + ' AND p.id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchone()[0]
        if count != len(ids):
            raise ValueError('삭제되거나 병합된 장소는 추가할 수 없습니다. 선택한 장소를 확인해주세요.')
        ordered.extend(ids)
        conn.executemany('INSERT INTO best_collection_places(collection_id,place_id,sort_order) VALUES (?,?,?)',
                         [(collection_id, pid, pos) for pos, pid in enumerate(ordered, 1) if pid in ids])
    elif action == 'remove':
        if len(ids) != 1 or ids[0] not in ordered:
            raise ValueError('모음에 포함된 장소를 선택해주세요.')
        conn.execute('DELETE FROM best_collection_places WHERE collection_id=? AND place_id=?', (collection_id, ids[0]))
        ordered.remove(ids[0])
    elif action == 'reorder':
        if set(ids) != set(ordered):
            raise ValueError('모음의 전체 장소를 중복 없이 보내주세요.')
        ordered = ids
    else:
        raise ValueError('지원하지 않는 작업입니다.')
    conn.executemany('UPDATE best_collection_places SET sort_order=? WHERE collection_id=? AND place_id=?',
                     [(pos, collection_id, pid) for pos, pid in enumerate(ordered, 1)])
    conn.execute('UPDATE best_collections SET updated_at=CURRENT_TIMESTAMP WHERE id=?', (collection_id,))
    audit(conn, request, {'add': 'BEST 장소 추가', 'remove': 'BEST 장소 제외', 'reorder': 'BEST 순서 변경'}[action], 'BEST', collection_id)


def register_routes(app, connect_db, guard, csrf, image_store):
    @app.get('/admin/best/{collection_id}/places/search')
    async def search_places(request: Request, collection_id: int):
        if response := guard(request): return response
        try:
            with closing(connect_db()) as conn:
                if not conn.execute('SELECT 1 FROM best_collections WHERE id=? AND deleted_at IS NULL', (collection_id,)).fetchone():
                    return JSONResponse({'error': '모음을 찾을 수 없습니다.'}, status_code=404)
                result = search(conn, collection_id, request.query_params, image_store)
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except (ValueError, TypeError):
            return JSONResponse({'error': '검색어와 활성 분류·지역을 확인해주세요.'}, status_code=400)

    @app.post('/admin/best/{collection_id}/places/manage')
    async def manage_places(request: Request, collection_id: int):
        if response := guard(request): return response
        form = await request.form(max_fields=6, max_part_size=32768)
        if not csrf(request, form):
            return JSONResponse({'error': 'CSRF 검증에 실패했습니다. 화면을 새로고침해주세요.'}, status_code=400)
        try:
            with closing(connect_db()) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                change(conn, request, collection_id, form.get('action'), json.loads(form.get('ids', 'null')), form.get('version', ''))
                result = editor_state(conn, collection_id, image_store)
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except Conflict as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)
        except LookupError as exc:
            return JSONResponse({'error': str(exc)}, status_code=404)
        except (ValueError, TypeError):
            return JSONResponse({'error': '선택한 장소를 확인해주세요. 중복·삭제된 장소나 불완전한 순서는 저장할 수 없습니다.'}, status_code=400)
        except sqlite3.Error:
            return JSONResponse({'error': '저장하지 못했습니다. 잠시 후 다시 시도해주세요.'}, status_code=503)
