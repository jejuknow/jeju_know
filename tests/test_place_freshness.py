import io
import unittest
from datetime import date
from unittest.mock import patch

from PIL import Image
import test_community
from place_freshness import six_month_cutoff


class FreshnessTests(unittest.TestCase):
    setUpClass = classmethod(test_community.CommunityTest.setUpClass.__func__)
    setUp = test_community.CommunityTest.setUp
    tearDown = test_community.CommunityTest.tearDown
    execute = test_community.CommunityTest.execute
    rows = test_community.CommunityTest.rows
    csrf = test_community.CommunityTest.csrf
    post = test_community.CommunityTest.post
    form = test_community.CommunityTest.form
    queue = '/admin/places/stale'

    def add(self, name, checked='', published=1):
        return self.execute("""INSERT INTO places(slug,name,region,category,category_id,checked_at,published,verification_status)
                            VALUES (?,?,'애월','카페',?,?,?,'jejuno_verified')""",
                            (name, name, self.category['cafe'], checked, published))

    @patch('admin_ui.today', return_value=date(2026, 11, 3))
    def test_queue_boundaries_pagination_visibility_and_dashboard(self, _):
        self.assertEqual(six_month_cutoff(date(2024,8,31)), date(2024,2,29))
        self.assertEqual(six_month_cutoff(date(2025,8,31)), date(2025,2,28))
        self.assertEqual(six_month_cutoff(date(2026,1,31)), date(2025,7,31))
        for i in range(11):
            self.add(f'unknown-{i}')
        self.add('old','2026-04-03',0)
        self.add('boundary','2026-05-03')
        self.add('too-early','2026-05-04')
        self.add('recent','2026-09-03')
        deleted = self.add('deleted')
        merged = self.add('merged')
        self.execute("UPDATE places SET deleted_at=CURRENT_TIMESTAMP WHERE id=?",(deleted,))
        self.execute("UPDATE places SET merged_into_id=? WHERE id=?",(deleted,merged))
        dashboard = self.admin.get('/admin')
        self.assertEqual(dashboard.context['freshness'], {'never':11,'stale':2})
        for mode, total in [('never',11),('stale',2),('all',13)]:
            url = self.queue + '?filter=' + mode
            if mode != 'all':
                self.assertIn('href="' + url + '"',dashboard.text)
            page = self.admin.get(url)
            self.assertEqual(page.context['selected'], mode)
            self.assertEqual(page.context['pagination']['total'], total)
            self.assertEqual(page.context['freshness'], dashboard.context['freshness'])
        last = self.admin.get(self.queue+'?page=2').context['places']
        self.assertEqual([r['name'] for r in last], ['unknown-10','old','boundary'])
        self.assertEqual(last[1]['elapsed_days'], 214)
        self.assertIn('비공개', self.admin.get(self.queue+'?q=old').text)
        self.assertEqual(self.admin.get(self.queue+'?q=old&filter=never').context['pagination']['total'],0)

    @patch('admin_ui.today', return_value=date(2026,11,3))
    def test_confirmation_both_roles_is_idempotent_and_only_updates_date(self, _):
        for idx, checked in enumerate(('', '2026-04-03')):
            pid = self.add(f'place-{idx}',checked,published=0)
            if idx:
                self.execute("INSERT INTO admins(username,password_hash,role) VALUES (?,?,'sub_admin')",('editor',self.password_hash))
                self.admin = self.other
                login = self.admin.post('/admin/login', data={'username':'editor','password':'community-test-secret',
                                        'csrf_token':self.csrf(self.admin,'/admin/login')}, follow_redirects=False)
                self.assertEqual(login.status_code,303)
            url = f'/admin/places/{pid}/confirm-information'
            before = self.rows('SELECT * FROM places WHERE id=?',(pid,))[0]
            for client in (self.member, self.visitor):
                self.assertEqual(client.get(self.queue,follow_redirects=False).status_code,303)
                self.assertEqual(client.post(url,follow_redirects=False).status_code,303)
            self.assertEqual(self.admin.post(url,data={}).status_code,400)
            good = self.admin.post(url,data={'csrf_token':self.csrf(self.admin,'/admin')},headers={'Accept':'application/json'})
            self.assertEqual(good.json()['checked_at'],'2026-11-03')
            self.assertEqual(self.post(self.admin,url).status_code,303)
            self.assertEqual(self.rows('SELECT * FROM places WHERE id=?',(pid,))[0], {**before,'checked_at':'2026-11-03'})
            self.assertEqual(self.rows("SELECT count(*) n FROM admin_activity_logs WHERE action='장소 정보 확인 완료' AND target_id=?",(pid,))[0]['n'],1)
            self.assertEqual(self.admin.get(self.queue).context['pagination']['total'],0)
        self.assertEqual(self.post(self.admin,'/admin/places/9999/confirm-information').status_code,404)

    def test_editor_photo_pick_and_visibility_cannot_overwrite_confirmed_date(self):
        pid = self.add('photo-test','2026-03-01')
        edit = f'/admin/places/{pid}/edit'
        stale_editor = self.admin.get(edit)
        self.assertNotIn('name="checked_at"', stale_editor.text)
        self.post(self.admin,f'/admin/places/{pid}/confirm-information')
        def stamp():
            return self.rows('SELECT checked_at FROM places WHERE id=?',(pid,))[0]['checked_at']
        confirmed = stamp()
        self.assertNotEqual(confirmed,'2026-03-01')
        image = io.BytesIO()
        Image.new('RGB',(50,60),'blue').save(image,'PNG')
        response = self.admin.post(edit,data={**self.form(slug='photo-test',published='1',checked_at='2026-03-01'),
                              'csrf_token':stale_editor.context['csrf_token']},
                              files={'image_0':('photo.png',image.getvalue(),'image/png')},follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.assertEqual(len(self.rows('SELECT * FROM images')),1)
        self.assertEqual(stamp(),confirmed)
        self.assertEqual(self.post(self.admin,'/admin/picks',place_id=pid,year=self.now.year,month=self.now.month,
                                   sort_order=0,is_active='1').status_code,303)
        self.assertEqual(stamp(),confirmed)
        self.assertEqual(self.post(self.admin,f'/admin/places/{pid}/toggle-published').status_code,303)
        self.assertEqual(stamp(),confirmed)
