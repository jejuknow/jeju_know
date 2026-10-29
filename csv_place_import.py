"""Strict CSV input adapters for the existing unified place/taxonomy model."""
import csv
import hashlib
import io
import json
import re
import uuid
from contextlib import closing
from pathlib import Path

from admin_settings import catalog
from auth_security import safe_external_url
from community_places import coordinates, duplicates, opening
from community_migrations import today
from csv_import_migrations import backup
from place_details import COFFEE_OPTIONS, USE_OPTIONS, validate_detail_value
from restaurant_taxonomy import official_terms, form_selection, parse_selection
from submission_fields import normalize_controls
from business_hours import csv_business_hours
from starlette.datastructures import FormData

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 500
KINDS = {'cafe': ('카페', 'cafe'), 'restaurant': ('맛집', 'food')}
BEFORE = 'place_id,장소명,지역,주소,위도,경도,영업시간_상태,영업_시작,영업_종료,휴무일,전화번호,대표메뉴,대표메뉴_가격,추천메뉴,탐나는전,오픈연도,오픈월,꿀팁,잘맞는동행'.split(',')
AFTER = '주차,반려동물,아이동반,무장애,확인상태,공개여부,JEJU_KNOW_PICK,PICK_연도,PICK_월,NEW_노출,대표사진,추가사진1,추가사진2,추가사진3,홈페이지_인스타그램,지도링크,비고'.split(',')
HEADERS = {kind: BEFORE + fields + AFTER for kind, fields in (
    ('cafe', ['카페_특징', '커피맛_특징']), ('restaurant', ['맛집_대분류', '맛집_세부유형', '음식맛_특징']))}
TEXT = {'장소명': ('name', 150), '주소': ('address', 500), '꿀팁': ('note', 4000),
        '비고': ('admin_import_note', 2000), '홈페이지_인스타그램': ('website_url', 2000), '지도링크': ('map_url', 2000)}
DETAILS = {'전화번호': 'phone', '대표메뉴': 'signature_menu', '대표메뉴_가격': 'representative_price',
           '추천메뉴': 'recommended_menu', '탐나는전': 'tamnaneunjeon_status',
           '커피맛_특징': 'coffee_taste', '음식맛_특징': 'food_taste_note'}
CHOICES = {'주차': ('parking_status', {x: x for x in USE_OPTIONS}),
           '반려동물': ('pet_status', {x: x for x in USE_OPTIONS}),
           '아이동반': ('child_status', {x: x for x in USE_OPTIONS}),
           '무장애': ('accessible_status', {x: x for x in USE_OPTIONS}),
           '확인상태': ('verification_status', {'미확인': 'unverified', 'JEJU_KNOW 확인': 'jejuno_verified'}),
           '공개여부': ('published', {'공개': 1, '비공개': 0}),
           'NEW_노출': ('new_override', {'자동': 'auto', '노출': 'show', '숨김': 'hide'})}
PHOTO_FIELDS = ['대표사진', '추가사진1', '추가사진2', '추가사진3']


def export_csv(headers, rows):
    def safe(value):
        text = str(value if value is not None else '')
        return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([safe(row.get(key, '')) for key in headers])
    return output.getvalue().encode('utf-8-sig')


def template_examples(kind):
    return [{'장소명': f'[예시] {KINDS[kind][0]} (실제 정보로 변경)',
             '영업시간_상태': status, '영업_시작': start, '영업_종료': end}
            for status, start, end in [('시간 입력', '09:30', '16:00'), ('미확인', '', '')]]


def parse_csv(raw, filename, mime, kind):
    if Path(filename or '').suffix.lower() != '.csv':
        raise ValueError('CSV 파일만 업로드해주세요.')
    if mime not in ('text/csv', 'application/csv', 'application/vnd.ms-excel', 'text/plain', 'application/octet-stream', ''):
        raise ValueError('CSV 파일 형식(MIME)을 확인해주세요.')
    if len(raw) > MAX_BYTES:
        raise ValueError('파일은 5MB 이하로 업로드해주세요.')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('UTF-8 인코딩이 아닙니다. Excel에서 CSV UTF-8로 저장해주세요.') from None
    if '\x00' in text:
        raise ValueError('CSV에 허용되지 않는 제어 문자가 있습니다.')
    try:
        reader = csv.reader(io.StringIO(text, newline=''), strict=True)
        header = next(reader, [])
        other = 'restaurant' if kind == 'cafe' else 'cafe'
        if set(HEADERS[other]) - set(HEADERS[kind]) & set(header):
            raise ValueError(f'{KINDS[other][0]} CSV 파일로 보입니다. {KINDS[other][0]} CSV 등록 메뉴에서 업로드해주세요.')
        allowed_headers = (set(HEADERS[kind]), set(HEADERS[kind]) - {'영업시간_상태'})
        if len(set(header)) != len(header) or set(header) not in allowed_headers:
            missing = ', '.join(x for x in HEADERS[kind] if x not in header)
            extra = ', '.join(x for x in header if x not in HEADERS[kind])
            raise ValueError('템플릿 헤더를 확인해주세요. ' + ('누락: ' + missing if missing else '') + (' 추가/중복: ' + (extra or '중복 열') if extra or len(set(header)) != len(header) else ''))
        rows = []
        for values in reader:
            if not any(v.strip() for v in values):
                continue
            if len(values) != len(header):
                raise ValueError(f'{reader.line_num}행: 열 개수가 템플릿과 다릅니다.')
            if any(len(v) > 4000 or any(ord(c) < 32 and c not in '\r\n\t' for c in v) for v in values):
                raise ValueError(f'{reader.line_num}행: 셀은 4,000자 이하이고 허용된 문자여야 합니다.')
            rows.append({'line': reader.line_num, 'raw': {**dict.fromkeys(HEADERS[kind], ''), **dict(zip(header, (v.strip() for v in values)))}})
            if len(rows) > MAX_ROWS:
                raise ValueError('한 번에 최대 500개 장소를 등록할 수 있습니다.')
    except csv.Error:
        raise ValueError('CSV 구문이 올바르지 않습니다. 따옴표와 구분자를 확인해주세요.') from None
    if not rows:
        raise ValueError('등록할 데이터 행이 없습니다.')
    return rows


def split_values(value):
    values = [v.strip() for v in value.split('|')]
    if not all(values):
        raise ValueError('복수값의 | 앞뒤에 빈 항목이 있습니다. 선택값을 확인해주세요.')
    return list(dict.fromkeys(values))


def exact_catalog(conn, kind, text):
    matches = {r['value'] for r in catalog(conn, kind) if text in (r['label'], r['value'])}
    if len(matches) != 1:
        raise ValueError(f"{'지역' if kind == 'region' else '동행'} '{text}'은 활성 선택 목록에 없습니다.")
    return matches.pop()


def validate_cafe_row(conn, raw, category_id, previous):
    if not raw['카페_특징']:
        return None
    terms = official_terms(conn, category_id)
    ids = []
    for name in split_values(raw['카페_특징']):
        matches = [r['id'] for r in terms if r['name'] == name or name in json.loads(r['legacy_aliases'])]
        if len(matches) != 1:
            raise ValueError(f"카페 특징 '{name}'은 활성 분류에 없습니다.")
        ids.extend(value for value in matches if value not in ids)
    # Replace only visible cafe features, preserving legacy/other linked terms.
    active = {r['id'] for r in terms}
    return ids + [r[0] for r in conn.execute('SELECT category_id FROM place_category_links WHERE place_id=?', (previous.get('id', 0),)) if r[0] not in active]


def validate_restaurant_row(conn, raw, category_id, previous):
    if not raw['맛집_대분류'] and not raw['맛집_세부유형'] and previous:
        return None
    rows = official_terms(conn, category_id)
    saved = form_selection(conn, previous['id'], category_id) if previous else {}
    parents = [r for r in rows if r['parent_id'] == category_id and
               (r['name'] == raw['맛집_대분류'] if raw['맛집_대분류'] else str(r['id']) == saved.get('restaurant_main_id'))]
    if len(parents) != 1:
        raise ValueError('맛집 대분류를 활성 목록과 정확히 일치하게 입력해주세요.')
    parent = parents[0]
    children = [r for r in rows if r['name'] == raw['맛집_세부유형'] and r['parent_id'] == parent['id']]
    if raw['맛집_세부유형'] and len(children) != 1:
        raise ValueError(f"맛집 대분류 '{parent['name']}'과 세부유형 '{raw['맛집_세부유형']}'의 관계가 올바르지 않습니다.")
    child = str(children[0]['id']) if children else saved.get('restaurant_subtype_id', '')
    if child and not any(str(r['id']) == child and r['parent_id'] == parent['id'] for r in rows):
        raise ValueError('기존 세부유형과 다른 대분류입니다. 세부유형도 함께 입력하거나 수동 수정해주세요.')
    return parse_selection(conn, {'restaurant_taxonomy_present': '1', 'restaurant_main_id': str(parent['id']),
                                 'restaurant_subtype_id': child}, category_id, previous or None, required=True)


def image_reference(conn, value, root):
    if value.startswith('/uploads/'):
        relative = value.removeprefix('/uploads/')
        if not re.fullmatch(r'(places|issues)/[0-9a-f]{32}\.webp', relative) or not (root / relative).is_file():
            raise ValueError('이미지 경로가 현재 업로드 저장소에 없습니다.')
        row = conn.execute('SELECT * FROM images WHERE file_path=? LIMIT 1', (relative,)).fetchone()
        if not row:
            raise ValueError('등록된 이미지 경로만 참조할 수 있습니다.')
        return dict(row)
    if not safe_external_url(value) or not value:
        raise ValueError('사진은 기존 /uploads/ 경로 또는 올바른 http/https URL로 입력해주세요.')
    return None


def normalize_common_fields(conn, raw, kind, previous, root):
    category = conn.execute("SELECT * FROM categories WHERE type='place' AND slug=? AND is_active=1", (KINDS[kind][1],)).fetchone()
    if not category or (previous and previous['category_id'] != category['id']):
        raise ValueError('활성 장소 카테고리와 기존 place_id의 종류가 일치하지 않습니다.')
    patch, warnings = {}, []
    for title, (field, limit) in TEXT.items():
        if raw[title]:
            if len(raw[title]) > limit:
                raise ValueError(f'{title}은 {limit}자 이내로 입력해주세요.')
            patch[field] = raw[title]
    for field in ('map_url', 'website_url'):
        if field in patch and not safe_external_url(patch[field]):
            raise ValueError('홈페이지·지도 링크는 올바른 http/https URL이어야 합니다.')
    for title, field in DETAILS.items():
        if raw.get(title):
            value = raw[title].replace('|', '\n') if field in ('signature_menu', 'recommended_menu') else raw[title]
            patch[field] = validate_detail_value(field, value)
    for title, (field, choices) in CHOICES.items():
        if raw[title]:
            if raw[title] not in choices:
                raise ValueError(f'{title}: ' + ' / '.join(choices) + ' 중 선택해주세요.')
            patch[field] = choices[raw[title]]
    if raw['지역']:
        patch['region'] = exact_catalog(conn, 'region', raw['지역'])
    if raw['잘맞는동행']:
        patch['companions'] = json.dumps([exact_catalog(conn, 'companion', v) for v in split_values(raw['잘맞는동행'])], ensure_ascii=False)
    hours = csv_business_hours(raw, previous)
    if hours is not None:
        patch['business_hours'] = hours
    if raw['휴무일']:
        mode = {'미확인': 'unknown', '연중무휴': 'everyday'}.get(raw['휴무일'], 'days')
        pairs = [('closed_present', '1'), ('closed_mode', mode)] + [('closed_weekdays', v) for v in split_values(raw['휴무일'])]
        patch['closed_days'] = normalize_controls(FormData(pairs))['closed_days']
    if raw['오픈연도'] or raw['오픈월']:
        patch['opened_year'], patch['opened_month'] = opening(raw['오픈연도'] or previous.get('opened_year'), raw['오픈월'] or previous.get('opened_month'))
    if raw['위도'] or raw['경도']:
        patch['latitude'], patch['longitude'] = coordinates(raw['위도'], raw['경도'])
    data = {'published': 0, 'new_override': 'auto', 'verification_status': 'unverified',
            'category': category['name'], 'category_id': category['id'], 'emoji': '☕' if kind == 'cafe' else '🍚', **previous, **patch}
    for field, title in (('name', '장소명'), ('region', '지역'), ('address', '주소')):
        if not data.get(field):
            raise ValueError(f"필수값 '{title}'가 없습니다.")
    if data.get('latitude') is None or data.get('longitude') is None:
        warnings.append('지도 위치 미등록: 주소 검색을 시도하거나 좌표 없이 등록할 수 있습니다.')
    elif raw['주소'] and previous and raw['주소'] != previous['address'] and not raw['위도']:
        warnings.append('주소가 변경되었지만 기존 좌표를 유지합니다. 위치를 확인해주세요.')
    if data.get('new_override') == 'auto' and not data.get('opened_month'):
        warnings.append('오픈월이 없어 NEW 자동 노출 대상이 아닙니다.')
    pick = None
    if raw['JEJU_KNOW_PICK'] or raw['PICK_연도'] or raw['PICK_월']:
        if raw['JEJU_KNOW_PICK'] not in ('예', '아니오'):
            raise ValueError('JEJU_KNOW_PICK은 예 / 아니오 중 선택해주세요.')
        if raw['JEJU_KNOW_PICK'] == '예' and not (raw['PICK_연도'] and raw['PICK_월']):
            raise ValueError('PICK을 선택하면 PICK_연도와 PICK_월을 입력해주세요.')
        try:
            year, month = int(raw['PICK_연도'] or today().year), int(raw['PICK_월'] or today().month)
            if not 1900 <= year <= 9999 or not 1 <= month <= 12:
                raise ValueError
        except ValueError:
            raise ValueError('PICK 연도·월을 확인해주세요.') from None
        pick = (year, month, int(raw['JEJU_KNOW_PICK'] == '예'))
    photos = []
    for slot, title in enumerate(PHOTO_FIELDS):
        if not raw[title]:
            continue
        metadata = image_reference(conn, raw[title], root)
        current = conn.execute('SELECT file_path FROM images WHERE place_id=? AND sort_order=?', (previous.get('id', 0),slot)).fetchone()
        ref = conn.execute('SELECT url FROM place_image_references WHERE place_id=? AND sort_order=?', (previous.get('id', 0),slot)).fetchone()
        existing_url = '/uploads/' + current[0] if current else ref[0] if ref else previous.get('image_url', '') if slot == 0 else ''
        if existing_url and existing_url != raw[title]:
            raise ValueError(f'{title}: 기존 사진을 보호하기 위해 교체는 관리자 수동 수정에서 진행해주세요.')
        if not existing_url:
            photos.append({'slot': slot, 'url': raw[title], 'metadata': metadata})
    return data, patch, warnings, pick, photos


def state_hash(conn):
    """Conservative optimistic lock: no overwrite after the preview becomes stale."""
    digest = hashlib.sha256()
    for table in ('places', 'categories', 'place_category_links', 'images', 'place_image_references', 'place_pick_periods', 'site_settings'):
        for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid'):
            digest.update(json.dumps(list(row), ensure_ascii=False).encode())
    return digest.hexdigest()


def prepare_row(conn, raw, kind, root, target=None):
    previous = {}
    if target or raw['place_id']:
        value = str(target or raw['place_id'])
        if not value.isascii() or not value.isdecimal():
            raise ValueError('place_id는 실제 장소 번호여야 합니다.')
        row = conn.execute('SELECT * FROM places WHERE id=? AND deleted_at IS NULL AND merged_into_id IS NULL', (int(value),)).fetchone()
        if not row:
            raise ValueError('place_id에 해당하는 현재 장소가 없습니다.')
        previous = dict(row)
    data, patch, warnings, pick, photos = normalize_common_fields(conn, raw, kind, previous, root)
    terms = (validate_cafe_row if kind == 'cafe' else validate_restaurant_row)(conn, raw, data['category_id'], previous)
    return dict(data=data, patch=patch, warnings=warnings, pick=pick, photos=photos, terms=terms, previous=previous)


def preview_rows(conn, rows, kind, root):
    result, seen = [], {}
    for source in rows:
        item = {'line': source['line'], 'raw': source['raw'], 'errors': [], 'warnings': [], 'duplicates': [], 'status': '정상', 'default_action': 'create'}
        try:
            hours = csv_business_hours(source['raw'], bool(source['raw'].get('place_id')))
            item['hours_status'] = '기존 값 유지' if hours is None else '시간 입력' if hours else '미확인'
            item['hours_display'] = hours or ''
        except ValueError as error:
            item['hours_error'] = str(error)
        try:
            prepared = prepare_row(conn, source['raw'], kind, root)
            data = prepared['data']
            candidates = {r['id']: r for r in duplicates(conn, data, include_private=True)}
            # Same-address candidates count even when the shop names differ.
            for r in conn.execute('SELECT id,name,address,category_id FROM places WHERE address=? AND deleted_at IS NULL AND merged_into_id IS NULL', (data['address'],)):
                candidates[r['id']] = dict(r)
            if prepared['previous']:
                candidates[data['id']] = dict(prepared['previous'])
            item['duplicates'] = [{'id': r['id'], 'name': r['name'], 'address': r['address'],
                'can_update': conn.execute('SELECT category_id FROM places WHERE id=?', (r['id'],)).fetchone()[0] == data['category_id']} for r in candidates.values()]
            key = (data['name'], data['address'])
            item['same_file'] = seen.get(key)
            seen.setdefault(key, source['line'])
            item['warnings'] = prepared['warnings']
            item['address'] = data['address']
            item['needs_coordinates'] = data.get('latitude') is None or data.get('longitude') is None
            if item['duplicates'] or item['same_file']:
                item['status'], item['default_action'] = '중복 가능', 'skip'
            elif item['warnings']:
                item['status'], item['default_action'] = '경고', 'skip'
        except ValueError as error:
            item['errors'] = [str(error)]
            item['status'], item['default_action'] = '오류', 'skip'
        result.append(item)
    return result


def save_rows(database, connect_db, stage, choices, actor):
    root = Path(database).parent / 'uploads'
    with closing(connect_db()) as conn:
        receipt = conn.execute('SELECT result FROM csv_import_runs WHERE token=? AND actor=?', (stage['token'], actor)).fetchone()
        if receipt:
            return json.loads(receipt[0])
    path = backup(database, stage['kind'] + '-csv-import')
    with closing(connect_db()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        receipt = conn.execute('SELECT result FROM csv_import_runs WHERE token=? AND actor=?', (stage['token'], actor)).fetchone()
        if receipt:
            return json.loads(receipt[0])
        if state_hash(conn) != stage['state_hash']:
            raise ValueError('미리보기 이후 장소 또는 분류가 변경되었습니다. CSV를 다시 업로드해 확인해주세요.')
        result = {'created': [], 'updated': [], 'skipped': 0, 'backup': path}
        updated = set()
        for item in stage['rows']:
            action, target = choices.get(str(item['line']), ('skip', ''))
            if action == 'skip':
                result['skipped'] += 1
                continue
            if item['errors'] or action not in ('create', 'update'):
                raise ValueError(f"{item['line']}행: 오류 행 또는 잘못된 작업은 저장할 수 없습니다.")
            raw = dict(item['raw'])
            if action == 'update':
                if str(target) not in {str(r['id']) for r in item['duplicates'] if r['can_update']} or target in updated:
                    raise ValueError('업데이트 대상을 확인해주세요. 한 장소는 한 번만 수정할 수 있습니다.')
                updated.add(target)
            else:
                raw['place_id'] = ''
            p = prepare_row(conn, raw, stage['kind'], root, target if action == 'update' else None)
            data = p['patch'] if action == 'update' else p['data']
            if action == 'create':
                data['slug'] = 'csv-' + uuid.uuid4().hex
                columns = list(data)
                place_id = conn.execute(f"INSERT INTO places ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", list(data.values())).lastrowid
            else:
                place_id = int(target)
                if data:
                    conn.execute(f"UPDATE places SET {','.join(k+'=?' for k in data)},updated_at=CURRENT_TIMESTAMP WHERE id=?", (*data.values(), place_id))
            if p['terms'] is not None:
                from place_taxonomy import save_links
                save_links(conn, place_id, p['terms'])
            if 'verification_status' in data:
                conn.execute("UPDATE places SET verified_at=CASE WHEN verification_status='jejuno_verified' THEN COALESCE(verified_at,CURRENT_TIMESTAMP) ELSE NULL END,verified_by=CASE WHEN verification_status='jejuno_verified' THEN ? ELSE NULL END WHERE id=?", (actor,place_id))
            if p['pick']:
                conn.execute('''INSERT INTO place_pick_periods(place_id,year,month,is_active) VALUES (?,?,?,?)
                    ON CONFLICT(place_id,year,month) DO UPDATE SET is_active=excluded.is_active''', (place_id,*p['pick']))
            for photo in p['photos']:
                if meta := photo['metadata']:
                    conn.execute('INSERT INTO images(place_id,file_path,width,height,byte_size,sort_order) VALUES (?,?,?,?,?,?)',
                        (place_id,meta['file_path'],meta['width'],meta['height'],meta['byte_size'],photo['slot']))
                else:
                    conn.execute('INSERT INTO place_image_references(place_id,sort_order,url) VALUES (?,?,?)', (place_id,photo['slot'],photo['url']))
            result['created' if action == 'create' else 'updated'].append(place_id)
        conn.execute('INSERT INTO csv_import_runs(token,actor,kind,result) VALUES (?,?,?,?)', (stage['token'],actor,stage['kind'],json.dumps(result)))
    return result


def choice_lists(conn):
    from submission_fields import TIME_OPTIONS
    lists = {'지역': [r['label'] for r in catalog(conn, 'region')], '잘맞는동행': [r['label'] for r in catalog(conn, 'companion')],
             '커피맛_특징': list(COFFEE_OPTIONS), '탐나는전': ['미확인', '가능', '불가'],
             'JEJU_KNOW_PICK': ['아니오', '예'], '영업시간_상태': ['시간 입력', '미확인'],
             '영업_시작': list(TIME_OPTIONS), '영업_종료': list(TIME_OPTIONS)}
    lists.update({title: list(options) for title, (_, options) in CHOICES.items()})
    cafe = conn.execute("SELECT id FROM categories WHERE slug='cafe' AND type='place'").fetchone()[0]
    food = conn.execute("SELECT id FROM categories WHERE slug='food' AND type='place'").fetchone()[0]
    lists['카페_특징'] = [r['name'] for r in official_terms(conn, cafe)]
    terms = official_terms(conn, food)
    lists['맛집_대분류'] = [r['name'] for r in terms if r['parent_id'] == food]
    lists['맛집_세부유형'] = list(dict.fromkeys(r['name'] for r in terms if r['parent_id'] != food))
    parents = {r['id']: r['name'] for r in terms}
    lists['맛집 분류 관계'] = [parents[r['parent_id']] + ' → ' + r['name'] for r in terms if r['parent_id'] in parents]
    return lists
