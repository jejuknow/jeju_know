"""Public discovery must not leak internal registration/review status labels."""
import unittest

import test_community
from auth_security import hash_password


class PublicPlaceStatusTest(unittest.TestCase):
    setUp = test_community.CommunityTest.setUp
    tearDown = test_community.CommunityTest.tearDown
    rows = test_community.CommunityTest.rows
    execute = test_community.CommunityTest.execute
    csrf = test_community.CommunityTest.csrf
    post = test_community.CommunityTest.post
    form = test_community.CommunityTest.form
    create = test_community.CommunityTest.create
    place = test_community.CommunityTest.place

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('community-test-secret')

    def test_public_api_cards_details_and_private_workflow_remain_separate(self):
        member = self.place()
        generic = self.place(name='CSV 일반 장소')
        verified = self.place(name='확인한 장소')
        pick = self.place(name='추천 장소')
        hidden = self.place(name='비공개 장소')
        self.execute("UPDATE places SET source_type='jejuno',submitted_by_user_id=NULL,admin_import_note='CSV receipt' WHERE id=?", (generic['id'],))
        self.execute("UPDATE places SET verification_status='jejuno_verified' WHERE id=?", (verified['id'],))
        self.execute("INSERT INTO place_pick_periods(place_id,year,month,is_active) VALUES (?,?,?,1)", (pick['id'],self.now.year,self.now.month))
        self.execute('UPDATE places SET published=0 WHERE id=?', (hidden['id'],))
        collection = self.execute("INSERT INTO best_collections(slug,title,published) VALUES ('status-qa','상태 점검',1)")
        for p in (member,generic,verified,pick):
            self.execute('INSERT INTO best_collection_places(collection_id,place_id) VALUES (?,?)', (collection,p['id']))
        before = self.rows('SELECT * FROM places ORDER BY id')
        api = {p['id']:p for p in self.visitor.get('/api/places').json()}
        self.assertEqual(set(api), {p['id'] for p in (member,generic,verified,pick)})
        for p,label in ((member,'사용자 등록'),(generic,''),(verified,'JEJU_KNOW 확인'),(pick,'')):
            self.assertEqual(api[p['id']]['status_label'], label)
            self.assertEqual(api[p['id']]['source_type'], 'jejuno' if p==generic else 'user')
            self.assertNotIn('submitted_by_user_id',api[p['id']])
            self.assertNotIn('admin_import_note',api[p['id']])
            page=self.visitor.get('/place/'+p['slug'])
            self.assertEqual(page.status_code,200)
            self.assertNotIn('JEJU_KNOW 등록',page.text)
            if label:
                self.assertIn(label,page.text)
            if p==generic:
                self.assertNotIn('<dt>등록</dt>',page.text)
                self.assertNotIn('class="user-badge',page.text)
        self.assertTrue(api[pick['id']]['is_pick'])
        self.assertEqual(self.visitor.get('/place/'+hidden['slug']).status_code,404)
        for path in ('/','/find','/new','/best','/best/status-qa','/map','/places'):
            page=self.visitor.get(path)
            self.assertEqual(page.status_code,200,path)
            self.assertNotIn('JEJU_KNOW 등록',page.text,path)
        best=self.visitor.get('/best/status-qa').text
        for label in ('사용자 등록','JEJU_KNOW 확인','JEJU_KNOW PICK'):
            self.assertIn(label,best)
        mine=self.member.get('/account/places').text
        self.assertIn('검토 대기',mine)
        self.assertIn('비공개',mine)
        admin=self.admin.get('/admin/places').text
        self.assertIn('등록 주체',admin)
        self.assertIn('관리자',admin)
        self.assertIn('사용자',admin)
        self.assertIn('JEJU_KNOW 확인',admin)
        self.assertEqual(before,self.rows('SELECT * FROM places ORDER BY id'))
