"""End-to-end participation, permissions, editorial selection and preservation tests."""
import io
import json
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from app import create_app
from auth_security import hash_password
from community_migrations import VERSION, backup_before_community_migration
from community_places import duplicates


class CommunityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('community-test-secret')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tmp.name)/'jejuno.db')
        self.env = patch.dict(os.environ, {'JEJUNO_SKIP_SEED':'1'})
        self.env.start()
        self.config = {'TESTING':True,'DATABASE':self.database,'SECRET_KEY':'community-test-only'}
        self.app = create_app(self.config)
        self.clients = [TestClient(self.app) for _ in range(4)]
        self.member,self.other,self.admin,self.visitor = self.clients
        for username in ('member','other'):
            self.execute('INSERT INTO users(username,password_hash,nickname) VALUES (?,?,?)',(username,self.password_hash,username))
        self.execute('INSERT INTO admins(username,password_hash) VALUES (?,?)',('admin',self.password_hash))
        for client,username,url in [(self.member,'member','/login'),(self.other,'other','/login'),(self.admin,'admin','/admin/login')]:
            r=client.post(url,data={'csrf_token':self.csrf(client,url),'username':username,'password':'community-test-secret'},follow_redirects=False)
            self.assertEqual(r.status_code,303)
        self.category={r['slug']:r['id'] for r in self.rows("SELECT * FROM categories WHERE type='place'")}
        self.now=date.today()

    def tearDown(self):
        for client in self.clients: client.close()
        self.env.stop()
        self.tmp.cleanup()

    def rows(self,sql,args=()):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory=sqlite3.Row
            return [dict(r) for r in conn.execute(sql,args)]

    def execute(self,sql,args=()):
        with closing(sqlite3.connect(self.database)) as conn,conn:
            return conn.execute(sql,args).lastrowid

    def csrf(self,client,path='/account'):
        page=client.get(path)
        token=re.search('name="csrf_token" value="([^"]+)"',page.text)
        self.assertIsNotNone(token, page.text[:250])
        return token[1]

    def post(self,client,path,**data):
        return client.post(path,data={'csrf_token':self.csrf(client,'/admin' if client is self.admin else '/account'),**data},follow_redirects=False)

    def form(self,**changes):
        return {'name':'테스트 커피','region':'애월','category_id':str(self.category['cafe']),'address':'제주 테스트로 123',**changes}

    def create(self,client=None,**data):
        return self.post(client or self.member,'/places/new',**self.form(**data))

    def place(self,**data):
        response=self.create(**data)
        self.assertEqual(response.status_code,303,response.text[:400])
        return self.rows('SELECT * FROM places ORDER BY id DESC')[0]

    def api(self,query=''):
        return self.visitor.get('/api/places'+query).json()

    def pick(self,p,**values):
        return self.post(self.admin,'/admin/picks',place_id=p['id'],year=self.now.year,month=self.now.month,sort_order='0',is_active='1',**values)

    def suggestion(self,p,**data):
        return self.post(self.member,f"/places/{p['id']}/suggest",field='name',new_value='바뀐 장소 이름',reason='현장 안내판 확인했습니다',**data)

    def test_01_common_menu_and_legacy_routes(self):
        for path in ('/','/find','/new','/news','/map','/best','/login','/signup','/partnership'):
            page=self.visitor.get(path)
            self.assertEqual(page.status_code,200,path)
            nav=re.search(r'<nav class="nav" id="siteNav".*?</nav>',page.text,re.S)[0]
            self.assertEqual(re.findall(r'>(FIND|NEW|ISSUE|MAP|BEST)</a>',nav),['FIND','NEW','ISSUE','MAP','BEST'])
            self.assertNotIn('PICK',nav);self.assertNotIn('제휴',nav)
        self.assertEqual(self.visitor.get('/pick',follow_redirects=False).headers['location'],'/map?pick=current')
        home=self.visitor.get('/').text
        for anchor in ('finder','pick','collections'): self.assertIn(f'id="{anchor}"',home)

    def test_02_anonymous_cannot_create_edit_delete_suggest(self):
        p=self.place()
        for path in ('/places/new',f"/places/{p['id']}/edit",f"/places/{p['id']}/delete",f"/places/{p['id']}/suggest"):
            r=self.visitor.post(path,data=self.form(),follow_redirects=False)
            self.assertEqual(r.status_code,303)
            self.assertTrue(r.headers['location'].startswith('/login?next='))
        self.assertEqual(len(self.api()),1)

    def test_03_create_immediate_public_unverified_not_recommended(self):
        p=self.place()
        result=self.api()[0]
        self.assertEqual(result['status_label'],'사용자 등록 · 미확인')
        self.assertFalse(result['is_pick']);self.assertEqual(p['reason'],'')
        self.assertEqual(self.rows('SELECT * FROM best_collection_places'),[])
        for key in ('parking_status','pet_status','child_status','accessible_status'): self.assertEqual(p[key],'미확인')
        self.assertNotIn('submitted_by_user_id',result)
        html=self.visitor.get('/place/'+p['slug']).text
        self.assertNotIn('JEJU_KNOW 추천 이유',html);self.assertIn('수정 제안하기',html)

    def test_04_required_and_length_validation(self):
        for data in ({'name':''},{'region':''},{'address':''},{'name':'가'*121},{'one_line':'가'*601},{'category_id':'9999'}):
            self.assertEqual(self.create(**data).status_code,400,data)
        self.assertEqual(self.api(),[])

    def test_05_unsafe_urls_and_coordinates_rejected(self):
        for data in ({'map_url':'javascript:alert(1)'},{'website_url':'data:text/html,evil'},{'latitude':'nan','longitude':'126'},{'latitude':'33'},{'latitude':'91','longitude':'126'}):
            self.assertEqual(self.create(**data).status_code,400,data)

    def test_06_forged_admin_fields_rejected(self):
        for key in ('is_pick','reason','note','verification_status','verified_at','submitted_by_user_id','source_type','published','new_override','is_sponsored','features'):
            self.assertEqual(self.create(**{key:'1'}).status_code,403,key)
        self.assertEqual(self.rows('SELECT * FROM places'),[])

    def test_07_owner_edit_allowed_and_other_denied(self):
        p=self.place()
        self.assertEqual(self.other.get(f"/places/{p['id']}/edit").status_code,403)
        self.assertEqual(self.post(self.other,f"/places/{p['id']}/edit",**self.form(name='탈취')).status_code,403)
        self.assertEqual(self.post(self.member,f"/places/{p['id']}/edit",**self.form(name='내 수정')).status_code,303)
        self.assertEqual(self.api()[0]['name'],'내 수정')

    def test_08_owner_delete_soft_and_other_denied(self):
        p=self.place()
        self.assertEqual(self.post(self.other,f"/places/{p['id']}/delete").status_code,403)
        self.assertEqual(self.post(self.member,f"/places/{p['id']}/delete").status_code,303)
        self.assertEqual(self.api(),[]);self.assertTrue(self.rows('SELECT deleted_at FROM places')[0]['deleted_at'])
        self.assertEqual(self.visitor.get('/place/'+p['slug']).status_code,404)

    def test_09_verified_owner_must_suggest(self):
        p=self.place()
        for status in ('jejuno_verified','community_confirmed'):
            self.execute('UPDATE places SET verification_status=? WHERE id=?',(status,p['id']))
            self.assertEqual(self.member.get(f"/places/{p['id']}/edit").status_code,403)
            self.assertEqual(self.post(self.member,f"/places/{p['id']}/edit",**self.form()).status_code,403)
            self.assertEqual(self.post(self.member,f"/places/{p['id']}/delete").status_code,403)
            self.assertEqual(self.suggestion(p).status_code,303)

    def test_10_csrf_enforced(self):
        p=self.place()
        for client,path in [(self.member,'/places/new'),(self.member,f"/places/{p['id']}/edit"),(self.member,f"/places/{p['id']}/delete"),(self.member,f"/places/{p['id']}/suggest"),(self.admin,'/admin/picks'),(self.admin,'/admin/best/1/places'),(self.admin,f"/admin/places/{p['id']}/merge"),(self.admin,'/admin/suggestions/1/review')]:
            self.assertEqual(client.post(path,data=self.form(),follow_redirects=False).status_code,400,path)

    def test_11_blocked_users_cannot_write(self):
        self.execute("UPDATE users SET status='blocked' WHERE username='member'")
        self.assertEqual(self.member.post('/places/new',data=self.form(),follow_redirects=False).status_code,303)

    def test_12_suggestion_does_not_change_original_then_accept(self):
        p=self.place();self.assertEqual(self.suggestion(p).status_code,303)
        self.assertEqual(self.api()[0]['name'],p['name'])
        s=self.rows('SELECT * FROM place_correction_suggestions')[0]
        self.assertEqual(self.post(self.admin,f"/admin/suggestions/{s['id']}/review",action='accept').status_code,303)
        self.assertEqual(self.api()[0]['name'],'바뀐 장소 이름')
        self.assertEqual(self.rows('SELECT status FROM place_correction_suggestions')[0]['status'],'accepted')
        self.assertEqual(self.post(self.admin,f"/admin/suggestions/{s['id']}/review",action='accept').status_code,409)

    def test_13_reject_preserves_original(self):
        p=self.place();self.suggestion(p)
        self.assertEqual(self.post(self.admin,'/admin/suggestions/1/review',action='reject').status_code,303)
        self.assertEqual(self.api()[0]['name'],p['name'])

    def test_14_stale_suggestion_cannot_overwrite_newer_fact(self):
        p=self.place();self.suggestion(p)
        self.execute("UPDATE places SET name='운영자 수정' WHERE id=?",(p['id'],))
        self.assertEqual(self.post(self.admin,'/admin/suggestions/1/review',action='accept').status_code,409)
        self.assertEqual(self.api()[0]['name'],'운영자 수정')

    def test_15_suggestion_validation_and_xss(self):
        p=self.place(name='<script>alert(1)</script>')
        html=self.visitor.get('/place/'+p['slug']).text
        self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>alert(1)</script>',html)
        for field,value in [('is_pick','1'),('category_id','999'),('website_url','javascript:evil'),('opening','99999'),('coordinates','nan,1')]:
            self.assertEqual(self.post(self.member,f"/places/{p['id']}/suggest",field=field,new_value=value,reason='확인 근거입니다').status_code,400)

    def test_16_suggestion_category_opening_coordinate_and_closure(self):
        p=self.place()
        for field,value in [('category_id',str(self.category['food'])),('opening',str(self.now.year)),('coordinates','33.42,126.55'),('closed','폐업 공지 확인')]:
            self.assertEqual(self.post(self.member,f"/places/{p['id']}/suggest",field=field,new_value=value,reason='공식 홈페이지에서 확인').status_code,303)
            sid=self.rows('SELECT id FROM place_correction_suggestions ORDER BY id DESC')[0]['id']
            self.assertEqual(self.post(self.admin,f'/admin/suggestions/{sid}/review',action='accept').status_code,303)
        r=self.rows('SELECT * FROM places')[0]
        self.assertEqual((r['category'],r['opened_year'],r['latitude'],r['published']),('맛집',self.now.year,33.42,0))

    def test_17_new_requires_year_and_food_or_cafe(self):
        self.assertEqual(self.create(from_new='1').status_code,400)
        self.assertEqual(self.create(from_new='1',opened_year=str(self.now.year),category_id=self.category['attraction']).status_code,400)
        for data in ({'opened_year':'9999'},{'opened_year':'2020','opened_month':'13'},{'opened_month':'1'}):
            self.assertEqual(self.create(**data).status_code,400)

    def test_18_new_uses_same_place_id_in_find_map_detail(self):
        p=self.place(from_new='1',opened_year=str(self.now.year),latitude='33.45',longitude='126.56')
        self.assertEqual([r['id'] for r in self.api('?new=current')],[p['id']])
        self.assertEqual([r['id'] for r in self.api('?category=cafe')],[p['id']])
        self.assertTrue(self.api()[0]['has_coordinates'])
        self.assertEqual(len(self.rows('SELECT * FROM places')),1)

    def test_19_new_current_year_not_hardcoded(self):
        p=self.place(opened_year=str(self.now.year))
        with patch('place_taxonomy.today',return_value=date(self.now.year+1,1,1)):
            self.assertEqual(self.api('?new=current'),[])
        self.assertEqual(len(self.api('?new=current')),1)

    def test_20_new_override_and_visibility(self):
        p=self.place(opened_year='2020')
        self.assertEqual(self.api('?new=current'),[])
        self.execute("UPDATE places SET new_override='show'")
        self.assertEqual(len(self.api('?new=current')),1)
        self.execute("UPDATE places SET new_override='hide'")
        self.assertEqual(self.api('?new=current'),[])
        self.execute("UPDATE places SET new_override='show',published=0")
        self.assertEqual(self.api('?new=current'),[])

    def test_21_duplicate_name_normalization_warning_and_ack(self):
        self.place(name='테스트 커피')
        r=self.create(name='테스트커피!')
        self.assertEqual(r.status_code,409);self.assertIn('비슷한 장소',r.text)
        self.assertEqual(len(self.rows('SELECT * FROM places')),1)
        self.assertEqual(self.create(name='테스트커피!',duplicate_ack='1').status_code,303)

    def test_22_duplicate_address_and_coordinates(self):
        p=self.place(latitude='33.4',longitude='126.5')
        self.assertEqual(self.create(name='테스트 카페').status_code,409)
        self.assertEqual(self.create(name='테스트 커피점',address='완전히 다른 주소',latitude='33.40001',longitude='126.50001').status_code,409)
        self.execute('UPDATE places SET published=0')
        self.assertEqual(self.create().status_code,303)  # Private name/address must not leak.

    def test_23_pick_only_admin_and_category_unchanged(self):
        p=self.place()
        self.assertEqual(self.post(self.member,'/admin/picks',place_id=p['id']).status_code,303)
        self.assertEqual(self.pick(p,pick_note='운영자의 추천').status_code,303)
        self.assertEqual(self.api('?pick=current')[0]['pick_note'],'운영자의 추천')
        self.assertEqual(self.api()[0]['category'],'카페')
        self.assertEqual(self.api()[0]['verification_status'],'unverified')

    def test_24_pick_month_boundaries_archive_and_inactive(self):
        p=self.place();self.pick(p)
        self.execute('INSERT INTO place_pick_periods(place_id,year,month) VALUES (?,?,?)',(p['id'],self.now.year+1,1))
        with patch('place_taxonomy.today',return_value=date(self.now.year+1,2,1)),patch('community_places.today',return_value=date(self.now.year+1,2,1)):
            self.assertEqual(self.api('?pick=current'),[]);self.assertFalse(self.api()[0]['is_pick'])
        self.execute('UPDATE place_pick_periods SET is_active=0 WHERE year=?',(self.now.year,))
        self.assertEqual(self.api('?pick=current'),[])
        self.assertEqual(len(self.rows('SELECT * FROM place_pick_periods')),2)

    def test_25_pick_order_hidden_and_missing_coordinates(self):
        first=self.place();second=self.place(name='음식점',address='다른 주소')
        self.pick(first);self.pick(second)
        self.execute('UPDATE place_pick_periods SET sort_order=10 WHERE place_id=?',(first['id'],))
        self.assertEqual([p['id'] for p in self.api('?pick=current')],[second['id'],first['id']])
        self.assertFalse(self.api('?pick=current')[0]['has_coordinates'])
        self.execute('UPDATE places SET published=0 WHERE id=?',(first['id'],))
        self.assertEqual(len(self.api('?pick=current')),1)

    def test_26_best_manual_membership_only(self):
        p=self.place()
        self.assertEqual(self.rows('SELECT * FROM best_collection_places'),[])
        url='/admin/best/1/places'
        self.assertEqual(self.post(self.other,url,place_id=p['id'],action='add').status_code,303)
        self.assertEqual(self.rows('SELECT * FROM best_collection_places'),[])
        self.assertEqual(self.post(self.admin,url,place_id=p['id'],action='add').status_code,303)
        self.assertIn(p['name'],self.visitor.get('/best/parents').text)
        self.execute('UPDATE places SET published=0')
        self.assertNotIn(p['name'],self.visitor.get('/best/parents').text)
        self.assertEqual(self.post(self.admin,url,place_id=p['id'],action='remove').status_code,303)
        self.assertEqual(self.rows('SELECT * FROM best_collection_places'),[])

    def test_27_merge_keeps_relations_and_old_links(self):
        source=self.place();target=self.place(name='유지할 장소',address='새 주소')
        self.execute("UPDATE places SET image_url='https://example.com/legacy-photo.jpg' WHERE id=?",(source['id'],))
        self.suggestion(source);self.pick(source)
        self.post(self.admin,'/admin/best/1/places',action='add',place_id=source['id'])
        self.assertEqual(self.post(self.admin,f"/admin/places/{source['id']}/merge",target_id=target['id'],confirm_merge='1').status_code,303)
        r=self.visitor.get('/place/'+source['slug'],follow_redirects=False)
        self.assertEqual(r.status_code,301);self.assertEqual(r.headers['location'],'/place/'+target['slug'])
        self.assertEqual([p['id'] for p in self.api()],[target['id']])
        self.assertEqual(self.rows('SELECT place_id FROM place_correction_suggestions')[0]['place_id'],target['id'])
        self.assertEqual(self.api('?pick=current')[0]['id'],target['id'])
        self.assertIn(target['name'],self.visitor.get('/best/parents').text)
        self.assertEqual(len(self.rows('SELECT * FROM places')),2)
        self.assertIn('https://example.com/legacy-photo.jpg',self.visitor.get('/place/'+target['slug']).text)
        self.assertEqual(self.member.get(f"/places/{source['id']}/edit").status_code,404)

    def test_28_merge_permissions_self_cycle_private_target(self):
        p=self.place();target=self.place(name='유지',address='다른 주소')
        url=f"/admin/places/{p['id']}/merge"
        self.assertEqual(self.post(self.member,url,target_id=target['id'],confirm_merge='1').status_code,303)
        self.assertEqual(self.post(self.admin,url,target_id=p['id'],confirm_merge='1').status_code,400)
        self.assertEqual(self.post(self.admin,url,target_id=target['id']).status_code,400)
        self.execute('UPDATE places SET published=0 WHERE id=?',(target['id'],))
        self.post(self.admin,url,target_id=target['id'],confirm_merge='1')
        self.assertEqual(self.visitor.get('/place/'+p['slug']).status_code,404)

    def test_29_image_upload_merge_overflow_and_private_access(self):
        output=io.BytesIO();Image.new('RGB',(40,50),'blue').save(output,'PNG')
        files=[(f'image_{n}',(f'{n}.png',output.getvalue(),'image/png')) for n in range(5)]
        for name,address in [('사진 원본','주소1'),('사진 유지','주소2')]:
            r=self.member.post('/places/new',data={'csrf_token':self.csrf(self.member),**self.form(name=name,address=address)},files=files,follow_redirects=False)
            self.assertEqual(r.status_code,303,r.text)
        source,target=self.rows('SELECT * FROM places ORDER BY id')
        image_paths=[r['file_path'] for r in self.rows('SELECT * FROM images')]
        self.post(self.admin,f"/admin/places/{source['id']}/merge",target_id=target['id'],confirm_merge='1')
        html=self.visitor.get('/place/'+target['slug']).text
        for path in image_paths:
            self.assertIn('/uploads/'+path,html)
            self.assertEqual(self.visitor.get('/uploads/'+path).status_code,200)
        self.execute('UPDATE places SET published=0 WHERE id=?',(target['id'],))
        for path in image_paths: self.assertEqual(self.visitor.get('/uploads/'+path).status_code,404)
        self.assertEqual(len(self.rows('SELECT * FROM images')),10)

    def test_30_no_admin_fields_or_legacy_image_url_in_member_form(self):
        html=self.member.get('/places/new').text
        for field in ('reason','is_pick','verification_status','image_url','published','new_override'):
            self.assertNotIn(f'name="{field}"',html)
        self.assertIn('multipart/form-data',html);self.assertEqual(html.count('type="file"'),5)
        self.assertIn('본인이 촬영했거나 사용 권한이 있는 사진만 등록해주세요.',html)

    def test_31_admin_pages_and_own_activity_render(self):
        p=self.place();self.suggestion(p)
        for path in ('/admin/community','/admin/community?status=duplicates','/admin/picks','/admin/best',f"/admin/places/{p['id']}/merge",f"/admin/places/{p['id']}/edit"):
            self.assertEqual(self.admin.get(path).status_code,200,path)
        html=self.member.get('/account/places').text
        self.assertIn(p['name'],html);self.assertIn('바뀐 장소 이름',html)
        self.assertNotIn(p['name'],self.other.get('/account/places').text)

    def test_32_login_returns_only_safe_participation_pages(self):
        for next_url,expected in [('/places/new?from=new','/places/new?from=new'),('https://evil.test','/account'),('//evil.test','/account')]:
            r=self.visitor.post('/login',data={'csrf_token':self.csrf(self.visitor,'/login'),'username':'other','password':'community-test-secret','next':next_url},follow_redirects=False)
            self.assertEqual(r.headers['location'],expected)
            self.post(self.visitor,'/logout')

    def test_33_rate_limit_no_extra_records(self):
        with patch('community_routes.rate_limited',return_value=True):
            self.assertEqual(self.create().status_code,429)
        self.assertEqual(self.api(),[])

    def test_34_existing_admin_can_verify_hide_and_edit_user_place(self):
        p=self.place()
        r=self.post(self.admin,f"/admin/places/{p['id']}/edit",**self.form(name='관리자 확인'),published='1',verification_status='jejuno_verified',reason='직접 고른 이유',opened_year=str(self.now.year))
        self.assertEqual(r.status_code,303,r.text)
        record=self.api()[0]
        self.assertEqual((record['source_type'],record['verification_status']),('user','jejuno_verified'))
        self.assertTrue(self.rows('SELECT verified_at FROM places')[0]['verified_at'])
        self.post(self.admin,f"/admin/places/{p['id']}/toggle-published")
        self.assertEqual(self.api(),[])

    def test_35_restart_keeps_places_picks_suggestions_and_users(self):
        p=self.place();self.pick(p);self.suggestion(p)
        before={table:self.rows(f'SELECT * FROM {table}') for table in ('places','place_pick_periods','place_correction_suggestions','users','categories')}
        restarted=create_app(self.config)
        with TestClient(restarted) as client: self.assertEqual(len(client.get('/api/places').json()),1)
        for table,rows in before.items(): self.assertEqual(self.rows(f'SELECT * FROM {table}'),rows)

    def test_38_new_food_registration_and_shared_filters(self):
        p=self.place(category_id=self.category['food'],from_new='1',opened_year=str(self.now.year),opened_month='1')
        self.assertEqual([r['id'] for r in self.api('?new=current&category=food')],[p['id']])
        self.assertEqual(self.api('?new=current&category=cafe'),[])
        self.assertEqual(self.api('?category=attraction'),[])
        self.assertEqual(self.api('?new=current')[0]['opened_month'],1)

    def test_39_merge_chain_preserves_all_shared_slugs(self):
        places=[self.place(name=f'장소 {n}',address=f'주소 {n}',duplicate_ack='1') for n in range(3)]
        for source,target in zip(places,places[1:]):
            self.assertEqual(self.post(self.admin,f"/admin/places/{source['id']}/merge",target_id=target['id'],confirm_merge='1').status_code,303)
        for old in places[:2]:
            self.assertEqual(self.visitor.get('/place/'+old['slug'],follow_redirects=False).headers['location'],'/place/'+places[-1]['slug'])
        self.assertEqual(len(self.api()),1)

    def test_40_admin_delete_retains_suggestion_and_pick_archive(self):
        p=self.place();self.suggestion(p);self.pick(p)
        self.post(self.admin,'/admin/best/1/places',place_id=p['id'],action='add')
        self.assertEqual(self.post(self.admin,f"/admin/places/{p['id']}/delete").status_code,303)
        self.assertEqual(self.api(),[])
        self.assertEqual(len(self.rows('SELECT * FROM place_pick_periods')),1)
        self.assertEqual(len(self.rows('SELECT * FROM place_correction_suggestions')),1)
        self.assertEqual(self.post(self.admin,'/admin/suggestions/1/review',action='accept').status_code,409)
        self.assertEqual(self.post(self.admin,'/admin/best/1/places',place_id=p['id'],action='remove').status_code,303)


    def test_38_map_registration_auth_prefill_and_legacy_url(self):
        from urllib.parse import unquote
        url='/map/register?source=new&category=food&region=%EC%95%A0%EC%9B%94&detail=food-western'
        guest=self.visitor.get(url,follow_redirects=False)
        self.assertEqual(guest.status_code,303)
        self.assertEqual(unquote(guest.headers['location'].split('next=')[1]),url)
        html=self.member.get(url).text
        self.assertIn('MAP · NEW',html)
        self.assertIn('action="/map/register"',html)
        self.assertRegex(html,r'name="opened_year"[^>]+required')
        self.assertIn('id="placeLocationMap"',html)
        term=self.rows("SELECT id FROM categories WHERE slug='food-western'")[0]['id']
        self.assertRegex(html,rf'name="term_ids" value="{term}" checked')
        legacy=self.member.get('/places/new?from=new',follow_redirects=False)
        self.assertEqual(legacy.headers['location'],'/map/register?from=new')
        self.assertEqual(self.visitor.get('/map?register=1&source=new',follow_redirects=False).headers['location'],'/map/register?register=1&source=new')
        self.assertEqual(self.admin.get('/map/register',follow_redirects=False).headers['location'],'/admin/places/new')
        login=self.visitor.post('/login',data={'csrf_token':self.csrf(self.visitor,'/login'),'username':'other','password':'community-test-secret','next':url},follow_redirects=False)
        self.assertEqual(login.headers['location'],url)

    def test_39_one_map_record_discovered_by_find_map_and_new(self):
        term=self.rows("SELECT id FROM categories WHERE slug='food-western'")[0]['id']
        r=self.post(self.member,'/map/register',**self.form(category_id=self.category['food'],opened_year=str(self.now.year),from_new='1',taxonomy_present='1',term_ids=[str(term)],latitude='33.45',longitude='126.4'))
        self.assertEqual(r.status_code,303,r.text)
        self.assertEqual(len(self.rows('SELECT id FROM places')),1)
        p=self.rows('SELECT * FROM places')[0]
        self.assertEqual(r.headers['location'],'/place/'+p['slug'])
        for query in ('?category=food&region=애월&detail=food-western','?new=current&category=food'):
            results=self.api(query)
            self.assertEqual([item['id'] for item in results],[p['id']])
            self.assertEqual(results[0]['status_label'],'사용자 등록 · 미확인')
            self.assertEqual(results[0]['latitude'],33.45)
            self.assertFalse(results[0]['is_pick'])
        self.assertEqual(self.visitor.get('/place/'+p['slug']).status_code,200)

    def test_40_member_terms_validate_preserve_and_switch_category(self):
        cafe=self.rows("SELECT id FROM categories WHERE slug='cafe-view'")[0]['id']
        food=self.rows("SELECT id FROM categories WHERE slug='food-western'")[0]['id']
        for term in (str(food),'99999','bad','9'*5000):
            r=self.post(self.member,'/map/register',**self.form(taxonomy_present='1',term_ids=[term]))
            self.assertEqual(r.status_code,400)
        p=self.place(taxonomy_present='1',term_ids=[str(cafe)])
        self.assertIn(f'value="{cafe}" checked',self.member.get(f"/places/{p['id']}/edit").text)
        self.assertEqual(self.post(self.member,f"/places/{p['id']}/edit",**self.form()).status_code,303)
        self.assertEqual(self.rows('SELECT category_id FROM place_category_links'),[{'category_id':cafe}])
        self.assertEqual(self.post(self.member,f"/places/{p['id']}/edit",**self.form(category_id=self.category['attraction'],taxonomy_present='1')).status_code,303)
        self.assertEqual(self.rows('SELECT * FROM place_category_links'),[])

    def test_41_canonical_map_upload_duplicate_and_security(self):
        image=io.BytesIO();Image.effect_noise((500,500),100).convert('RGB').save(image,format='PNG')
        self.assertGreater(len(image.getvalue()),131072)
        data={'csrf_token':self.csrf(self.member),**self.form(opened_year=str(self.now.year))}
        r=self.member.post('/map/register',data=data,files={'image_0':('photo.png',image.getvalue(),'image/png')},follow_redirects=False)
        self.assertEqual(r.status_code,303,r.text)
        self.assertEqual(len(self.rows('SELECT * FROM images')),1)
        duplicate=self.post(self.member,'/map/register',**self.form())
        self.assertEqual(duplicate.status_code,409)
        self.assertIn('정보 수정 제안',duplicate.text)
        self.assertEqual(len(self.rows('SELECT * FROM places')),1)
        self.assertEqual(self.member.post('/map/register',data=self.form()).status_code,400)
        self.assertEqual(self.post(self.member,'/map/register',**self.form(is_pick='1')).status_code,403)

    def test_42_find_has_no_register_cta_and_new_uses_map(self):
        html=self.member.get('/find').text
        self.assertNotIn('+ 장소 알려주기',html)
        self.assertIn('MAP에서 장소 알려주기',html)
        self.assertIn('id="finderMapLink"',html)
        self.assertIn('id="finderQuery"',html)
        self.assertIn('/map/register?source=new&amp;category=cafe',self.member.get('/new?category=cafe').text)
        self.assertIn('id="mapRegisterLink"',self.visitor.get('/map').text)
        self.assertIn('id="mapFindLink"',self.visitor.get('/map').text)

    def test_43_new_context_requires_year_and_still_allows_missing_coordinates(self):
        r=self.post(self.member,'/map/register',**self.form(from_new='1'))
        self.assertEqual(r.status_code,400)
        r=self.post(self.member,'/map/register',**self.form(from_new='1',opened_year=str(self.now.year)))
        self.assertEqual(r.status_code,303)
        result=self.api('?new=current')[0]
        self.assertIsNone(result['latitude']);self.assertIsNone(result['longitude'])


class CommunityMigrationTest(unittest.TestCase):
    def test_36_legacy_backup_preserves_all_original_columns_and_pick(self):
        with tempfile.TemporaryDirectory() as folder:
            db=str(Path(folder)/'old.db')
            # Build exactly the previous commit's schema through its existing migration functions.
            with patch.dict(os.environ,{'JEJUNO_SKIP_SEED':'1'}),patch('app.migrate_community'),patch('app.backup_before_community_migration'):
                create_app({'TESTING':True,'DATABASE':db,'SECRET_KEY':'migration-test'})
            with closing(sqlite3.connect(db)) as conn,conn:
                conn.execute("INSERT INTO places(slug,name,category,region,is_pick,companions,checked_at) VALUES ('old','기존 장소','카페','애월',1,'[\"부모님\"]','2020-01-01')")
                original=conn.execute('SELECT * FROM places').fetchall()[0]
                columns=[r[1] for r in conn.execute('PRAGMA table_info(places)')]
            config={'TESTING':True,'DATABASE':db,'SECRET_KEY':'migration-test'}
            create_app(config)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute('SELECT '+','.join(columns)+' FROM places').fetchone(),original)
                self.assertEqual(conn.execute('SELECT verification_status FROM places').fetchone()[0],'unverified')
                self.assertEqual(conn.execute('SELECT count(*) FROM place_pick_periods').fetchone()[0],1)
                self.assertEqual(conn.execute('SELECT count(*) FROM best_collection_places').fetchone()[0],1)
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
                backup=conn.execute('SELECT backup_path FROM schema_migrations WHERE version=?',(VERSION,)).fetchone()[0]
            with closing(sqlite3.connect(backup)) as conn:
                self.assertEqual(conn.execute('SELECT * FROM places').fetchone(),original)
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertIsNone(backup_before_community_migration(db))

    def test_37_backup_failure_blocks_startup_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            db=str(Path(folder)/'old.db')
            with closing(sqlite3.connect(db)) as conn,conn: conn.execute('CREATE TABLE sentinel (id)')
            before=Path(db).read_bytes()
            with patch('app.backup_before_community_migration',side_effect=OSError('backup failed')):
                with self.assertRaises(OSError): create_app({'TESTING':True,'DATABASE':db,'SECRET_KEY':'failure-test'})
            self.assertEqual(Path(db).read_bytes(),before)


if __name__=='__main__': unittest.main()
