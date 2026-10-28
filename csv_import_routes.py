"""Admin-only, staged CSV imports. Preview files never enter the public tree."""
import hashlib
import json
import re
import secrets
import time
from contextlib import closing
from pathlib import Path

from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from auth_security import current_user
from csv_place_import import (MAX_BYTES, HEADERS, KINDS, parse_csv, preview_rows,
                              state_hash, export_csv, save_rows, choice_lists, template_examples)
from community_places import coordinates

TTL = 3600


class PreviewStore:
    def __init__(self, database):
        self.root = Path(database).parent / 'csv-import-previews'
        self.root.mkdir(exist_ok=True)

    def write(self, stage):
        path = self.root / (stage['token'] + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(stage, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)

    def create(self, owner, kind, rows, digest):
        for path in self.root.glob('*.json'):
            if path.stat().st_mtime < time.time() - TTL:
                path.unlink(missing_ok=True)
        if len(list(self.root.glob('*.json'))) >= 50:
            raise ValueError('미리보기가 많습니다. 기존 미리보기를 사용하거나 한 시간 후 다시 시도해주세요.')
        stage = dict(token=secrets.token_hex(24), owner=owner, kind=kind, rows=rows,
                     state_hash=digest, expires=time.time() + TTL)
        self.write(stage)
        return stage

    def read(self, token, owner, kind):
        if not re.fullmatch(r'[a-f0-9]{48}', token):
            raise ValueError('미리보기를 찾을 수 없습니다.')
        try:
            stage = json.loads((self.root / (token + '.json')).read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise ValueError('미리보기가 만료되었습니다. CSV를 다시 업로드해주세요.') from None
        if stage['owner'] != owner or stage['kind'] != kind or stage['expires'] < time.time():
            raise ValueError('미리보기가 만료되었거나 현재 로그인과 일치하지 않습니다.')
        return stage


def register_csv_routes(app, templates, connect_db, guard, csrf, flash):
    store = PreviewStore(app.state.database)
    root = Path(app.state.database).parent / 'uploads'

    def actor(request):
        return 'admin:' + str(request.session['admin_id']) if request.session.get('admin_id') else 'user:' + str(current_user(request)['id'])

    def owner(request):
        return actor(request) + ':' + hashlib.sha256(request.session.get('csrf_token', '').encode()).hexdigest()

    def kind_check(kind):
        if kind not in KINDS:
            raise HTTPException(404)

    def render(request, kind=None, **context):
        with closing(connect_db()) as conn:
            choices = choice_lists(conn)
        return templates.TemplateResponse(request=request, name='admin/csv_import.html', context={
            'kind': kind, 'label': KINDS[kind][0] if kind else '', 'choices': choices,
            'kakao_map_key': app.state.kakao_map_key, **context})

    def file_response(raw, filename, mime='text/csv; charset=utf-8'):
        return Response(raw, media_type=mime, headers={'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get('/admin/imports', response_class=HTMLResponse)
    def landing(request: Request):
        return guard(request) or render(request)

    @app.get('/admin/imports/template.xlsx')
    def workbook(request: Request):
        if response := guard(request):
            return response
        from csv_import_workbook import build_workbook
        with closing(connect_db()) as conn:
            content = build_workbook(choice_lists(conn))
        return file_response(content, 'JEJU_KNOW-place-template.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @app.get('/admin/imports/{kind}/template.csv')
    def template(request: Request, kind: str):
        if response := guard(request):
            return response
        kind_check(kind)
        return file_response(export_csv(HEADERS[kind], template_examples(kind)), kind + '-template.csv')

    @app.get('/admin/imports/{kind}', response_class=HTMLResponse)
    def upload_page(request: Request, kind: str):
        if response := guard(request):
            return response
        kind_check(kind)
        return render(request, kind)

    @app.post('/admin/imports/{kind}/preview')
    async def preview(request: Request, kind: str):
        if response := guard(request):
            return response
        kind_check(kind)
        form = await request.form(max_files=1, max_fields=5, max_part_size=MAX_BYTES)
        if not csrf(request, form):
            return HTMLResponse('CSRF 검증에 실패했습니다.', status_code=400)
        upload = form.get('csv_file')
        try:
            if not isinstance(upload, UploadFile):
                raise ValueError('CSV 파일을 선택해주세요.')
            raw = await upload.read(MAX_BYTES + 1)
            rows = parse_csv(raw, upload.filename, upload.content_type or '', kind)
            def prepare():
                with closing(connect_db()) as conn:
                    conn.execute('BEGIN')
                    rows_preview = preview_rows(conn, rows, kind, root)
                    return rows_preview, state_hash(conn)
            prepared, digest = await run_in_threadpool(prepare)
            stage = store.create(owner(request), kind, prepared, digest)
        except ValueError as error:
            flash(request, str(error), 'error')
            return render(request, kind)
        finally:
            if isinstance(upload, UploadFile):
                await upload.close()
        return RedirectResponse(f"/admin/imports/{kind}/preview/{stage['token']}", status_code=303)

    @app.get('/admin/imports/{kind}/preview/{token}', response_class=HTMLResponse)
    def preview_page(request: Request, kind: str, token: str):
        if response := guard(request):
            return response
        kind_check(kind)
        try:
            stage = store.read(token, owner(request), kind)
        except ValueError as error:
            return HTMLResponse(str(error), status_code=400)
        with closing(connect_db()) as conn:
            receipt = conn.execute('SELECT result FROM csv_import_runs WHERE token=? AND actor=?', (token, actor(request))).fetchone()
        return render(request, kind, stage=stage, result=json.loads(receipt[0]) if receipt else None,
                      counts={s: sum(row['status'] == s for row in stage['rows']) for s in ('정상', '경고', '오류', '중복 가능')})

    @app.get('/admin/imports/{kind}/preview/{token}/errors.csv')
    def errors(request: Request, kind: str, token: str):
        if response := guard(request):
            return response
        kind_check(kind)
        try:
            stage = store.read(token, owner(request), kind)
        except ValueError as error:
            return HTMLResponse(str(error), status_code=400)
        rows = [{**row['raw'], '오류사유': '; '.join(row['errors'])} for row in stage['rows'] if row['errors']]
        return file_response(export_csv(HEADERS[kind] + ['오류사유'], rows), kind + '-errors.csv')

    @app.post('/admin/imports/{kind}/preview/{token}/coordinates')
    async def geocoded(request: Request, kind: str, token: str):
        if response := guard(request):
            return response
        kind_check(kind)
        form = await request.form(max_fields=1010)
        if not csrf(request, form):
            return HTMLResponse('CSRF 검증에 실패했습니다.', status_code=400)
        try:
            stage = store.read(token, owner(request), kind)
            with closing(connect_db()) as conn:
                conn.execute('BEGIN')
                if state_hash(conn) != stage['state_hash'] or conn.execute('SELECT 1 FROM csv_import_runs WHERE token=?', (token,)).fetchone():
                    raise ValueError('미리보기가 변경되었습니다. 다시 업로드해주세요.')
                for row in stage['rows']:
                    lat, lng = form.get('lat_' + str(row['line'])), form.get('lng_' + str(row['line']))
                    if row.get('needs_coordinates') and not row['errors'] and lat and lng:
                        lat, lng = coordinates(lat, lng)
                        row['raw']['위도'], row['raw']['경도'] = str(lat), str(lng)
                stage['rows'] = preview_rows(conn, stage['rows'], kind, root)
            store.write(stage)
        except ValueError as error:
            flash(request, str(error), 'error')
        return RedirectResponse(f'/admin/imports/{kind}/preview/{token}', status_code=303)

    @app.post('/admin/imports/{kind}/preview/{token}/hours')
    async def correct_hours(request: Request, kind: str, token: str):
        if response := guard(request):
            return response
        kind_check(kind)
        form = await request.form(max_fields=5)
        if not csrf(request, form):
            return HTMLResponse('CSRF 검증에 실패했습니다.', status_code=400)
        try:
            stage = store.read(token, owner(request), kind)
            row = next((r for r in stage['rows'] if str(r['line']) == form.get('line')), None)
            if row is None:
                raise ValueError('수정할 행을 찾을 수 없습니다.')
            status = form.get('hours_status')
            if status not in ('시간 입력', '미확인'):
                raise ValueError('영업시간 상태를 선택해주세요.')
            row['raw'].update({'영업시간_상태': status,
                               '영업_시작': '' if status == '미확인' else str(form.get('hours_start', ''))[:80],
                               '영업_종료': '' if status == '미확인' else str(form.get('hours_end', ''))[:80]})
            with closing(connect_db()) as conn:
                conn.execute('BEGIN')
                if state_hash(conn) != stage['state_hash'] or conn.execute('SELECT 1 FROM csv_import_runs WHERE token=?', (token,)).fetchone():
                    raise ValueError('미리보기가 변경되었거나 이미 저장되었습니다. 다시 업로드해주세요.')
                prepared = preview_rows(conn, stage['rows'], kind, root)
            # New immutable preview: a second tab cannot commit edited rows unseen.
            stage = store.create(owner(request), kind, prepared, stage['state_hash'])
            token = stage['token']
        except ValueError as error:
            flash(request, str(error), 'error')
        return RedirectResponse(f'/admin/imports/{kind}/preview/{token}', status_code=303)

    @app.post('/admin/imports/{kind}/preview/{token}/commit')
    async def commit(request: Request, kind: str, token: str):
        if response := guard(request):
            return response
        kind_check(kind)
        form = await request.form(max_fields=1010)
        if not csrf(request, form):
            return HTMLResponse('CSRF 검증에 실패했습니다.', status_code=400)
        try:
            stage = store.read(token, owner(request), kind)
            if form.get('confirm') != '1':
                raise ValueError('선택한 작업과 공개 여부를 확인한 후 등록 확인을 체크해주세요.')
            choices = {str(row['line']): (form.get('action_' + str(row['line']), 'skip'), form.get('target_' + str(row['line']), '')) for row in stage['rows']}
            if not any(action != 'skip' for action, _ in choices.values()):
                raise ValueError('등록 또는 업데이트할 행을 선택해주세요.')
            await run_in_threadpool(save_rows, app.state.database, connect_db, stage, choices, actor(request))
        except ValueError as error:
            flash(request, str(error), 'error')
        except Exception:
            # No raw cells, credentials or filesystem paths in the user-facing error.
            import logging
            logging.getLogger(__name__).exception('CSV import transaction failed')
            flash(request, '저장을 완료하지 못했습니다. 이번 작업은 모두 취소되었습니다. 잠시 후 다시 확인해주세요.', 'error')
        return RedirectResponse(f'/admin/imports/{kind}/preview/{token}', status_code=303)
