"""Read-only operational CSV adapter; all relations are loaded in bounded queries."""
import json
from collections import defaultdict

from admin_settings import catalog
from community_migrations import today
from csv_place_import import CHOICES, DETAILS, EXPORT_HEADERS, KINDS, TEXT, export_csv
from restaurant_taxonomy import official_terms
from submission_fields import controls


def place_rows(conn, kind, *, on_date=None):
    """Yield import-compatible rows in stable place order, without user/account data."""
    on_date = on_date or today()
    category = conn.execute("SELECT id FROM categories WHERE type='place' AND slug=?", (KINDS[kind][1],)).fetchone()
    if not category:
        return
    category_id = category['id']
    places = conn.execute('''SELECT * FROM places WHERE category_id=? AND deleted_at IS NULL
                             AND merged_into_id IS NULL ORDER BY id''', (category_id,)).fetchall()
    terms = official_terms(conn, category_id)
    active = {t['id'] for t in terms}
    linked = defaultdict(set)
    for row in conn.execute('''SELECT l.place_id,l.category_id FROM place_category_links l
            JOIN places p ON p.id=l.place_id WHERE p.category_id=?
            AND p.deleted_at IS NULL AND p.merged_into_id IS NULL''', (category_id,)):
        linked[row['place_id']].add(row['category_id'])
    picks = {r['place_id']: dict(r) for r in conn.execute('''SELECT place_id,year,month,is_active
             FROM place_pick_periods WHERE year=? AND month=?''', (on_date.year, on_date.month))}
    regions = {r['value']: r['label'] for r in catalog(conn, 'region')}
    companions = {r['value']: r['label'] for r in catalog(conn, 'companion')}
    for saved in places:
        p = dict(saved)
        row = {h: p.get(field) for h, (field, _) in TEXT.items()}
        row.update({h: p.get(field) for h, field in DETAILS.items() if h in EXPORT_HEADERS[kind]})
        for h, (field, choices) in CHOICES.items():
            row[h] = next((label for label, value in choices.items() if value == p.get(field)),
                          '미확인' if '미확인' in choices else '')
        row['탐나는전'] = p.get('tamnaneunjeon_status') or '미확인'
        if kind == 'cafe':
            row['커피맛_특징'] = p.get('coffee_taste') or '미확인'
        row.update({'지역': regions.get(p.get('region'), p.get('region') or ''),
                    '위도': p.get('latitude'), '경도': p.get('longitude'),
                    '오픈연도': p.get('opened_year'), '오픈월': p.get('opened_month'),
                    '잘맞는동행': '|'.join(companions.get(v, v) for v in json.loads(p.get('companions') or '[]'))})
        ui = controls(p)
        if ui['hours_same']:
            row.update({'영업시간_상태': '미확인' if ui['hours_unknown'] else '시간 입력',
                        '영업_시작': ui['hours_start'], '영업_종료': ui['hours_end']})
        else:
            row['영업시간_원문'] = p.get('business_hours') or ''
        row['휴무일'] = ('|'.join(ui['closed_weekdays']) if ui['closed_mode'] == 'days'
                       else p.get('closed_days') or '미확인')
        selected = linked[p['id']]
        if kind == 'cafe':
            row['카페_특징'] = '|'.join(t['name'] for t in terms if t['id'] in selected)
        else:
            # Never guess how to replace an ambiguous/retired classification.
            # Blank cells preserve that original set on explicit update.
            branches = selected | {t['parent_id'] for t in terms if t['id'] in selected}
            parents = [t for t in terms if t['parent_id'] == category_id and t['id'] in branches]
            if len(parents) == 1 and selected <= active:
                children = [t for t in terms if t['parent_id'] == parents[0]['id'] and t['id'] in selected]
                if len(children) <= 3:
                    row['맛집_대분류'] = parents[0]['name']
                    row['맛집_세부유형'] = '|'.join(t['name'] for t in children)
        if pick := picks.get(p['id']):
            row.update({'JEJU_KNOW_PICK': '예' if pick['is_active'] else '아니오',
                        'PICK_연도': pick['year'], 'PICK_월': pick['month']})
        yield row


def export_places(conn, kind, *, on_date=None):
    return export_csv(EXPORT_HEADERS[kind], place_rows(conn, kind, on_date=on_date))
