"""Public metadata from stored content; no guessed ratings, facts or host headers."""
import re
from datetime import datetime
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET

ORIGIN = 'https://www.jeju-know.com'
DEFAULT_IMAGE = ORIGIN + '/static/images/brand/default-og.png'
FILTER_PAGES = {'/', '/find', '/map', '/places', '/new', '/news'}


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


def page_metadata(request, description):
    path = request.url.path
    private = private_page(path)
    result = {'title': '', 'description': description, 'image': DEFAULT_IMAGE,
              'canonical': '' if private else ORIGIN + path,
              'robots': 'noindex,nofollow' if private else 'index,follow', 'json_ld': None}
    # Filter combinations consolidate to their base page, without a conflicting noindex.
    if path in FILTER_PAGES:
        result['canonical'] = ORIGIN + path
    if path == '/':
        result['title'] = 'JEJU_KNOW | 내가 다시 가고 싶은 제주 기록'
        result['json_ld'] = {'@context': 'https://schema.org', '@type': 'WebSite',
                             'name': 'JEJU_KNOW', 'url': ORIGIN + '/',
                             'publisher': {'@type': 'Organization', 'name': 'JEJU_KNOW',
                                           'url': ORIGIN + '/', 'logo': DEFAULT_IMAGE}}
    elif path == '/new':
        result['title'] = '제주 신상 카페·맛집 | JEJU_KNOW'
    elif path == '/news':
        result['title'] = '제주 이슈 | JEJU_KNOW'
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
    description = f'{location}의 {category + " " if category else ""}{name}.'
    facts = [label for field,label in [('business_hours','영업시간'), ('signature_menu','대표메뉴'),
                                      ('detail_features','장소 특징'), ('note','JEJU_KNOW 꿀팁')] if place.get(field)]
    if facts:
        description += ' ' + ', '.join(facts) + '을 확인하세요.'
    url = content_url('/place/', place['slug'])
    data = {'@context': 'https://schema.org', '@type': {'food':'Restaurant','cafe':'CafeOrCoffeeShop'}.get(place.get('category_slug'), 'Place'),
            'name': name, 'url': url}
    if place.get('image_url'):
        data['image'] = absolute_image(place['image_url'])
    if place.get('address'):
        data['address'] = {'@type':'PostalAddress','streetAddress':place['address']}
    if place.get('phone_href'):
        data['telephone'] = place['phone']
    if place.get('has_coordinates'):
        data['geo'] = {'@type':'GeoCoordinates','latitude':place['latitude'],'longitude':place['longitude']}
    hours = opening_hours(place)
    if hours and data['@type'] in ('Restaurant','CafeOrCoffeeShop'):
        data['openingHoursSpecification'] = [hours]
    return {'title': f'{name} | {" ".join(v for v in (location, category) if v)} | JEJU_KNOW',
            'share_title': f'{name} | JEJU_KNOW', 'description': description,
            'share_text': f'{location}의 {category + ", " if category else ""}{name} 정보를 JEJU_KNOW에서 확인해보세요.',
            'image': absolute_image(place.get('image_url')), 'canonical': url, 'robots':'index,follow', 'json_ld':data}


def collection_metadata(collection):
    return {'title': collection['title'] + ' | JEJU_KNOW', 'description':collection.get('description') or collection['title'],
            'canonical':content_url('/best/',collection['slug']), 'image':absolute_image(collection.get('cover_image_url')),
            'robots':'index,follow', 'json_ld':None}


def issue_metadata(item):
    return {'title':item['title']+' | JEJU_KNOW', 'description':item.get('summary') or item['title'],
            'canonical':content_url('/news/',item['slug']), 'image':absolute_image(item.get('image_url')),
            'robots':'index,follow', 'json_ld':None}


def sitemap(conn):
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
    for table,prefix,extra in [('places','/place/',' AND merged_into_id IS NULL'), ('best_collections','/best/',''), ('news_posts','/news/','')]:
        for row in conn.execute(f'SELECT slug,updated_at FROM {table} WHERE published=1 AND deleted_at IS NULL{extra} ORDER BY id'):
            add(content_url(prefix,row['slug']),row['updated_at'])
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
