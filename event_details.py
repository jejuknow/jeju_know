"""Optional EVENT admission/benefit facts on the existing news records."""
import re

from issue_calendar_migration import apply_columns
from issue_discovery import LABELS

VERSION = '20261126_event_expansion'
TEXT_LIMITS = {'benefit_summary':600, 'benefit_audience':200, 'benefit_conditions':1000,
               'promo_code':80, 'benefit_howto':1000, 'benefit_quantity':200}
PRICE_FIELDS = ('regular_price', 'benefit_price')
FIELDS = ('fee_status', *TEXT_LIMITS, *PRICE_FIELDS)


def migrate(database):
    columns = {'fee_status':"TEXT CHECK(fee_status IN ('free','paid'))",
               **{key:'TEXT' for key in TEXT_LIMITS},
               **{key:f'INTEGER CHECK({key}>=0)' for key in PRICE_FIELDS}}
    apply_columns(database, VERSION, 'jejuno-before-event-expansion', columns,
                  'CREATE INDEX IF NOT EXISTS news_event_fee ON news_posts(fee_status,event_end) WHERE published=1 AND deleted_at IS NULL')


def parse_fields(form, existing=None):
    old = dict(existing) if existing else {}
    values = {key:form.get(key, old.get(key)) for key in FIELDS}
    return {key:(str(value).strip() or None) if value is not None else None for key,value in values.items()}


def validate(data):
    if data.get('fee_status') not in (None, '', 'free', 'paid'):
        return '참가비 정보를 확인해주세요.'
    for key, limit in TEXT_LIMITS.items():
        if len(data.get(key) or '') > limit:
            return '혜택 정보의 입력 가능한 글자 수를 초과했습니다.'
    for key in PRICE_FIELDS:
        value = data.get(key)
        if value is None or value == '':
            data[key] = None
        elif not re.fullmatch(r'\d{1,10}', str(value)) or int(value) > 1_000_000_000:
            return '가격은 0~1,000,000,000 사이의 원 단위 숫자로 입력하거나 비워주세요.'
        else:
            data[key] = int(value)
    if data['category'] == LABELS['benefit-discount']:
        if not all(data.get(k) for k in ('summary','event_start','event_end')):
            return '혜택·할인은 한줄 요약과 시작일·종료일을 입력해주세요.'
        if not (data.get('external_url') or data.get('source_name')):
            return '혜택을 확인할 수 있는 출처 또는 공식 링크를 입력해주세요.'


def decorate(item, day):
    from datetime import date
    item['is_benefit'] = item['category'] == LABELS['benefit-discount']
    item['end_badge'] = ''
    try:
        remaining = (date.fromisoformat(item['event_end'])-day).days
        if item['schedule_key'] != 'ended' and 0 <= remaining <= 3:
            item['end_badge'] = '오늘 종료' if remaining == 0 else f'종료 D-{remaining}'
    except (ValueError, TypeError):
        pass
    return item
