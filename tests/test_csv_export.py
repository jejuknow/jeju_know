"""Operational CSV round trips, read-only downloads and authorization."""
import csv
import io
import json
import sqlite3
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

from auth_security import hash_password
from csv_place_export import export_places
from csv_place_import import EXPORT_HEADERS, HEADERS, export_csv, parse_csv
from test_csv_import import CsvImportTest


class CsvExportTest(unittest.TestCase):
    setUp = CsvImportTest.setUp
    tearDown = CsvImportTest.tearDown
    rows = CsvImportTest.rows
    execute = CsvImportTest.execute
    csrf = CsvImportTest.csrf
    row = CsvImportTest.row
    upload = CsvImportTest.upload
    preview = CsvImportTest.preview
    commit = CsvImportTest.commit

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('community-test-secret')

    def create(self, kind='cafe', **values):
        path, page = self.preview([self.row(kind, **values)], kind)
        self.assertFalse(page.context['stage']['rows'][0]['errors'])
        result = self.commit(path, action_2='create')
        self.assertIn('등록 완료', result.text)
        return self.rows('SELECT * FROM places ORDER BY id DESC LIMIT 1')[0]

    def download(self, kind='cafe'):
        response = self.admin.get(f'/admin/imports/{kind}/export.csv')
        self.assertEqual(response.status_code, 200)
        return response, list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))

    def snapshot(self):
        with closing(sqlite3.connect(self.database)) as conn:
            return '\n'.join(conn.iterdump())

    def test_download_contract_permissions_and_read_only(self):
        self.create()
        self.create('restaurant', 장소명='표선우동가게', 맛집_세부유형='돈가스|우동|소바')
        before = self.snapshot()
        for kind, label in [('cafe', '카페'), ('restaurant', '맛집')]:
            path = f'/admin/imports/{kind}/export.csv'
            for client in (self.visitor, self.member):
                self.assertEqual(client.get(path, follow_redirects=False).status_code, 303)
            with patch('csv_import_routes.today', return_value=date(2026, 11, 11)):
                response, rows = self.download(kind)
            self.assertEqual(list(rows[0]), EXPORT_HEADERS[kind])
            self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
            self.assertEqual(response.headers['content-type'], 'text/csv; charset=utf-8')
            self.assertIn(f"filename*=UTF-8''JEJU_KNOW_{label}_2026-11-11.csv", unquote(response.headers['content-disposition']))
            self.assertEqual(response.headers['cache-control'], 'no-store')
            for excluded in ('place_id', '대표사진', '추가사진1', '예약', '웨이팅', 'password_hash', 'user_id'):
                self.assertNotIn(excluded, rows[0])
        self.assertEqual(before, self.snapshot())
        self.execute("INSERT INTO admins(username,password_hash,role) VALUES('sub',?,'sub_admin')", (self.password_hash,))
        self.other.post('/admin/login', data={'csrf_token': self.csrf(self.other, '/admin/login'),
                        'username': 'sub', 'password': 'community-test-secret'})
        for kind in ('cafe', 'restaurant'):
            self.assertEqual(self.other.get(f'/admin/imports/{kind}/export.csv').status_code, 403)
        self.assertEqual(self.admin.get('/admin/imports/unknown/export.csv').status_code, 404)

    def test_filters_nulls_choices_and_current_taxonomy(self):
        cafe = self.create(카페_특징='커피 전문|조용한 공간|작업하기 좋은', 주차='가능', 반려동물='불가', 아이동반='제한',
                           잘맞는동행='혼자|친구|부모님', 영업_시작='9:30', 영업_종료='18:00', 휴무일='월|화', 공개여부='공개')
        private = self.create(장소명='비공개 카페', 주소='다른 주소')
        deleted = self.create(장소명='삭제 장소', 주소='삭제 주소')
        merged = self.create(장소명='병합 장소', 주소='병합 주소')
        self.execute('UPDATE places SET deleted_at=CURRENT_TIMESTAMP WHERE id=?', (deleted['id'],))
        self.execute('UPDATE places SET merged_into_id=? WHERE id=?', (cafe['id'], merged['id']))
        self.create('restaurant', 장소명='다른 종류')
        _, rows = self.download()
        self.assertEqual([r['장소명'] for r in rows], [cafe['name'], private['name']])
        row = rows[0]
        self.assertEqual(set(row['카페_특징'].split('|')), {'커피 전문', '조용한 공간', '작업하기 좋은'})
        self.assertEqual((row['영업시간_상태'], row['영업_시작'], row['영업_종료'], row['휴무일']), ('시간 입력', '09:30', '18:00', '월|화'))
        self.assertEqual((row['주차'], row['반려동물'], row['아이동반'], row['무장애']), ('가능', '불가', '제한', '미확인'))
        self.assertEqual(row['잘맞는동행'], '혼자|친구|부모님')
        self.assertEqual(row['커피맛_특징'], '고소한 편')
        self.assertEqual(rows[1]['공개여부'], '비공개')
        self.assertEqual(rows[1]['오픈월'], '')
        self.assertFalse(any(v in ('None', 'null', 'undefined') for row in rows for v in row.values()))

    def test_download_edit_preview_and_explicit_update(self):
        for kind in ('restaurant', 'cafe'):
            saved = self.create(kind, 장소명=kind+' 장소', 맛집_세부유형='돈가스|우동|소바', 대표메뉴='기존 메뉴',
                                오픈연도='2026', 오픈월='10', NEW_노출='자동', 공개여부='공개')
            self.execute("UPDATE places SET reservation_status='가능',waiting_status='있음',image_url='https://example.com/keep.jpg' WHERE id=?", (saved['id'],))
            response, rows = self.download(kind)
            if kind == 'restaurant':
                self.assertEqual(rows[0]['맛집_대분류'], '일식')
                self.assertEqual(rows[0]['맛집_세부유형'], '돈가스|우동|소바')
            before = self.snapshot()
            for payload in (response.content, export_csv(EXPORT_HEADERS[kind], [{**rows[0], '대표메뉴': '수정 메뉴'}])):
                uploaded = self.upload([], kind, raw=payload)
                self.assertEqual(uploaded.status_code, 303)
                path = uploaded.headers['location']
                page = self.admin.get(path)
                item = page.context['stage']['rows'][0]
                self.assertFalse(item['errors'], item['errors'])
                self.assertEqual(item['default_action'], 'skip')
                self.assertTrue(any(r['id'] == saved['id'] and r['can_update'] for r in item['duplicates']))
                self.assertEqual(before, self.snapshot())
            self.commit(path, action_2='update', target_2=str(saved['id']))
            updated = self.rows('SELECT * FROM places WHERE id=?', (saved['id'],))[0]
            self.assertEqual(updated['signature_menu'], '수정 메뉴')
            self.assertEqual((updated['reservation_status'], updated['waiting_status'], updated['image_url']),
                             ('가능', '있음', 'https://example.com/keep.jpg'))
            self.assertEqual(updated['new_override'], 'auto')
            self.assertEqual((updated['opened_year'], updated['opened_month']), (2026, 10))

    def test_free_text_hours_and_unclassified_old_rows_are_preserved(self):
        p = self.create('restaurant')
        self.execute('DELETE FROM place_category_links WHERE place_id=?', (p['id'],))
        self.execute("UPDATE places SET business_hours='평일 09:00~18:00 / 주말 10:00~16:00',closed_days='매월 둘째 월요일',region='',address='' WHERE id=?", (p['id'],))
        response, rows = self.download('restaurant')
        self.assertEqual(rows[0]['영업시간_원문'], '평일 09:00~18:00 / 주말 10:00~16:00')
        self.assertEqual(rows[0]['영업시간_상태'], '')
        self.assertEqual(rows[0]['맛집_대분류'], '')
        page = self.admin.get(self.upload([], 'restaurant', raw=response.content).headers['location'])
        self.assertFalse(page.context['stage']['rows'][0]['errors'])
        self.commit(str(page.url).replace('http://testserver',''), action_2='update', target_2=str(p['id']))
        saved = self.rows('SELECT business_hours,closed_days FROM places WHERE id=?', (p['id'],))[0]
        self.assertEqual(saved, {'business_hours': rows[0]['영업시간_원문'], 'closed_days': rows[0]['휴무일']})
        rows[0]['영업시간_상태'] = '미확인'
        path = self.upload([], 'restaurant', raw=export_csv(EXPORT_HEADERS['restaurant'], rows)).headers['location']
        self.assertIn('한 방식만', self.admin.get(path).text)

    def test_formula_escape_is_reversible_including_literal_apostrophes(self):
        for value in ('=1+1', '+SUM(1,2)', '-2+3', '@SUM(A1)', '  =1+1', '\t=1+1', "'=literal", "'ordinary", "''=two", '쉼표, 따옴표 "와"\n여러 줄'):
            raw = export_csv(EXPORT_HEADERS['cafe'], [{'장소명': value}])
            cell = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))[0]['장소명']
            self.assertFalse(cell.lstrip().startswith(('=', '+', '-', '@')))
            parsed = parse_csv(raw, 'test.csv', 'text/csv', 'cafe')[0]['raw']['장소명']
            self.assertEqual(parsed, value)

    def test_pick_history_and_query_count(self):
        p = self.create()
        self.execute('INSERT INTO place_pick_periods(place_id,year,month,is_active) VALUES(?,2026,11,1)', (p['id'],))
        self.execute('INSERT INTO place_pick_periods(place_id,year,month,is_active) VALUES(?,2026,12,1)', (p['id'],))
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            queries = []
            conn.set_trace_callback(queries.append)
            raw = export_places(conn, 'cafe', on_date=date(2026,11,11))
            first_count = len(queries)
            rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
            self.assertEqual((rows[0]['JEJU_KNOW_PICK'], rows[0]['PICK_연도'], rows[0]['PICK_월']), ('예', '2026', '11'))
        for i in range(5):
            self.create(장소명=f'추가 {i}', 주소=f'추가 주소 {i}')
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            queries = []
            conn.set_trace_callback(queries.append)
            export_places(conn, 'cafe', on_date=date(2026,11,11))
            self.assertEqual(len(queries), first_count)
        self.assertEqual(len(self.rows('SELECT * FROM place_pick_periods')), 2)
