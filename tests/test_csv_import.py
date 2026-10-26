"""CSV contracts, authorization, preservation, atomicity and shared-site integration."""
import csv
import io
import json
import re
import sqlite3
import zipfile
import xml.etree.ElementTree as ET
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import test_community
from auth_security import hash_password
from csv_place_import import HEADERS, MAX_BYTES, parse_csv, export_csv, prepare_row


class CsvImportTest(unittest.TestCase):
    setUp = test_community.CommunityTest.setUp
    tearDown = test_community.CommunityTest.tearDown
    rows = test_community.CommunityTest.rows
    execute = test_community.CommunityTest.execute
    csrf = test_community.CommunityTest.csrf

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('community-test-secret')

    def row(self, kind='cafe', **changes):
        row = dict.fromkeys(HEADERS[kind], '')
        row.update({'장소명': 'CSV 검증 장소', '지역': '애월', '주소': '제주특별자치도 제주시 애월읍 검증로 1',
                    '위도': '33.46', '경도': '126.31', 'NEW_노출': '숨김'})
        row.update({'카페_특징': '커피 전문|베이커리', '커피맛_특징': '고소한 편'} if kind == 'cafe' else {'맛집_대분류': '일식', '맛집_세부유형': '돈가스', '음식맛_특징': '담백한 맛'})
        row.update(changes)
        return row

    def upload(self, rows, kind='cafe', raw=None, client=None):
        client = client or self.admin
        response = client.post(f'/admin/imports/{kind}/preview', data={'csrf_token': self.csrf(client, '/admin')},
            files={'csv_file': ('input.csv', raw if raw is not None else export_csv(HEADERS[kind], rows), 'text/csv')}, follow_redirects=False)
        return response

    def preview(self, rows, kind='cafe'):
        response = self.upload(rows, kind)
        self.assertEqual(response.status_code, 303, response.text[:400])
        path = response.headers['location']
        return path, self.admin.get(path)

    def commit(self, path, **choices):
        return self.admin.post(path + '/commit', data={'csrf_token': self.csrf(self.admin, '/admin'), 'confirm': '1', **choices})

    def test_downloads_and_access_csrf(self):
        for kind in HEADERS:
            path = f'/admin/imports/{kind}/template.csv'
            response = self.admin.get(path)
            self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
            self.assertEqual(next(csv.reader(io.StringIO(response.content.decode('utf-8-sig')))), HEADERS[kind])
            for client in (self.visitor, self.member):
                self.assertEqual(client.get(path, follow_redirects=False).status_code, 303)
                self.assertEqual(client.post(f'/admin/imports/{kind}/preview', follow_redirects=False).status_code,303)
        self.assertEqual(self.admin.post('/admin/imports/cafe/preview').status_code,400)

    def test_four_cafe_rows_preview_and_save(self):
        first = self.row(잘맞는동행='연인|친구', 영업_시작='10:15', 영업_종료='18:45', 휴무일='월|화', 전화번호='0641234567', 대표메뉴_가격='5500', 꿀팁='뒤편 주차')
        path, page = self.preview([first, self.row(장소명='두 번째 카페', 주소='다른 주소'), first, self.row(카페_특징='존재하지 않는 카페 특징')])
        self.assertIn('중복 가능', page.text)
        self.assertIn('활성 분류에 없습니다', page.text)
        self.assertEqual(len(self.rows('SELECT * FROM places')), 0)
        result = self.commit(path, action_2='create', action_3='create')
        self.assertIn('등록 완료', result.text)
        saved = self.rows('SELECT * FROM places ORDER BY id')
        self.assertEqual(len(saved), 2)
        p = saved[0]
        self.assertEqual((p['category'],p['published'],p['business_hours'],p['closed_days'],p['note'],p['representative_price']), ('카페',0,'10:15 ~ 18:45','월요일 · 화요일','뒤편 주차',5500))
        self.assertEqual(json.loads(p['companions']), ['연인','친구'])
        self.assertEqual(p['phone'], '064-123-4567')
        self.assertEqual(len(self.rows('SELECT * FROM place_category_links')), 4)
        self.assertTrue(list((Path(self.database).parent / 'backups').glob('*cafe-csv-import*.db')))
        self.commit(path, action_2='create', action_3='create')
        self.assertEqual(len(self.rows('SELECT * FROM places')), 2)

    def test_food_four_rows_and_parent_relation(self):
        first = self.row('restaurant')
        path, page = self.preview([first, self.row('restaurant', 장소명='국수집', 주소='다른 주소', 맛집_대분류='한식', 맛집_세부유형='국수·면'), first,
                                  self.row('restaurant', 맛집_대분류='한식', 맛집_세부유형='돈가스')], 'restaurant')
        self.assertIn('관계가 올바르지 않습니다', page.text)
        self.commit(path, action_2='create', action_3='create')
        self.assertEqual([r['category'] for r in self.rows('SELECT category FROM places')], ['맛집','맛집'])
        self.assertTrue(list((Path(self.database).parent / 'backups').glob('*restaurant-csv-import*.db')))

    def test_wrong_templates_and_invalid_files(self):
        for kind, other in [('cafe','restaurant'), ('restaurant','cafe')]:
            with self.assertRaisesRegex(ValueError, 'CSV 파일로 보입니다'):
                parse_csv(export_csv(HEADERS[other], [self.row(other)]), 'x.csv','text/csv',kind)
        invalid = [(b'', 'x.csv','text/csv'), (b'name\nx','x.csv','text/csv'),
                   ('한글'.encode('cp949'),'x.csv','text/csv'), (b'\x00','x.csv','text/csv'),
                   (b'a'*(MAX_BYTES+1),'x.csv','text/csv'), (b'a','x.xlsx','text/csv'), (b'a','x.csv','image/jpeg')]
        for raw,name,mime in invalid:
            with self.subTest(name=name, length=len(raw)), self.assertRaises(ValueError):
                parse_csv(raw,name,mime,'cafe')
        valid = export_csv(HEADERS['cafe'], [self.row()])
        self.assertEqual(len(parse_csv(valid[3:], 'x.csv','application/vnd.ms-excel','cafe')),1)
        with self.assertRaisesRegex(ValueError,'500'):
            parse_csv(export_csv(HEADERS['cafe'], [self.row()]*501),'x.csv','text/csv','cafe')

    def test_validation_and_error_csv_injection(self):
        row = self.row(장소명='=HYPERLINK("https://example.com")', 카페_특징='예쁜카페')
        out = io.StringIO(newline=''); writer = csv.DictWriter(out,fieldnames=HEADERS['cafe']); writer.writeheader(); writer.writerow(row)
        response = self.upload([], raw=out.getvalue().encode())
        path = response.headers['location']
        error = self.admin.get(path+'/errors.csv')
        self.assertTrue(error.content.startswith(b'\xef\xbb\xbf'))
        decoded = list(csv.DictReader(io.StringIO(error.content.decode('utf-8-sig'))))[0]
        self.assertTrue(decoded['장소명'].startswith("'="))
        self.commit(path, action_2='create')
        self.assertEqual(len(self.rows('SELECT * FROM places')),0)

    def test_update_preserves_empty_and_excluded_fields(self):
        path, _ = self.preview([self.row(공개여부='공개', 대표사진='https://example.com/photo.jpg', 꿀팁='보존할 꿀팁')])
        self.commit(path, action_2='create')
        p = self.rows('SELECT * FROM places')[0]
        excluded = {'one_line':'한 줄', 'reason':'추천 이유', 'reservation_status':'가능', 'waiting_status':'있음',
                    'info_source':'직접 확인', 'checked_at':'2026-10-01', 'cafe_visit_times':'["오전"]', 'dessert_note':'디저트'}
        self.execute('UPDATE places SET '+','.join(k+'=?' for k in excluded)+' WHERE id=?', (*excluded.values(),p['id']))
        raw = dict.fromkeys(HEADERS['cafe'],''); raw.update(place_id=str(p['id']), 전화번호='0649998888')
        path, page = self.preview([raw])
        self.assertIn('중복 가능',page.text)
        self.commit(path, action_2='update', target_2=str(p['id']))
        new = self.rows('SELECT * FROM places')[0]
        for field in (*excluded, 'name','address','region','published','note','latitude'):
            self.assertEqual(new[field],excluded.get(field,p[field]),field)
        self.assertEqual(new['phone'],'064-999-8888')
        self.assertEqual(self.rows('SELECT url FROM place_image_references')[0]['url'],'https://example.com/photo.jpg')
        self.assertEqual(len(self.rows('SELECT * FROM place_category_links')),2)

    def test_stale_preview_rejected_and_wrong_target(self):
        path,_=self.preview([self.row()])
        self.execute("UPDATE categories SET name=name||'변경' WHERE slug='cafe-feature-coffee'")
        # Force a meaningful state change regardless of taxonomy slug migrations.
        self.execute("UPDATE categories SET sort_order=sort_order+1 WHERE type='place'")
        response=self.commit(path,action_2='create')
        self.assertIn('다시 업로드',response.text)
        self.assertEqual(len(self.rows('SELECT * FROM places')),0)

    def test_atomic_rollback(self):
        path,_=self.preview([self.row(),self.row(장소명='다음 장소',주소='두 번째 주소')])
        calls=0
        def failing(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2: raise RuntimeError('Injected storage failure')
            return prepare_row(*args,**kwargs)
        with patch('csv_place_import.prepare_row',side_effect=failing):
            self.commit(path,action_2='create',action_3='create')
        self.assertEqual(len(self.rows('SELECT * FROM places')),0)
        self.assertEqual(len(self.rows('SELECT * FROM csv_import_runs')),0)
        self.commit(path,action_2='create',action_3='create')
        self.assertEqual(len(self.rows('SELECT * FROM places')),2)

    def test_public_integration_new_pick_photos(self):
        for kind in HEADERS:
            path,_=self.preview([self.row(kind,장소명='공개 '+kind,주소='주소 '+kind,공개여부='공개',NEW_노출='자동',오픈연도=str(self.now.year),오픈월=str(self.now.month),
                JEJU_KNOW_PICK='예',PICK_연도=str(self.now.year),PICK_월=str(self.now.month),대표사진='https://example.com/one.jpg',추가사진1='https://example.com/two.jpg')],kind)
            self.commit(path,action_2='create')
        saved=self.rows('SELECT * FROM places')
        for p in saved:
            for url in ('/admin/places','/find','/new','/map','/api/places'):
                client=self.admin if url.startswith('/admin') else self.visitor
                self.assertEqual(client.get(url).status_code,200)
            detail=self.visitor.get('/place/'+p['slug'])
            self.assertEqual(detail.status_code,200,detail.text[:120])
            self.assertIn('https://example.com/two.jpg',detail.text)
            self.assertIn(p['name'],self.visitor.get('/api/places?category='+('cafe' if p['category']=='카페' else 'food')).text)
        self.assertEqual(len(self.rows('SELECT * FROM place_pick_periods WHERE is_active=1')),2)

    def test_invalid_values_no_guesses(self):
        cases=[dict(카페_특징='커피맛집'),dict(커피맛_특징='산미 최고'),dict(잘맞는동행='아무나'),dict(지역='서울'),dict(위도='NaN'),
               dict(영업_종료='',영업_시작='10:00'),dict(주차='있음'),dict(홈페이지_인스타그램='javascript:alert(1)'),dict(대표사진='/etc/passwd'),
               dict(place_id='99999'),dict(JEJU_KNOW_PICK='예'),dict(대표메뉴_가격='비쌈')]
        path,page=self.preview([self.row(**values) for values in cases])
        text=page.text
        self.assertEqual(text.count('class="csv-state">오류'),len(cases))
        self.assertEqual(len(self.rows('SELECT * FROM places')),0)

    def test_preview_owner_and_expiry(self):
        path,_=self.preview([self.row()])
        self.assertEqual(self.member.get(path,follow_redirects=False).status_code,303)
        token=path.rsplit('/',1)[-1]
        filename=Path(self.database).parent/'csv-import-previews'/(token+'.json')
        data=json.loads(filename.read_text(encoding='utf-8'));data['expires']=0;filename.write_text(json.dumps(data),encoding='utf-8')
        self.assertEqual(self.admin.get(path).status_code,400)

    def test_workbook_current_taxonomy_and_columns(self):
        self.execute("UPDATE categories SET name='새 카페 특징' WHERE type='cafe_feature' AND name='커피 전문'")
        response=self.admin.get('/admin/imports/template.xlsx')
        self.assertEqual(response.status_code,200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn('새 카페 특징',archive.read('xl/worksheets/sheet4.xml').decode())
            book=ET.fromstring(archive.read('xl/workbook.xml'))
            ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            self.assertEqual([s.get('name') for s in book.find('x:sheets',ns)],['카페DB','맛집DB','입력가이드','선택목록'])
            self.assertGreater(len(book.find('x:definedNames',ns)),10)

    def test_photo_references_manual_form_and_private_note(self):
        path,_=self.preview([self.row(공개여부='공개',비고='관리자 비공개 메모',대표사진='https://example.com/photo.jpg')])
        self.commit(path,action_2='create')
        p=self.rows('SELECT * FROM places')[0]
        page=self.admin.get(f"/admin/places/{p['id']}/edit")
        self.assertEqual(page.status_code,200)
        self.assertIn('관리자 비공개 메모',page.text)
        self.assertNotIn('관리자 비공개 메모',self.visitor.get('/api/places').text)
        update=dict.fromkeys(HEADERS['cafe'],'');update.update(place_id=str(p['id']),대표사진='https://example.com/other.jpg')
        _,page=self.preview([update])
        self.assertIn('기존 사진을 보호',page.text)

    def test_geocode_only_updates_preview_and_validates_pair(self):
        path,_=self.preview([self.row(위도='',경도='')])
        response=self.admin.post(path+'/coordinates',data={'csrf_token':self.csrf(self.admin,'/admin'),'lat_2':'33.45','lng_2':'126.33'})
        self.assertNotIn('지도 위치 미등록:',response.text)
        self.assertEqual(len(self.rows('SELECT * FROM places')),0)
        self.commit(path,action_2='create')
        self.assertEqual(self.rows('SELECT latitude FROM places')[0]['latitude'],33.45)

    def test_internal_photo_reuse_keeps_original_file(self):
        from PIL import Image
        image=io.BytesIO();Image.new('RGB',(20,20),'blue').save(image,'PNG')
        response=self.admin.post('/admin/places/new',data={'csrf_token':self.csrf(self.admin,'/admin'), 'name':'사진 원본', 'region':'애월','category_id':str(self.category['cafe'])},
            files={'image_0':('original.png',image.getvalue(),'image/png')},follow_redirects=False)
        self.assertEqual(response.status_code,303)
        image_row=self.rows('SELECT * FROM images')[0]
        path,_=self.preview([self.row(대표사진='/uploads/'+image_row['file_path'])])
        self.commit(path,action_2='create')
        self.assertEqual(len(self.rows('SELECT * FROM images')),2)
        self.assertTrue((Path(self.database).parent/'uploads'/image_row['file_path']).is_file())
        self.assertEqual(self.rows('SELECT * FROM images WHERE id=?',(image_row['id'],))[0],image_row)

