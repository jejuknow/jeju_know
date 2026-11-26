"""Member gallery transactions, visibility, ownership and shared decoder policy."""
import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from PIL import Image
from test_issue_gallery import IssueGalleryTests as Base
from test_images import picture


class UserEventGalleryTest(unittest.TestCase):
    setUpClass = classmethod(Base.setUpClass.__func__)
    setUp, tearDown = Base.setUp, Base.tearDown
    csrf, rows, execute, data, review = Base.csrf, Base.rows, Base.execute, Base.data, Base.review
    admin_save = Base.save

    def submit(self, count=0, issue=None, files=None, **changes):
        rows = self.rows('SELECT * FROM issue_images WHERE issue_id=? ORDER BY sort_order', (issue,)) if issue else []
        keys = ['saved:'+str(row['id']) for row in rows]
        data = self.data(csrf_token=self.csrf(self.member), title='회원 이미지 점검', time_mode='unknown',
                         gallery_original=json.dumps(keys), gallery_order=json.dumps(keys+[f'new:{i}' for i in range(count)]))
        data.update(changes)
        if files is None:
            files = ([] if issue else [('image_0', ('poster.png',picture(),'image/png'))])
            files += [(f'gallery_{i}', (f'program-{i}.png',picture(size=(120+i,180)),'image/png')) for i in range(count)]
        return self.member.post(f'/issues/{issue}/edit' if issue else '/issues/new', data=data, files=files or None, follow_redirects=False)

    def test_counts_categories_times_order_and_member_form(self):
        for i, category in enumerate(('행사•축제','전시•팝업','공연','교육·체험','혜택·할인')):
            count = (0,1,5,5,5)[i]
            mode = ('unknown','all_day','specified','all_day','specified')[i]
            response = self.submit(count, category=category, time_mode=mode, start_time='10:00',end_time='18:30',
                                   benefit_summary='도민 할인',source_name='공식 주최',title=f'회원 점검 {i}')
            self.assertEqual(response.status_code,303,response.text[:500])
            row = self.rows('SELECT * FROM news_posts ORDER BY id DESC')[0]
            self.assertEqual((row['approval_status'],row['published'],row['time_mode']),('pending',0,mode))
            photos = self.rows('SELECT * FROM issue_images WHERE issue_id=? ORDER BY sort_order',(row['id'],))
            self.assertEqual([p['sort_order'] for p in photos],list(range(1,count+1)))
            self.assertEqual([p['width'] for p in photos],list(range(120,120+count)))
            self.assertEqual(len(self.rows('SELECT * FROM images WHERE issue_id=?',(row['id'],))),1)
            page = self.member.get(f'/issues/{row["id"]}/edit')
            self.assertIn('data-reorder="false"',page.text)
            self.assertIn('data-preview-url="/issues/image-preview"',page.text)
            for photo in photos:self.assertIn(photo['file_path'],page.text)
            self.execute("UPDATE news_posts SET created_at=datetime('now','-2 minutes')")

    def test_pending_rejected_approved_direct_urls_and_gallery(self):
        self.assertEqual(self.submit(5).status_code,303)
        row = self.rows('SELECT * FROM news_posts')[0]
        photos = self.rows('SELECT file_path FROM images UNION ALL SELECT file_path FROM issue_images')
        urls = ['/uploads/'+p['file_path'] for p in photos]
        for url in urls:
            for client in (self.visitor,self.other):self.assertEqual(client.get(url).status_code,404)
            for client in (self.member,self.admin):
                response=client.get(url);self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers['cache-control'],'no-store')
        for path in ('/','/news','/news?q=회원','/sitemap.xml','/api/places'):
            page=self.visitor.get(path)
            self.assertNotIn(row['slug'],page.text)
            for photo in photos:self.assertNotIn(photo['file_path'],page.text)
        self.assertEqual(self.visitor.get('/news/'+row['slug']).status_code,404)
        self.assertEqual(self.admin.get('/admin').context['admin_counts']['issue_pending'],1)
        self.assertEqual(self.review(row).status_code,303)
        for url in urls:self.assertEqual(self.visitor.get(url).status_code,200)
        page=self.visitor.get('/news/'+row['slug'])
        self.assertIn('class="issue-gallery"',page.text)
        for photo in photos:self.assertIn(photo['file_path'],page.text)
        self.assertIn('content="https://www.jeju-know.com'+urls[0]+'"',page.text)
        self.assertEqual(self.submit(0,issue=row['id']).status_code,403)
        # A separate rejected submission remains private; the owner keeps its preview.
        self.assertEqual(self.submit(1,title='반려 점검',duplicate_ack='1').status_code,303)
        rejected=self.rows('SELECT * FROM news_posts ORDER BY id DESC')[0]
        self.assertEqual(self.admin.post(f'/admin/news/{rejected["id"]}/reject',data={
            'csrf_token':self.csrf(self.admin,'/admin'),'review_version':rejected['review_version']},follow_redirects=False).status_code,303)
        private=self.rows('SELECT file_path FROM issue_images WHERE issue_id=?',(rejected['id'],))[0]
        self.assertEqual(self.visitor.get('/uploads/'+private['file_path']).status_code,404)
        self.assertEqual(self.member.get('/uploads/'+private['file_path']).status_code,200)

    def test_member_removal_admin_reorder_and_cross_owner_denied(self):
        self.assertEqual(self.submit(5).status_code,303)
        row=self.rows('SELECT * FROM news_posts')[0];iid=row['id']
        photos=self.rows('SELECT * FROM issue_images ORDER BY sort_order')
        kept=['saved:'+str(r['id']) for r in photos[1:]]
        self.assertEqual(self.submit(issue=iid,gallery_order=json.dumps(kept)).status_code,303)
        self.assertEqual(self.visitor.get('/uploads/'+photos[0]['file_path']).status_code,404)
        self.assertEqual(self.other.post(f'/issues/{iid}/edit',data=self.data(csrf_token=self.csrf(self.other))).status_code,403)
        self.assertEqual(self.submit(issue=iid,gallery_order='["saved:99999"]').status_code,400)
        version=self.rows('SELECT review_version FROM news_posts WHERE id=?',(iid,))[0]['review_version']
        self.assertEqual(self.admin_save(iid,count=1,review_version=str(version),gallery_order=json.dumps(kept[::-1]+['new:0'])).status_code,303)
        after=self.rows('SELECT * FROM issue_images ORDER BY sort_order')
        self.assertEqual([p['id'] for p in after[:4]],[p['id'] for p in photos[:0:-1]])
        self.assertEqual(len(after),5)
        page=self.admin.get(f'/admin/news/{iid}/edit')
        self.assertIn('data-reorder="true"',page.text)

    def test_sixth_invalid_and_failed_transaction_leave_no_partial_rows_or_files(self):
        self.assertEqual(self.submit(6).status_code,400)
        files=[('image_0',('p.png',picture(),'image/png')),('gallery_0',('good.png',picture(),'image/png')),
               ('gallery_1',('broken.png',b'not an image','image/png'))]
        result=self.submit(2,files=files)
        self.assertEqual(result.status_code,400);self.assertIn('broken.png',result.text)
        self.assertEqual(self.rows('SELECT * FROM news_posts'),[])
        self.assertEqual(self.rows('SELECT * FROM issue_images'),[])
        self.assertEqual(list(self.app.state.image_store.root.joinpath('issues').glob('*')),[])
        with patch('issue_gallery.IssueImagePlan.apply',side_effect=sqlite3.OperationalError('injected')):
            with self.assertRaises(sqlite3.OperationalError):self.submit(5)
        self.assertEqual(self.rows('SELECT * FROM news_posts'),[])
        self.assertEqual(list(self.app.state.image_store.root.joinpath('issues').glob('*')),[])

    def test_preview_auth_csrf_formats_limits_and_exif(self):
        url='/issues/image-preview';token=self.csrf(self.member)
        payload={'image_0':('p.png',picture(),'image/png')}
        self.assertEqual(self.visitor.post(url,files=payload,follow_redirects=False).status_code,303)
        self.assertEqual(self.member.post(url,files=payload).status_code,400)
        for fmt,mime in [('JPEG','image/jpeg'),('PNG','image/png'),('WEBP','image/webp')]:
            exif=Image.Exif();exif[274]=6;exif[270]='private metadata'
            response=self.member.post(url,data={'csrf_token':token},files={'image_0':('phone.heic',picture(fmt,exif=exif),mime)})
            self.assertEqual(response.status_code,200,response.text[:100] if response.status_code!=200 else '')
            with Image.open(io.BytesIO(response.content)) as image:
                self.assertEqual(image.format,'WEBP');self.assertEqual(image.size,(180,120));self.assertFalse(image.getexif())
        for limit,target in [('MAX_FILE_BYTES',16),('MAX_PIXELS',10)]:
            with patch('image_uploads.'+limit,target):
                self.assertEqual(self.member.post(url,data={'csrf_token':token},files=payload).status_code,400)
        for name,raw,mime in [('bad.jpg',b'broken','image/jpeg'),('fake.png',picture(),'text/html'),('p.gif',picture('GIF'),'image/gif')]:
            response=self.member.post(url,data={'csrf_token':token},files={'image_0':(name,raw,mime)})
            self.assertEqual(response.status_code,400);self.assertIn(name,response.json()['detail'])
        self.assertEqual(list(self.app.state.image_store.root.joinpath('issues').glob('*')),[])

    def test_large_valid_multi_file_body_is_allowed_and_over_limit_rejected(self):
        raw=picture('JPEG')+b'\0'*(6*1024*1024)
        files=[('image_0',('p.jpg',raw,'image/jpeg'))]+[(f'gallery_{i}',(f'{i}.jpg',raw,'image/jpeg')) for i in range(5)]
        self.assertEqual(self.submit(5,files=files).status_code,303)
        self.assertEqual(len(self.rows('SELECT * FROM issue_images')),5)
        with patch('auth_security.MAX_FILE_BYTES',100):
            self.assertEqual(self.submit(5,files=files).status_code,413)

