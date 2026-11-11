"""Category slug policy; existing identifiers are never regenerated on name edits."""
import re
import uuid

from restaurant_migrations import TREE


def valid_slug(value):
    return bool(len(value) <= 80 and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', value))


def automatic_slug(conn, name, parent_id, kind='restaurant_subcategory'):
    """Reuse the established semantic map, otherwise issue an opaque stable token.

    Called inside the category write transaction (BEGIN IMMEDIATE), so selection
    and insertion are serialized; the existing UNIQUE constraint is the final guard.
    """
    parent = conn.execute('SELECT slug FROM categories WHERE id=?', (parent_id,)).fetchone()
    parent_slug = parent['slug'] if parent else ''
    choices = [(key, label) for key, label, _ in TREE] if parent_slug == 'food' else [
        choice for key, _, children in TREE if parent_slug == 'food-' + key for choice in children]
    key = next((key for key, label in choices if label == name), None)
    prefix = {'place': 'place-', 'cafe_feature': 'cafe-feature-'}.get(kind, 'food-type-')
    base = 'food-' + key if key and kind == 'restaurant_subcategory' else prefix + uuid.uuid4().hex[:12]
    # Same suffix convention as the existing place/news slug helpers, scoped to
    # categories rather than their content tables, and retaining ASCII-only slugs.
    candidate, suffix = base, 2
    while conn.execute('SELECT 1 FROM categories WHERE slug=? COLLATE NOCASE', (candidate,)).fetchone():
        candidate = f'{base}-{suffix}'
        suffix += 1
    return candidate


def category_slug(conn, form, name, parent_id, original=None, kind='restaurant_subcategory'):
    if original is not None and form.get('slug_edit') != '1':
        return original['slug']
    submitted = str(form.get('slug', '')).strip().lower()
    if original is None and not submitted:
        return automatic_slug(conn, name, parent_id, kind)
    if not valid_slug(submitted):
        raise ValueError('슬러그는 영문 소문자·숫자·하이픈으로 1~80자 입력해주세요.')
    if original is not None and submitted != original['slug']:
        from taxonomy_order import CORE_SLUGS
        if original['type'] == 'place' and original['slug'] in CORE_SLUGS:
            raise ValueError('서비스 핵심 카테고리의 슬러그는 변경할 수 없습니다.')
        if form.get('slug_change_confirm') != '1':
            raise ValueError('슬러그 변경이 기존 필터·URL·CSV 연동에 미치는 영향을 확인해주세요.')
        if form.get('original_slug') != original['slug']:
            raise ValueError('다른 작업에서 슬러그가 변경되었습니다. 화면을 새로고침한 뒤 다시 확인해주세요.')
    if conn.execute('SELECT 1 FROM categories WHERE slug=? COLLATE NOCASE AND id<>?',
                    (submitted, original['id'] if original is not None else -1)).fetchone():
        raise ValueError('이미 사용 중인 슬러그입니다. 다른 값을 입력하거나 신규 등록의 슬러그를 비워 자동 생성해주세요.')
    return submitted


def restaurant_slug(conn, form, name, parent_id, original=None):
    return category_slug(conn, form, name, parent_id, original)
