"""Optional, explicit relaxations using the existing public query unchanged."""
from place_taxonomy import public_search

FILTER_KEYS = ('category','q','region','companion','detail','condition','pick','new','focus','cuisine')


def suggestions(conn, query, taxonomy):
    filters = {key: str(query.get(key) or '') for key in FILTER_KEYS}
    if any(len(value) > (8100 if key == 'detail' else 1000) for key,value in filters.items()):
        return []
    # Suggestions never run a parallel search algorithm and never mix into the original results.
    original_count = len(public_search(conn, **filters))
    if original_count > 2:
        return []
    labels = {c['slug']:c['name'] for c in taxonomy['categories'] + taxonomy['terms']}
    candidates = []
    for key in FILTER_KEYS:
        value = filters[key]
        if not value:
            continue
        values = value.split(',')[:10] if key in ('companion','detail','focus') else [value]
        for part in values:
            relaxed = dict(filters)
            if key in ('companion','detail','focus'):
                relaxed[key] = ','.join(v for v in value.split(',') if v != part)
            else:
                relaxed[key] = ''
            label = labels.get(part, taxonomy.get('region_labels',{}).get(part, taxonomy.get('companion_labels',{}).get(part, part)))
            if key == 'category':
                relaxed.update(cuisine='',detail='',focus='')
                if filters['cuisine'] or filters['detail'] or filters['focus']:
                    label += ' 및 하위 분류'
            elif key == 'cuisine':
                relaxed['detail'] = ''
                if filters['detail']:
                    label += ' 및 세부유형'
            elif key == 'pick':
                label = 'JEJU_KNOW PICK'
            elif key == 'new':
                label = 'NEW'
            elif key == 'condition' and part == 'rain':
                label = '비 오는 날'
            candidates.append((label, relaxed))
        if key in ('detail','focus') and len(values)>1:
            candidates.append(('카페 특징' if key=='detail' and filters['category']=='cafe' else '세부 조건', {**filters,key:''}))
    result, seen = [], set()
    for label, relaxed in candidates[:30]:
        signature = tuple(relaxed.items())
        if signature in seen:
            continue
        seen.add(signature)
        count = len(public_search(conn, **relaxed))
        if count > original_count:
            result.append({'label': label, 'count': count, 'filters': {k:v for k,v in relaxed.items() if v}})
    return sorted(result, key=lambda s:s['count'], reverse=True)[:4]
