"""Member facts stay reviewable; editorial and retired values stay protected."""
import json
import re
import unittest
from unittest.mock import patch

import test_community as fixtures
from submission_suggestions import IGNORED_MEMBER_FIELDS, MEMBER_USAGE_LABELS


class SimpleMemberFormTest(unittest.TestCase):
    setUp = fixtures.CommunityTest.setUp
    tearDown = fixtures.CommunityTest.tearDown
    setUpClass = classmethod(fixtures.CommunityTest.setUpClass.__func__)
    rows = fixtures.CommunityTest.rows
    execute = fixtures.CommunityTest.execute
    csrf = fixtures.CommunityTest.csrf
    post = fixtures.CommunityTest.post
    form = fixtures.CommunityTest.form
    create = fixtures.CommunityTest.create
    place = fixtures.CommunityTest.place

    def review(self):
        row = self.rows('SELECT * FROM place_correction_suggestions ORDER BY id DESC')[0]
        return self.post(self.admin, f'/admin/suggestions/{row["id"]}/review', action='accept')

    def test_form_defaults_and_admin_curation_remain_separate(self):
        for category in ('cafe', 'food', 'attraction'):
            page = self.member.get('/map/register', params={'category':category})
            self.assertEqual(page.status_code,200)
            for text in ('세부 정보 작성','세부 정보도 알려줄게요','잘 맞는 동행','웨이팅','예약'):
                self.assertNotIn(text,page.text)
            for key in MEMBER_USAGE_LABELS:
                inputs = re.findall(r'<input type="radio" name="detail_'+key+r'"[^>]*>',page.text)
                self.assertEqual(len(inputs),3)
                self.assertEqual(sum('checked' in i for i in inputs),1)
                self.assertIn('value="미확인" checked', inputs[0])
            self.assertIn('name="detail_note"',page.text)
            self.assertNotIn('name="details_enabled"',page.text)
        admin = self.admin.get('/admin/places/new').text
        self.assertIn('누구와 잘 맞나요?',admin)
        self.assertIn('name="companions"',admin)
        self.assertNotIn('name="detail_parking_status"',admin)

    def test_usage_and_tips_without_toggle_saved_only_after_review(self):
        p = self.place(detail_parking_status='가능',detail_pet_status='불가',detail_child_status='가능',
                       detail_accessible_status='미확인',detail_note='골목 안쪽 입구를 이용하세요.')
        self.assertEqual([p[k] for k in MEMBER_USAGE_LABELS], ['미확인']*4)
        self.assertEqual(p['note'],'')
        proposal = json.loads(self.rows('SELECT new_value FROM place_correction_suggestions')[0]['new_value'])
        self.assertEqual(proposal['usage'],{'parking_status':'가능','pet_status':'불가','child_status':'가능'})
        self.assertEqual(proposal['companions'],[])
        self.assertEqual(self.review().status_code,303)
        saved = self.rows('SELECT * FROM places')[0]
        self.assertEqual([saved[k] for k in MEMBER_USAGE_LABELS],['가능','불가','가능','미확인'])
        self.assertEqual(saved['note'],'골목 안쪽 입구를 이용하세요.')
        self.assertEqual((saved['published'],saved['verification_status'],saved['is_pick']),(1,'unverified',0))
        self.assertIn(saved['note'],self.visitor.get('/place/'+p['slug']).text)

    @patch('community_routes.rate_limited', return_value=False)
    def test_removed_fields_ignored_and_other_admin_fields_rejected(self, _rate_limit):
        payload = {key:'tampered' for key in IGNORED_MEMBER_FIELDS}
        p = self.place(**payload)
        self.assertEqual(json.loads(p['companions']),[])
        self.assertEqual((p['reservation_status'],p['waiting_status']),('미확인','미확인'))
        self.assertFalse(self.rows('SELECT * FROM place_correction_suggestions'))
        for values in ({'is_pick':'1'},{'verification_status':'jejuno_verified'},{'reason':'관리자 추천'},
                       {'cafe_focus':'taste'},{'published':'1'}):
            self.assertEqual(self.create(**values).status_code,403)
        for key in MEMBER_USAGE_LABELS:
            for value in ('fake','제한','0'):
                self.assertEqual(self.create(**{'detail_'+key:value}).status_code,400)
        self.assertEqual(len(self.rows('SELECT * FROM places')),1)

    def test_member_edit_and_corrections_preserve_editorial_and_legacy_data(self):
        p = self.place()
        self.execute("UPDATE places SET companions='[\"부모님\"]',reservation_status='가능',waiting_status='주말·성수기 있음',parking_status='제한',note='기존 꿀팁' WHERE id=?",(p['id'],))
        self.execute("CREATE TRIGGER protect_curation BEFORE UPDATE OF companions,reservation_status,waiting_status ON places BEGIN SELECT RAISE(ABORT,'protected values rewritten'); END")
        before = self.rows('SELECT * FROM places')[0]
        self.assertEqual(self.post(self.member,f'/places/{p["id"]}/edit',**self.form(),
                         companions=['연인'],detail_companions=['아이'],reservation_status='',waiting_status='',
                         detail_parking_status='미확인',detail_note='').status_code,303)
        after = self.rows('SELECT * FROM places')[0]
        for key in ('companions','reservation_status','waiting_status','parking_status','note'):
            self.assertEqual(after[key],before[key])
        self.assertFalse(self.rows('SELECT * FROM place_correction_suggestions'))
        url=f'/places/{p["id"]}/suggest'
        html=self.member.get(url).text
        self.assertNotIn('value="reservation_status"',html)
        for key in (*MEMBER_USAGE_LABELS,'note'):
            self.assertIn(f'value="{key}"',html)
        for field in ('companions','detail_companions','reservation_status','waiting_status','is_pick','verification_status'):
            self.assertEqual(self.post(self.member,url,field=field,new_value='가능',reason='현장 안내 확인').status_code,400)
        self.assertEqual(self.post(self.member,url,field='parking_status',new_value='미확인',reason='현장 안내 확인').status_code,303)
        self.assertFalse(self.rows('SELECT * FROM place_correction_suggestions'))
        for field,value in (('pet_status','불가'),('child_status','가능'),('accessible_status','가능'),('note','새로운 방문 팁')):
            original=self.rows('SELECT * FROM places')[0]
            self.assertEqual(self.post(self.member,url,field=field,new_value=value,reason='현장 안내 확인').status_code,303)
            self.assertEqual(self.rows('SELECT * FROM places')[0],original)
            self.assertEqual(self.review().status_code,303)
            self.assertEqual(self.rows('SELECT * FROM places')[0][field],value)
        final=self.rows('SELECT * FROM places')[0]
        for key in ('companions','reservation_status','waiting_status','parking_status'):
            self.assertEqual(final[key],before[key])
        self.assertEqual(len(self.visitor.get('/api/places?companion=부모님').json()),1)
        self.assertEqual(self.visitor.get('/api/places?companion=연인').json(),[])
