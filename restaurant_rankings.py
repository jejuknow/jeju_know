"""Manually curated, exact-subtype rankings. No inferred ratings or automatic entrants."""
import hashlib
import json

from restaurant_taxonomy import official_terms
from public_seo import absolute_image, breadcrumbs, content_url, summary

NOTICE = '현재 제주노가 직접 비교한 장소를 기준으로 선정한 랭킹입니다.'
LIVE = 'p.deleted_at IS NULL AND p.merged_into_id IS NULL'


def subtypes(conn):
    food = conn.execute("SELECT id FROM categories WHERE type='place' AND slug='food'").fetchone()
    if not food:
        return []
    terms = official_terms(conn, food[0])
    majors = {r['id']: r for r in terms if r['parent_id'] == food[0]}
    leaves = [dict(r, parent_name=majors[r['parent_id']]['name']) for r in terms if r['parent_id'] in majors]
    return sorted(leaves, key=lambda r: (majors[r['parent_id']]['sort_order'], r['parent_id'], r['sort_order'], r['id']))


def subtype(conn, subtype_id):
    return next((r for r in subtypes(conn) if r['id'] == subtype_id), None)


def matches(conn, place_id, subtype_id):
    return bool(conn.execute("""SELECT 1 FROM places p JOIN categories c ON c.id=p.category_id
        WHERE p.id=? AND c.slug='food' AND c.type='place' AND """ + LIVE + """
        AND EXISTS(SELECT 1 FROM place_category_links l WHERE l.place_id=p.id AND l.category_id=?)""",
        (place_id, subtype_id)).fetchone())


def items(conn, ranking_id):
    return [dict(r) for r in conn.execute('SELECT * FROM restaurant_ranking_items WHERE ranking_id=? ORDER BY sort_order,id', (ranking_id,))]


def order_state(conn, ranking):
    rows = items(conn, ranking['id'])
    version = hashlib.sha256(json.dumps([dict(ranking), rows], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return dict(parent_id=ranking['id'], version=version,
                items=[dict(id=r['id'],sort_order=r['sort_order']) for r in rows])


def renumber(conn, ranking_id, ordered_ids=None):
    ids = ordered_ids if ordered_ids is not None else [r['id'] for r in items(conn, ranking_id)]
    for n, item_id in enumerate(ids, 1):
        conn.execute('UPDATE restaurant_ranking_items SET sort_order=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND ranking_id=?', (n,item_id,ranking_id))


def publication_error(conn, ranking):
    if not subtype(conn, ranking['subtype_taxonomy_id']):
        return '활성 상태인 맛집 세부유형만 공개할 수 있습니다.'
    rows = items(conn, ranking['id'])
    if not rows:
        return '랭킹에 장소를 1곳 이상 추가한 뒤 공개해주세요.'
    if any(not matches(conn, r['place_id'], ranking['subtype_taxonomy_id']) for r in rows):
        return '삭제·병합되었거나 세부유형이 달라진 장소가 있습니다. 해당 항목을 확인한 뒤 공개해주세요.'
    if not any(conn.execute('SELECT published FROM places WHERE id=?', (r['place_id'],)).fetchone()[0] for r in rows):
        return '공개 가능한 장소가 최소 1곳 필요합니다. 비공개 장소는 방문자에게 표시하지 않습니다.'
    return None


def public_items(conn, ranking):
    # Filter at the SQL boundary: hidden names/images/reasons never reach public HTML or SEO.
    return [dict(r) for r in conn.execute("""SELECT p.*,i.id AS ranking_item_id,i.reason AS ranking_reason,
        i.sort_order AS editorial_order FROM restaurant_ranking_items i JOIN places p ON p.id=i.place_id
        JOIN categories c ON c.id=p.category_id WHERE i.ranking_id=? AND p.published=1 AND """ + LIVE + """
        AND c.slug='food' AND c.type='place'
        AND EXISTS(SELECT 1 FROM place_category_links l WHERE l.place_id=p.id AND l.category_id=?)
        ORDER BY i.sort_order,i.id""", (ranking['id'],ranking['subtype_taxonomy_id']))]


def public_rankings(conn):
    active = {r['id']: r for r in subtypes(conn)}
    result = []
    for row in conn.execute('SELECT * FROM restaurant_rankings WHERE is_public=1 AND deleted_at IS NULL ORDER BY id'):
        if row['subtype_taxonomy_id'] not in active:
            continue
        ranking = dict(row, subtype_name=active[row['subtype_taxonomy_id']]['name'])
        visible = public_items(conn, ranking)
        if visible:
            ranking.update(visible_items=visible, place_count=len(visible))
            result.append(ranking)
    return result


def metadata(ranking, places):
    url = content_url('/best/ranking/', ranking['slug'])
    description = summary(f"JEJU_KNOW가 직접 비교해 고른 제주 {ranking['subtype_name']} 맛집 순위를 확인하세요. " + NOTICE)
    return dict(title=ranking['title']+' | JEJU_KNOW 제주노', description=description,
                canonical=url, image=absolute_image(places[0].get('image_url') if places else ''),
                share_title=ranking['title']+' | JEJU_KNOW', share_text=description,
                robots='index,follow', json_ld=None,
                breadcrumbs=breadcrumbs('BEST','/best',ranking['title'],url))
