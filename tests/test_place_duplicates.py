"""Conservative matching, decision persistence and real registration routes."""
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

import test_community as base
import test_csv_import as csv_base
from place_duplicates import (compare, calculate_place_distance, normalize_place_name,
    normalize_address, normalize_phone, groups, find_duplicate_candidates, migrate)


class DuplicateTest(unittest.TestCase):
    setUp = base.CommunityTest.setUp
    tearDown = base.CommunityTest.tearDown
    setUpClass = classmethod(base.CommunityTest.setUpClass.__func__)
    rows = base.CommunityTest.rows
    execute = base.CommunityTest.execute
    csrf = base.CommunityTest.csrf
    post = base.CommunityTest.post
    form = base.CommunityTest.form
    create = base.CommunityTest.create
    row = csv_base.CsvImportTest.row
    upload = csv_base.CsvImportTest.upload
    preview = csv_base.CsvImportTest.preview
    commit = csv_base.CsvImportTest.commit

    def connect(self):
        conn=sqlite3.connect(self.database);conn.row_factory=sqlite3.Row
        return conn

    def fixture(self, **changes):
        n=len(self.rows('SELECT id FROM places'))+1
        values=dict(slug=f'candidate-{n}',name='돈까스가 있는 풍경',address='제주 제주시 정원로 39-1',
                    region='제주시',category='카페',category_id=self.category['cafe'],published=1,
                    latitude=33.4783531,longitude=126.4780063,phone='064-713-0541',**{})
        values.update(changes)
        return self.execute('INSERT INTO places('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values()))

    def test_normalization_retains_original_branch_unit_and_punctuation(self):
        a=' Cafe  바람 제주점 ';self.assertEqual(normalize_place_name(a),'cafe바람제주점')
        self.assertNotEqual(normalize_place_name(a),normalize_place_name('Cafe 바람 서귀포점'))
        self.assertNotEqual(normalize_place_name('A-B'),normalize_place_name('AB'))
        self.assertEqual(normalize_address('제주특별자치도 제주시 정원로 39-1'),normalize_address('제주 제주시 정원로 39-1'))
        self.assertNotEqual(normalize_address('제주시 정원로 39-1 101호'),normalize_address('제주시 정원로 39-1 102호'))
        self.assertEqual(normalize_phone('064-713-0541'),'0647130541')
        self.assertEqual(normalize_phone('미확인'),'')

    def test_thresholds_require_combined_evidence(self):
        a=dict(name='카페',address='제주시 A로 1',category_id=1,region='애월',latitude=33.4,longitude=126.4)
        self.assertEqual(compare(a,{**a,'name':' 카 페 '})['level'],'high')
        self.assertEqual(compare(a,{**a,'address':'다른 주소','latitude':33.4005})['level'],'high')
        self.assertEqual(compare(a,{**a,'address':'다른 주소','latitude':33.6})['level'],'low')
        self.assertIsNone(compare(a,{**a,'address':'다른 주소','latitude':33.6,'region':'성산'}))
        self.assertEqual(compare(a,{**a,'name':'다른 가게'})['level'],'medium')
        self.assertIsNone(compare(a,{**a,'name':'다른 가게','address':'다른 주소'}))
        self.assertEqual(compare({**a,'phone':'064-123-4567'},{**a,'name':'완전히 다른 상호','phone':'0641234567','address':'B'})['level'],'high')
        self.assertIsNone(compare({**a,'phone':'0641234567'},{'name':'다름','phone':'0641234567','category_id':2}))
        self.assertIsNone(calculate_place_distance(a,{**a,'latitude':float('nan')}))
        self.assertGreater(calculate_place_distance(a,{**a,'latitude':33.401}),100)

    def test_groups_cache_refresh_and_excluded_deleted(self):
        a,b,c=[self.fixture(name='돈까스가있는풍경') for _ in range(3)]
        self.fixture(name='돈까스가있는풍경',deleted_at='2026-01-01')
        self.fixture(name='돈까스가있는풍경',merged_into_id=a)
        with closing(self.connect()) as conn:
            first=groups(conn);self.assertIs(first,groups(conn))
            self.assertEqual(len(first),1);self.assertEqual(len(first[0]['places']),3)
        self.execute('UPDATE places SET name=?,address=?,phone=?,region=?,latitude=NULL,longitude=NULL WHERE id=?',('다른 이름','다른 주소','','성산',c))
        with closing(self.connect()) as conn:self.assertEqual(len(groups(conn)[0]['places']),2)
        self.assertEqual(len(self.rows('SELECT * FROM places')),5)

    def test_admin_compare_full_fields_escaping_and_no_mutation(self):
        a=self.fixture(name='<script>상호</script>');b=self.fixture(phone='',published=0)
        before=self.rows('SELECT * FROM places ORDER BY id')
        response=self.admin.get(f'/admin/places/duplicates/compare/{a}/{b}')
        self.assertEqual(response.status_code,200)
        self.assertIn('&lt;script&gt;상호&lt;/script&gt;',response.text)
        self.assertIn('duplicate-different',response.text)
        for title in ['전화번호','직선거리','맛집 대분류','대표메뉴','PICK 기록','수정 제안','맛집 랭킹']:
            self.assertIn(title,response.text)
        for choice in ['keep_left','keep_right']:
            self.assertEqual(self.post(self.admin,f'/admin/places/duplicates/compare/{a}/{b}',decision=choice).status_code,303)
        self.assertEqual(before,self.rows('SELECT * FROM places ORDER BY id'))
        self.assertEqual(len(self.rows("SELECT * FROM admin_activity_logs WHERE action='유지할 장소 선택'")),2)

    def test_separate_is_canonical_persistent_and_restorable(self):
        a,b=self.fixture(),self.fixture()
        url=f'/admin/places/duplicates/compare/{b}/{a}'
        for _ in range(2):self.assertEqual(self.post(self.admin,url,decision='separate').status_code,303)
        self.assertEqual(len(self.rows('SELECT * FROM place_duplicate_ignores')),1)
        self.assertEqual((self.rows('SELECT * FROM place_duplicate_ignores')[0]['place_low']),a)
        with closing(self.connect()) as conn:
            self.assertEqual(groups(conn),[])
            self.assertEqual(find_duplicate_candidates(conn,dict(conn.execute('SELECT * FROM places WHERE id=?',(a,)).fetchone()),a,include_private=True),[])
        self.assertIn('별개 장소로 확인',self.admin.get(url).text)
        self.assertEqual(self.post(self.admin,url,decision='restore').status_code,303)
        with closing(self.connect()) as conn:self.assertEqual(len(groups(conn)),1)

    def test_admin_list_filter_badges_dashboard(self):
        self.fixture();self.fixture();self.fixture(name='별도',address='서귀포시 다른로 1',phone='',region='성산')
        response=self.admin.get('/admin/places?duplicate=suspected')
        self.assertEqual(response.context['pagination']['total'],2)
        self.assertIn('중복 의심 2곳',response.text);self.assertIn('중복 비교',response.text)
        self.assertEqual(self.admin.get('/admin/places?duplicate=clear').context['pagination']['total'],1)
        self.assertEqual(self.admin.get('/admin').context['admin_counts']['duplicates'],1)

    def test_admin_new_warning_and_explicit_override(self):
        self.fixture(name='관리자 비교')
        data=self.form(name='관리자 비교',region='제주시',address='제주 제주시 정원로 39-1',slug='admin-new')
        response=self.post(self.admin,'/admin/places/new',**data)
        self.assertEqual(response.status_code,409,response.text[:400])
        self.assertEqual(len(self.rows('SELECT * FROM places')),1)
        response=self.post(self.admin,'/admin/places/new',**data,duplicate_ack='1')
        self.assertEqual(response.status_code,303,response.text[:400])
        self.assertEqual(len(self.rows('SELECT * FROM places')),2)

    def test_member_public_minimal_api_private_server_warning_and_override(self):
        a=self.fixture(name='공개 후보');self.fixture(name='공개 후보',published=0,slug='private-secret')
        self.fixture(name='공개 후보',is_example=1,slug='example-secret')
        data=self.form(name='공개 후보',region='제주시',address='제주 제주시 정원로 39-1')
        response=self.post(self.member,'/places/duplicate-check',**data)
        self.assertEqual(response.status_code,200,response.text)
        candidates=response.json()['candidates'];self.assertEqual(len(candidates),1)
        self.assertEqual(set(candidates[0]),{'name','image_url','region','category','url'})
        self.assertNotIn('private-secret',response.text)
        warning=self.create(**data);self.assertEqual(warning.status_code,409)
        self.assertIn('혹시 이 장소인가요?',warning.text)
        self.assertNotIn('private-secret',warning.text)
        self.assertNotIn('example-secret',warning.text)
        response=self.create(**data,duplicate_ack='1');self.assertEqual(response.status_code,303,response.text[:400])
        saved=self.rows('SELECT * FROM places ORDER BY id DESC')[0]
        self.assertEqual((saved['published'],saved['source_type'],saved['verification_status']),(0,'user','unverified'))

    def test_permissions_csrf_cross_owner_and_oversized_id(self):
        a,b=self.fixture(),self.fixture()
        path=f'/admin/places/duplicates/compare/{a}/{b}'
        for client in [self.member,self.visitor]:
            self.assertEqual(client.get(path,follow_redirects=False).status_code,303)
            self.assertEqual(client.post(path,data={'decision':'separate'},follow_redirects=False).status_code,303)
        self.assertEqual(self.admin.post(path,data={'decision':'separate'}).status_code,400)
        self.assertEqual(self.visitor.post('/places/duplicate-check').status_code,401)
        self.assertEqual(self.member.post('/places/duplicate-check').status_code,400)
        self.assertEqual(self.post(self.member,'/places/duplicate-check',exclude_id=str(a)).status_code,403)
        self.assertEqual(self.post(self.admin,'/admin/places/duplicate-check',exclude_id='9'*80).status_code,400)
        self.execute("INSERT INTO admins(username,password_hash,role) VALUES('other-super',?,'super_admin')",(self.password_hash,))
        self.execute("UPDATE admins SET role='sub_admin' WHERE username='admin'")
        self.assertEqual(self.admin.get('/admin/places/duplicates').status_code,200)
        self.assertEqual(self.post(self.admin,path,decision='separate').status_code,303)
        self.assertEqual(self.post(self.admin,f'/admin/places/{a}/delete').status_code,403)

    def test_csv_normalized_warning_comparison_explicit_save_and_stale(self):
        a=self.fixture(name='별 도 카페')
        raw=self.row(장소명='별도카페',주소='제주특별자치도 제주시 정원로 39-1')
        path,page=self.preview([raw]);self.assertIn('중복 의심',page.text)
        url=f'{path}/compare/2/{a}';comparison=self.admin.get(url)
        self.assertEqual(comparison.status_code,200,comparison.text[:400])
        self.assertIn('카테고리',comparison.text);self.assertIn('CSV 입력',comparison.text)
        self.assertEqual(self.member.get(url,follow_redirects=False).status_code,303)
        self.assertEqual(self.admin.get(f'{path}/compare/2/999').status_code,400)
        unchanged=self.rows('SELECT * FROM places')
        self.assertEqual(len(unchanged),1)
        self.execute('UPDATE places SET phone=? WHERE id=?',('064-234-5678',a))
        self.assertEqual(self.admin.get(url).status_code,400)
        path,_=self.preview([raw]);result=self.commit(path,action_2='create')
        self.assertIn('등록 완료',result.text);self.assertEqual(len(self.rows('SELECT * FROM places')),2)

    def test_migration_backup_idempotency_and_data_preservation(self):
        self.fixture();before=self.rows('SELECT * FROM places')
        migrate(self.database);migrate(self.database)
        self.assertEqual(before,self.rows('SELECT * FROM places'))
        receipts=self.rows("SELECT * FROM schema_migrations WHERE version='20261116_place_duplicate_ignores'")
        self.assertEqual(len(receipts),1)
        with closing(sqlite3.connect(receipts[0]['backup_path'])) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='place_duplicate_ignores'").fetchone())

    def test_manual_delete_cascades_only_ignore_relation(self):
        a,b=self.fixture(),self.fixture()
        self.post(self.admin,f'/admin/places/duplicates/compare/{a}/{b}',decision='separate')
        response=self.post(self.admin,f'/admin/places/{a}/delete')
        self.assertEqual(response.status_code,303)
        self.assertEqual(self.rows('SELECT * FROM place_duplicate_ignores'),[])
        self.assertEqual([p['id'] for p in self.rows('SELECT * FROM places')],[b])


if __name__=='__main__':unittest.main()
