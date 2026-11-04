"""Public metadata from stored content; no guessed ratings, facts or host headers."""
import os
import re
from datetime import date, datetime
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET

ORIGIN = 'https://www.jeju-know.com'
DEFAULT_IMAGE = ORIGIN + '/static/images/brand/default-og.png'
FILTER_PAGES = {'/', '/find', '/map', '/places', '/new', '/news'}
PAGES = {
    '/': ('JEJU_KNOW | 제주노의 맛집·카페·행사 추천', '제주노가 기록한 제주 맛집, 카페, 가볼 곳과 행사·이슈를 지역·취향·동행에 맞게 찾아보세요.'),
    '/find': ('제주 취향 장소 찾기 | JEJU_KNOW', '제주 지역, 장소 종류, 동행과 카페·맛집 특징을 선택해 취향에 맞는 장소를 찾아보세요. 검색한 장소는 지도에서도 확인할 수 있습니다.'),
    '/map': ('제주 맛집·카페 지도 | JEJU_KNOW', '제주 맛집과 카페, 가볼 곳의 위치를 지도에서 살펴보세요. 지역과 카테고리로 탐색하고 장소별 정보와 추천 이유를 확인하세요.'),
    '/new': ('제주 신상 맛집·카페 NEW | JEJU_KNOW', '최근 6개월 사이에 문을 연 제주 맛집·카페와 새 소식으로 소개하는 장소를 모았습니다. 오픈 정보와 확인 상태를 함께 살펴보세요.'),
    '/news': ('제주 행사·축제·팝업·이슈 | JEJU_KNOW', '제주 행사·축제, 전시·팝업, 교육·체험과 지역소식을 날짜별로 찾아보세요. 일정, 장소와 공식 출처를 확인할 수 있습니다.'),
    '/best': ('제주 상황별 추천 BEST | JEJU_KNOW', '제주 여행의 동행과 상황에 맞춘 장소 추천 모음을 만나보세요. 모음별 소개와 장소 정보를 확인하고 다음 제주 일정을 준비하세요.'),
    '/places': ('제주 장소 검색 결과 | JEJU_KNOW', '선택한 조건에 맞는 제주 맛집·카페·가볼 곳의 전체 검색 결과입니다. 장소별 소개와 상세 정보를 확인하세요.'),
    '/partnership': ('광고·제휴 안내 | JEJU_KNOW', 'JEJU_KNOW의 제주 장소·행사 소개와 광고·제휴 안내를 확인하고 협업을 문의하세요.'),
}


def summary(value, limit=160):
    # Stored text remains escaped by Jinja/tojson; normalize whitespace only.
    value = ' '.join(str(value or '').split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + '…'


def verification_meta():
    return {name: value for name, env in (
        ('google-site-verification', 'GOOGLE_SITE_VERIFICATION'),
        ('naver-site-verification', 'NAVER_SITE_VERIFICATION'))
        if (value := os.environ.get(env, '').strip())}


def robots_policy(request):
    if private_page(request.url.path):
        return 'noindex,nofollow'
    if (request.url.path in ('/contact', '/contact/sent') or
            request.url.path in FILTER_PAGES and request.url.query or
            any(key in request.query_params for key in ('preview', 'test'))):
        return 'noindex,follow'
    return 'index,follow'


def breadcrumbs(section, path, title, url):
    return {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': i, 'name': name, 'item': link}
        for i, (name, link) in enumerate((('홈', ORIGIN + '/'), (section, ORIGIN + path), (title, url)), 1)]}


def initial_places(conn, query, decorate, *, limit=None, **overrides):
    """Render the existing search results before JS loads, using the API's query logic."""
    from place_taxonomy import public_search
    args = {key: query.get(key, '') for key in
            ('category', 'q', 'region', 'companion', 'detail', 'condition', 'pick', 'new', 'focus', 'cuisine')}
    args.update(overrides)
    rows = public_search(conn, **args)
    if args['pick'] == 'current':
        rows.sort(key=lambda p: (p['pick_order'], p['id']))
    return {'initial_places': [decorate(row, public=True) for row in rows[:limit]], 'initial_total': len(rows)}


def absolute_image(value):
    value = str(value or '')
    if value.startswith(('/uploads/', '/static/')):
        return ORIGIN + value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return DEFAULT_IMAGE
    return value if parsed.scheme == 'https' and parsed.hostname and not parsed.username else DEFAULT_IMAGE


def content_url(prefix, slug):
    return ORIGIN + prefix + quote(str(slug), safe='')


def private_page(path):
    return (path.startswith(('/admin', '/account', '/api/', '/issues/')) or
            path in ('/login', '/signup', '/logout', '/map/register', '/places/new') or
            bool(re.fullmatch(r'/places/\d+/(edit|suggest)', path)))


def page_metadata(request, description, settings=None):
    path = request.url.path
    private = private_page(path)
    result = {'title': '', 'description': description, 'image': DEFAULT_IMAGE,
              'canonical': '' if private else ORIGIN + quote(path, safe='/'),
              'robots': robots_policy(request), 'json_ld': None}
    if path in PAGES:
        result['title'], result['description'] = PAGES[path]
    if path == '/':
        organization = {'@type': 'Organization', '@id': ORIGIN + '/#organization',
                        'name': 'JEJU_KNOW', 'alternateName': '제주노', 'url': ORIGIN + '/',
                        'logo': ORIGIN + '/static/images/brand/jeju_know-logo.png'}
        instagram = (settings or {}).get('instagram_url', '')
        if instagram.startswith('https://www.instagram.com/'):
            organization['sameAs'] = [instagram]
        result['json_ld'] = {'@context': 'https://schema.org', '@type': 'WebSite',
                             'name': 'JEJU_KNOW', 'alternateName': '제주노', 'url': ORIGIN + '/',
                             'publisher': organization}
    elif path in ('/contact', '/contact/sent'):
        result.update(title='문의하기 | JEJU_KNOW', description='JEJU_KNOW 광고·제휴, 협업 및 홈페이지 관련 문의를 남겨주세요.',
                      robots='noindex,follow', canonical=ORIGIN + '/contact')
    return result


def opening_hours(place):
    match = re.fullmatch(r'([0-2]\d:[0-5]\d)\s*[~–-]\s*([0-2]\d:[0-5]\d)', place.get('business_hours') or '')
    if not match or any(int(v[:2]) > 23 for v in match.groups()):
        return None
    result = {'@type': 'OpeningHoursSpecification', 'opens': match[1], 'closes': match[2]}
    names = dict(zip('월화수목금토일', ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')))
    closed = (place.get('closed_days') or '').strip()
    if closed == '연중무휴':
        result['dayOfWeek'] = list(names.values())
    elif re.fullmatch(r'[월화수목금토일]요일(?: · [월화수목금토일]요일)*', closed):
        result['dayOfWeek'] = [v for k,v in names.items() if k+'요일' not in closed]
        if not result['dayOfWeek']:
            return None
    return result


def place_metadata(place):
    name, region, category = place['name'], place.get('region_label') or place.get('region'), place.get('category')
    location = ' '.join(v for v in ('제주', region) if v)
    intro = place.get('one_line') or place.get('reason') or place.get('note')
    description = summary(f'{location}의 {category + " " if category else ""}{name}. ' +
                          (intro or '등록된 장소 정보와 위치를 확인하세요.'))
    url = content_url('/place/', place['slug'])
    data = {'@context': 'https://schema.org', '@type': {'food':'Restaurant','cafe':'CafeOrCoffeeShop', 'attraction':'TouristAttraction'}.get(place.get('category_slug'), 'Place'),
            'name': name, 'url': url, 'description': description}
    image = absolute_image(place.get('image_url'))
    if image != DEFAULT_IMAGE:
        data['image'] = image
    if place.get('address'):
        data['address'] = {'@type':'PostalAddress','streetAddress':place['address']}
    if place.get('phone_href'):
        data['telephone'] = place['phone']
    if place.get('has_coordinates'):
        data['geo'] = {'@type':'GeoCoordinates','latitude':place['latitude'],'longitude':place['longitude']}
    hours = opening_hours(place)
    if hours and data['@type'] in ('Restaurant','CafeOrCoffeeShop'):
        data['openingHoursSpecification'] = [hours]
    return {'title': f'{name} - {" ".join(v for v in (location, category) if v)} | JEJU_KNOW',
            'share_title': f'{name} | JEJU_KNOW', 'description': description,
            'share_text': f'{location}의 {category + ", " if category else ""}{name} 정보를 JEJU_KNOW에서 확인해보세요.',
            'image': absolute_image(place.get('image_url')), 'canonical': url,
            'robots': 'noindex,follow' if place.get('is_example') else 'index,follow', 'json_ld':data,
            'breadcrumbs': breadcrumbs('FIND', '/find', name, url)}


def collection_metadata(collection):
    url = content_url('/best/', collection['slug'])
    return {'title': collection['title'] + ' | JEJU_KNOW', 'description':summary(collection.get('description') or collection['title'] + '에 담긴 제주 장소와 추천 이유를 살펴보세요.'),
            'canonical':url, 'image':absolute_image(collection.get('image_url')),
            'robots':'index,follow', 'json_ld':None,
            'breadcrumbs': breadcrumbs('BEST', '/best', collection['title'], url)}


def issue_metadata(item):
    url = content_url('/news/', item['slug'])
    description = summary(item.get('summary') or ' '.join(str(item.get(key) or '') for key in
                          ('title', 'event_start', 'event_end', 'venue', 'address')))
    return {'title':item['title']+' - 제주 '+(item.get('category_label') or item['category'])+' | JEJU_KNOW',
            'description':description, 'canonical':url, 'image':absolute_image(item.get('image_url')),
            'robots':'index,follow', 'json_ld':event_schema(item, url, description),
            'breadcrumbs': breadcrumbs('ISSUE', '/news', item['title'], url)}


def event_schema(item, url, description):
    from issue_discovery import LABELS, stored_names
    if item.get('category') not in {name for key in ('events', 'exhibitions', 'activities') for name in stored_names(LABELS[key])}:
        return None
    if not all(str(item.get(key) or '').strip() for key in ('title', 'venue', 'address')):
        return None
    try:
        start, end = (date.fromisoformat(item.get(key) or '') for key in ('event_start', 'event_end'))
    except (ValueError, TypeError):
        return None
    if end < start:
        return None
    def timestamp(day, time):
        return day.isoformat() + 'T' + time + ':00+09:00' if re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', time or '') else day.isoformat()
    data = {'@context': 'https://schema.org', '@type': 'Event', 'name': item['title'],
            'url': url, 'description': description,
            'startDate': timestamp(start, item.get('start_time')),
            'endDate': timestamp(end, item.get('end_time')),
            'location': {'@type': 'Place', 'name': item['venue'],
                         'address': {'@type': 'PostalAddress', 'streetAddress': item['address']}}}
    if 'T' in data['startDate'] and 'T' in data['endDate'] and data['endDate'] < data['startDate']:
        return None
    image = absolute_image(item.get('image_url'))
    if image != DEFAULT_IMAGE:
        data['image'] = image
    return data


def sitemap(conn):
    from issue_discovery import public_where, today
    root = ET.Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    def add(url, modified=None):
        node = ET.SubElement(root, 'url')
        ET.SubElement(node, 'loc').text = url
        if modified:
            try:
                value = datetime.fromisoformat(str(modified).replace('Z','+00:00')).date().isoformat()
                ET.SubElement(node, 'lastmod').text = value
            except ValueError:
                pass
    for path in ('/', '/find', '/map', '/new', '/news', '/best'):
        add(ORIGIN+path)
    for table,prefix,extra in [('places','/place/',' AND merged_into_id IS NULL AND COALESCE(is_example,0)=0'), ('best_collections','/best/',''), ('news_posts','/news/','')]:
        where, params = public_where(today()) if table == 'news_posts' else ('published=1 AND deleted_at IS NULL'+extra, [])
        for row in conn.execute(f'SELECT slug,updated_at FROM {table} WHERE {where} ORDER BY id', params):
            add(content_url(prefix,row['slug']),row['updated_at'])
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
