"""An AJAX save keeps its flash for the one visible destination request."""
import unittest

from test_community import CommunityTest as Base
from test_issue_review import IssueReviewTest
from test_images import picture


class AdminSaveTest(unittest.TestCase):
    setUpClass = classmethod(Base.setUpClass.__func__)
    setUp = Base.setUp
    tearDown = Base.tearDown
    rows = Base.rows
    execute = Base.execute
    csrf = Base.csrf
    post = Base.post
    form = Base.form

    def save(self, path='/admin/places/new', **changes):
        return self.admin.post(path, data=self.form(
            csrf_token=self.csrf(self.admin, '/admin'), next='/admin/places?category=cafe',
            **changes), headers={'X-Requested-With': 'fetch'}, follow_redirects=False)

    def assert_flash_once(self, response, message):
        self.assertEqual(response.status_code, 200, response.text[:200])
        target = response.json()['redirect']
        self.assertIn(message, self.admin.get(target).text)
        self.assertNotIn(message, self.admin.get(target).text)

    def test_create_edit_one_update_and_success_flash_once(self):
        self.assert_flash_once(self.save(), '장소를 저장했습니다.')
        row = self.rows('SELECT * FROM places')[0]
        self.execute('CREATE TABLE qa_updates (id INTEGER)')
        self.execute('CREATE TRIGGER qa_update AFTER UPDATE OF name ON places BEGIN INSERT INTO qa_updates VALUES (NEW.id); END')
        self.assert_flash_once(self.save(f'/admin/places/{row["id"]}/edit',
            slug=row['slug'], info_source='마지막 입력값', published='1', is_pick='1',
            is_example='1', new_override='hide'), '수정사항을 저장했습니다.')
        stored = self.rows('SELECT * FROM places')[0]
        self.assertEqual(stored['info_source'], '마지막 입력값')
        self.assertEqual((stored['published'], stored['is_pick'], stored['is_example'], stored['new_override']), (1, 1, 1, 'hide'))
        self.assertEqual(self.rows('SELECT * FROM qa_updates'), [{'id': row['id']}])
        self.assertEqual(len(self.rows("SELECT * FROM admin_activity_logs WHERE action='장소 수정'")), 1)

    def test_normal_form_keeps_303_and_unchecked_values_clear(self):
        self.save(is_pick='1', published='1', is_example='1')
        row = self.rows('SELECT * FROM places')[0]
        response = self.post(self.admin, f'/admin/places/{row["id"]}/edit', **self.form())
        self.assertEqual(response.status_code, 303)
        stored = self.rows('SELECT * FROM places')[0]
        self.assertEqual((stored['published'], stored['is_pick'], stored['is_example']), (0, 0, 0))

    def test_validation_csrf_and_permissions_do_not_write(self):
        self.save()
        before = self.rows('SELECT * FROM places')
        path = f'/admin/places/{before[0]["id"]}/edit'
        for response in (
            self.save(path, name=''),
            self.admin.post(path, data=self.form(csrf_token='bad'), headers={'X-Requested-With': 'fetch'}),
            self.other.post(path, data=self.form(), headers={'X-Requested-With': 'fetch'}, follow_redirects=False),
            self.visitor.post(path, data=self.form(), headers={'X-Requested-With': 'fetch'}, follow_redirects=False),
        ):
            self.assertIn(response.status_code, (303, 400, 403))
        self.assertEqual(self.rows('SELECT * FROM places'), before)

    def test_ajax_return_stays_inside_admin(self):
        response = self.admin.post('/admin/places/new', data=self.form(
            csrf_token=self.csrf(self.admin, '/admin'), next='https://example.org'),
            headers={'X-Requested-With': 'fetch'})
        self.assertEqual(response.json()['redirect'], '/admin')

    def test_related_admin_and_public_views(self):
        self.save(published='1')
        for path in ('/admin/places', '/admin/places/new', '/admin/community',
                     '/admin/suggestions', '/admin/places/stale', '/admin/places/duplicates',
                     '/admin/new', '/admin/picks', '/admin/imports', '/admin/issues',
                     '/admin/best', '/admin/ads', '/admin/classifications/restaurant_subcategory'):
            self.assertEqual(self.admin.get(path).status_code, 200, path)
        slug = self.rows('SELECT slug FROM places')[0]['slug']
        for path in ('/', '/find', '/new', '/map', '/best', '/place/'+slug):
            self.assertEqual(self.visitor.get(path).status_code, 200, path)

    def test_shared_issue_and_ad_success_flash(self):
        token = self.csrf(self.admin, '/admin')
        fields = IssueReviewTest.data(self, csrf_token=token, next='/admin/issues')
        response = self.admin.post('/admin/news/new', data=fields, headers={'X-Requested-With':'fetch'})
        self.assert_flash_once(response, '제주 소식을 저장했습니다.')
        row = self.rows('SELECT * FROM news_posts')[0]
        response = self.admin.post(f'/admin/news/{row["id"]}/edit', data={**fields, 'slug':row['slug']},
                                   headers={'X-Requested-With':'fetch'})
        self.assert_flash_once(response, '제주 소식을 수정했습니다.')
        response = self.admin.post('/admin/ads/new', data={
            'csrf_token': token, 'name':'광고 저장 점검', 'advertiser_name':'점검', 'ad_type':'ad',
            'slot':'SIDE-01', 'target_url':'https://example.org', 'start_date':'2026-11-25',
            'end_date':'2026-11-26', 'priority':'1'},
            files={'image_0':('test.png',picture(),'image/png')}, headers={'X-Requested-With':'fetch'})
        self.assert_flash_once(response, '광고를 저장했습니다.')
