"""Gallery atomicity, ownership, legacy schedule compatibility and public output."""
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from test_issue_review import IssueReviewTest as Base
from test_images import picture
from issue_gallery import migrate, VERSION


class IssueGalleryTests(unittest.TestCase):
    setUpClass=classmethod(Base.setUpClass.__func__)
    setUp=Base.setUp
    tearDown=Base.tearDown
    csrf=Base.csrf
    rows=Base.rows
    execute=Base.execute
    data=Base.data
    submit=Base.submit
    review=Base.review

    def save(self, issue=None, count=0, **changes):
        original=self.rows('SELECT * FROM issue_images WHERE issue_id=? ORDER BY sort_order',(issue,)) if issue else []
        keys=['saved:'+str(r['id']) for r in original]
        data=self.data(csrf_token=self.csrf(self.admin,'/admin'),time_mode='specified',start_time='10:00',end_time='18:30',
            gallery_original=json.dumps(keys),gallery_order=json.dumps(keys+[f'new:{n}' for n in range(count)]))
        data.update(changes)
        files=[(f'gallery_{n}',(f'프로그램{n}.png',picture(),'image/png')) for n in range(count)]
        return self.admin.post(f'/admin/news/{issue}/edit' if issue else '/admin/news/new',data=data,files=files or None,follow_redirects=False)

    def test_five_images_order_removal_primary_and_og(self):
        self.assertEqual(self.save(count=5,published='1',image_url='https://example.com/poster.jpg').status_code,303)
        issue=self.rows('SELECT * FROM news_posts')[0];iid=issue['id']
        photos=self.rows('SELECT * FROM issue_images ORDER BY sort_order');self.assertEqual(len(photos),5)
        page=self.visitor.get('/news/'+issue['slug']);self.assertEqual(page.status_code,200)
        self.assertIn('행사 이미지',page.text);self.assertIn('10:00 ~ 18:30',page.text)
        self.assertIn('content="https://example.com/poster.jpg"',page.text)
        self.assertEqual(self.visitor.get('/uploads/'+photos[0]['file_path']).status_code,200)
        order=['saved:'+str(r['id']) for r in photos[::-1]]
        self.assertEqual(self.save(iid,gallery_order=json.dumps(order)).status_code,303)
        self.assertEqual([r['id'] for r in self.rows('SELECT * FROM issue_images ORDER BY sort_order')],[r['id'] for r in photos[::-1]])
        self.assertEqual(self.save(iid,gallery_order='[]').status_code,303)
        self.assertEqual(self.rows('SELECT * FROM issue_images'),[])
        self.assertEqual(self.rows('SELECT image_url FROM news_posts')[0]['image_url'],'https://example.com/poster.jpg')
        self.assertTrue(self.app.state.image_store.path(photos[0]['file_path']).exists())
        self.assertNotIn('class="issue-gallery"',self.visitor.get('/news/'+issue['slug']).text)

    def test_limit_forged_owner_and_stale_order_are_atomic(self):
        self.assertEqual(self.save(count=5).status_code,303);iid=self.rows('SELECT id FROM news_posts')[0]['id']
        before=self.rows('SELECT * FROM issue_images')
        self.assertEqual(self.save(iid,count=1).status_code,400)
        self.assertEqual(self.save(iid,gallery_order='["saved:999999"]').status_code,400)
        self.assertEqual(self.save(iid,gallery_original='[]',gallery_order='[]').status_code,400)
        self.assertEqual(self.rows('SELECT * FROM issue_images'),before)
        self.assertEqual(len(list(self.app.state.image_store.root.joinpath('issues').glob('*'))),5)

    def test_private_access_and_member_cannot_upload_gallery(self):
        self.assertEqual(self.save(count=1).status_code,303)
        url='/uploads/'+self.rows('SELECT file_path FROM issue_images')[0]['file_path']
        self.assertEqual(self.admin.get(url).status_code,200)
        for client in (self.member,self.other,self.visitor):self.assertEqual(client.get(url).status_code,404)
        response=self.member.post('/issues/new',data=self.data(csrf_token=self.csrf(self.member)),files={'gallery_0':('a.png',picture(),'image/png')})
        self.assertEqual(response.status_code,400)
        self.assertEqual(len(self.rows('SELECT * FROM news_posts')),1)
        self.assertEqual(self.visitor.post('/admin/news/image-preview').status_code,200) # follows login redirect

    def test_time_states_legacy_minutes_and_validation(self):
        self.assertEqual(self.save(time_mode='specified',start_time='18:00',end_time='10:00').status_code,400)
        self.assertEqual(self.save(start_time='14:10').status_code,400)
        self.assertEqual(self.save(time_mode='all_day',start_time='garbage',end_time='garbage').status_code,303)
        issue=self.rows('SELECT * FROM news_posts')[0];iid=issue['id']
        self.assertEqual((issue['time_mode'],issue['start_time'],issue['end_time']),('all_day','',''))
        self.assertEqual(self.save(iid,time_mode='unknown').status_code,303)
        self.assertEqual(self.rows('SELECT start_time,end_time FROM news_posts')[0],{'start_time':'','end_time':''})
        self.execute("UPDATE news_posts SET time_mode=NULL,start_time='14:10',end_time='17:45' WHERE id=?",(iid,))
        page=self.admin.get(f'/admin/news/{iid}/edit');self.assertIn('14:10 (기존값)',page.text);self.assertIn('17:45 (기존값)',page.text)
        self.assertEqual(self.save(iid,start_time='14:10',end_time='17:45').status_code,303)
        self.assertEqual(self.rows('SELECT start_time,end_time FROM news_posts')[0],{'start_time':'14:10','end_time':'17:45'})
        self.assertEqual(page.text.count('value="23:30"'),2)

    def test_preview_security_and_rollback(self):
        token=self.csrf(self.admin,'/admin');url='/admin/news/image-preview'
        self.assertEqual(self.admin.post(url,data={'csrf_token':token},files={'image_0':('live.heic',picture(),'image/heic')}).status_code,200)
        self.assertEqual(list(self.app.state.image_store.root.joinpath('issues').glob('*')),[])
        self.assertEqual(self.admin.post(url,data={'csrf_token':token},files={'image_0':('wrong.jpg',b'MZ not pixels','image/jpeg')}).status_code,400)
        self.assertEqual(self.admin.post(url,files={'image_0':('x.png',picture(),'image/png')}).status_code,400)
        blocked=self.member.post(url,data={'csrf_token':self.csrf(self.member)},files={'image_0':('x.png',picture(),'image/png')},follow_redirects=False)
        self.assertEqual(blocked.status_code,303)
        self.assertTrue(blocked.headers['location'].startswith('/admin/login'))
        self.save(count=1);iid=self.rows('SELECT id FROM news_posts')[0]['id'];before=self.rows('SELECT * FROM issue_images')
        with patch('issue_gallery.IssueImagePlan.apply',side_effect=sqlite3.OperationalError('injected')):
            with self.assertRaises(sqlite3.OperationalError):self.save(iid,count=1)
        self.assertEqual(self.rows('SELECT * FROM issue_images'),before)
        self.assertEqual(len(list(self.app.state.image_store.root.joinpath('issues').glob('*'))),1)

    def test_backup_migration_idempotence_and_member_compatibility(self):
        receipt=self.rows('SELECT backup_path FROM schema_migrations WHERE version=?',(VERSION,))[0]['backup_path']
        self.assertTrue(Path(receipt).is_file())
        with closing(sqlite3.connect(receipt)) as conn:
            self.assertNotIn('time_mode',[r[1] for r in conn.execute('PRAGMA table_info(news_posts)')])
        before=self.rows('SELECT * FROM schema_migrations');migrate(self.database)
        self.assertEqual(before,self.rows('SELECT * FROM schema_migrations'))
        self.assertEqual(self.submit(time_mode='all_day').status_code,303)
        row=self.rows('SELECT * FROM news_posts')[0];self.assertEqual(row['time_mode'],'all_day')
        self.assertEqual(self.review(row,time_mode='all_day').status_code,303)
        page=self.visitor.get('/news/'+row['slug']);self.assertIn('<dd>종일</dd>',page.text)
        self.assertNotIn('class="issue-gallery"',page.text)
