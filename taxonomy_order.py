"""Restaurant sibling ordering, serialized writes and optimistic UI revisions."""
import hashlib
import json
import sqlite3
from contextlib import closing

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlite_migrations import verified_migration

KIND = 'restaurant_subcategory'
VERSION = '20261111_taxonomy_order'


def siblings(conn, parent_id):
    return [dict(r) for r in conn.execute(
        'SELECT * FROM categories WHERE type=? AND parent_id IS ? ORDER BY sort_order,id',
        (KIND, parent_id))]


def state(conn, parent_id):
    rows = siblings(conn, parent_id)
    version = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return dict(parent_id=parent_id, version=version,
                items=[dict(id=r['id'], sort_order=r['sort_order'], is_active=r['is_active']) for r in rows])


def renumber(conn, ids):
    conn.executemany('UPDATE categories SET sort_order=? WHERE id=? AND sort_order<>?',
                     [(i, item_id, i) for i, item_id in enumerate(ids, 1)])


def compact(conn, parent_id):
    renumber(conn, [r['id'] for r in siblings(conn, parent_id)])


def position(conn, item_id, parent_id, requested, original=None):
    """Called within the existing create/edit transaction, after validation."""
    rows = siblings(conn, parent_id)
    ids = [r['id'] for r in rows if r['id'] != item_id]
    if requested is None:
        requested = (next(i for i, r in enumerate(rows, 1) if r['id'] == item_id)
                     if original and original['parent_id'] == parent_id else len(ids) + 1)
    ids.insert(max(0, min(requested - 1, len(ids))), item_id)
    renumber(conn, ids)
    if original and original['parent_id'] != parent_id:
        compact(conn, original['parent_id'])


def migrate(database):
    # Data-only migration. Backup is verified before the first UPDATE; no schema changes.
    with verified_migration(database, VERSION, 'jejuno-before-taxonomy-reorder') as conn:
        if conn is None:
            return
        conn.row_factory = sqlite3.Row
        parents = conn.execute('SELECT DISTINCT parent_id FROM categories WHERE type=?', (KIND,)).fetchall()
        for row in parents:
            compact(conn, row[0])


class Conflict(ValueError):
    pass


def check_version(current, submitted):
    if not submitted or submitted != current['version']:
        raise Conflict('다른 작업에서 목록이 변경되었습니다. 최신 목록을 다시 불러와주세요.')


def register_routes(app, connect_db, guard, csrf):
    async def mutate(request, category_id=None):
        if response := guard(request):
            return response
        form = await request.form()
        if not csrf(request, form):
            return JSONResponse({'error': 'CSRF 검증에 실패했습니다. 화면을 새로고침해주세요.'}, status_code=400)
        try:
            with closing(connect_db()) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                if category_id is None:
                    parent_id = int(form.get('parent_id', ''))
                    parent = conn.execute('SELECT * FROM categories WHERE id=?', (parent_id,)).fetchone()
                    food = conn.execute("SELECT id FROM categories WHERE slug='food' AND type='place'").fetchone()
                    if not food or not parent or not (parent_id == food[0] or
                            (parent['type'] == KIND and parent['parent_id'] == food[0])):
                        raise ValueError('맛집 대분류 또는 같은 대분류의 세부유형만 정렬할 수 있습니다.')
                    ordered = json.loads(form.get('ordered_ids', '[]'))
                    current = state(conn, parent_id)
                    if (not isinstance(ordered, list) or not ordered or len(ordered) > 2000 or
                            any(type(i) is not int for i in ordered) or len(set(ordered)) != len(ordered) or
                            set(ordered) != {r['id'] for r in current['items']}):
                        raise ValueError('같은 상위 분류의 전체 항목을 중복 없이 보내주세요. 최신 목록을 다시 불러와주세요.')
                    check_version(current, form.get('version'))
                    renumber(conn, ordered)
                    message = '저장되었습니다.'
                else:
                    row = conn.execute('SELECT * FROM categories WHERE id=? AND type=?', (category_id, KIND)).fetchone()
                    if not row:
                        return JSONResponse({'error': '맛집 유형을 찾을 수 없습니다.'}, status_code=404)
                    parent_id = row['parent_id']
                    active = form.get('is_active')
                    if active not in ('0', '1'):
                        raise ValueError('활성 값은 ON 또는 OFF만 사용할 수 있습니다.')
                    check_version(state(conn, parent_id), form.get('version'))
                    conn.execute('UPDATE categories SET is_active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                                 (int(active), category_id))
                    message = '활성화했습니다.' if active == '1' else '비활성화했습니다.'
                result = state(conn, parent_id)
            return JSONResponse({**result, 'message': message})
        except Conflict as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return JSONResponse({'error': str(exc) or '입력값을 확인해주세요.'}, status_code=400)
        except sqlite3.Error:
            return JSONResponse({'error': '저장하지 못했습니다. 잠시 후 다시 시도해주세요.'}, status_code=503)

    @app.post('/admin/categories/reorder')
    async def reorder(request: Request):
        return await mutate(request)

    @app.post('/admin/categories/{category_id}/active')
    async def active(request: Request, category_id: int):
        return await mutate(request, category_id)
