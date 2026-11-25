"""One time-state parser/formatter for admin, member and public ISSUE surfaces."""
import re

TIME_OPTIONS = tuple(f'{hour:02}:{minute:02}' for hour in range(24) for minute in (0,30))


def time_mode(item):
    return item.get('time_mode') or ('specified' if item.get('start_time') or item.get('end_time') else 'unknown')


def format_issue_time(item):
    mode = time_mode(item)
    if mode == 'all_day':
        return '종일'
    if mode == 'unknown':
        return '시간 미정'
    return f"{item.get('start_time') or '시작 미정'} ~ {item.get('end_time') or '종료 미정'}"


def parse_time_state(form, existing=None):
    old = dict(existing) if existing else {}
    values = {key:str(form.get(key,old.get(key) or '')).strip() for key in ('start_time','end_time')}
    # Old forms/clients remain valid; explicit state is used by the new form.
    mode = str(form.get('time_mode') or ('specified' if any(values.values()) else old.get('time_mode') or 'unknown'))
    error = None
    if mode not in ('specified','all_day','unknown'):
        error = '시간 상태를 선택해주세요.'
    elif mode != 'specified':
        values = dict(start_time='',end_time='')  # Existing NOT NULL convention.
    elif 'time_mode' in form:
        for key,value in values.items():
            if value not in TIME_OPTIONS and not (existing and value == (old.get(key) or '')):
                error = '시작·종료시간을 30분 단위로 선택해주세요. 기존 시간값은 그대로 유지할 수 있습니다.'
    return {'time_mode':mode,**values}, error


def time_error(data):
    for key in ('start_time','end_time'):
        if data.get(key) and not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',data[key]):
            return '시간은 00:00부터 23:59 사이로 입력해주세요.'
    if data.get('event_start') == data.get('event_end') and data.get('start_time') and data.get('end_time') and data['end_time'] < data['start_time']:
        return '같은 날짜의 종료시간은 시작시간보다 빠를 수 없습니다.'
