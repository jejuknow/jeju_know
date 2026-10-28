"""Shared time normalization; stored hours remain the existing plain text field."""
import re
from decimal import Decimal, ROUND_HALF_UP


def normalize_business_time(value):
    text = str(value if value is not None else '').strip()
    match = re.fullmatch(r'([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}))?', text)
    if match:
        hour, minute, second = (int(part or 0) for part in match.groups())
        if hour < 24 and minute < 60 and second < 60:
            return f'{hour:02d}:{minute:02d}'
    elif re.fullmatch(r'(?:0(?:\.[0-9]+)?|\.[0-9]+)', text):
        # Decimal and minute rounding avoid 09:29 from Excel's 0.395833.
        minutes = int((Decimal(text) * 1440).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        minutes %= 1440  # Rounded midnight is 00:00, never invalid 24:00.
        return f'{minutes // 60:02d}:{minutes % 60:02d}'
    raise ValueError('HH:MM, H:MM, HH:MM:SS 또는 0 이상 1 미만의 Excel 시간값으로 입력해주세요.')


def business_hours_range(start, end):
    times = []
    for label, value in (('시작', start), ('종료', end)):
        text = str(value if value is not None else '').strip()
        if not text:
            raise ValueError(f'영업 {label}시간이 비어 있습니다.')
        try:
            times.append(normalize_business_time(text))
        except ValueError as error:
            raise ValueError(f"영업 {label}시간 '{text[:80]}'을 읽을 수 없습니다. {error}") from None
    return ' ~ '.join(times)


def csv_business_hours(raw, previous=None):
    status = raw.get('영업시간_상태', '')
    start, end = raw.get('영업_시작', ''), raw.get('영업_종료', '')
    if status not in ('', '시간 입력', '미확인'):
        raise ValueError('영업시간 상태는 시간 입력 / 미확인 중 선택해주세요.')
    if status == '미확인':
        if start or end:
            raise ValueError('영업시간 상태가 미확인입니다. 시작·종료시간을 비우거나 시간 입력을 선택해주세요.')
        return ''
    if not status and not start and not end:
        # Existing update contract: blank cells never erase saved information.
        return None if previous else ''
    return business_hours_range(start, end)
