"""Cover uploads use existing storage, preserve data, and honor private-content access."""
import io
import sqlite3
import unittest
from unittest.mock import patch

from PIL import Image
from image_uploads import ImageStore
from test_best_editor import BestEditorTests as Base


def photo(fmt='JPEG', size=(320,180)):
    buffer = io.BytesIO()
    Image.new('RGB', size, '#3864ee').save(buffer, fmt)
    return buffer.getvalue()


class BestCoverTests(unittest.TestCase):
    setUpClass = classmethod(Base.setUpClass.__func__)
    setUp = Base.setUp
    tearDown = Base.tearDown
    rows = Base.rows
    execute = Base.execute
    csrf = Base.csrf
    post = Base.post
    place = Base.place

    def row(self): return self.rows('SELECT * FROM best_collections WHERE id=?',(self.cid,))[0]

    def save(self, file=None, url=None, client=None, **changes):
        client = client or self.admin
        old = self.row()
        data = dict(csrf_token=self.csrf(client,'/admin' if client is self.admin else '/account'),
                    title=old['title'],description=old['description'],image_url=old['image_url'],
                    original_image=old['image_url'],sort_order='3',published='1')
        data.update(changes)
        return client.post(url or f'/admin/best/{self.cid}/edit', data=data,
                           files={'image_0':file} if file else None,follow_redirects=False)

    def preview(self, file, **data):
        return self.admin.post('/admin/best/image-preview',data={'csrf_token':self.csrf(self.admin,'/admin'),**data},files={'image_0':file},follow_redirects=False)

    def test_upload_and_replace_reflect_in_public_and_og_without_schema_changes(self):
        schema=self.rows('SELECT * FROM sqlite_master ORDER BY name')
        pid=self.place('보존 장소'); self.execute('INSERT INTO best_collection_places VALUES(?,?,?)',(self.cid,pid,7))
        before=self.rows('SELECT * FROM places'),self.rows('SELECT * FROM best_collection_places')
        response=self.save(('한글사진.JPG',photo(),'image/jpeg')); self.assertEqual(response.status_code,303,response.text)
        first=self.row()['image_url']; self.assertRegex(first,r'^/uploads/best/[0-9a-f]{32}\.webp$')
        response=self.visitor.get(first); self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['content-type'],'image/webp')
        self.assertEqual(response.headers['x-content-type-options'],'nosniff')
        with Image.open(io.BytesIO(response.content)) as im: self.assertEqual((im.format,im.size),('WEBP',(320,180)))
        response=self.save(('portrait.png',photo('PNG',(180,500)),'image/png')); self.assertEqual(response.status_code,303)
        second=self.row()['image_url']; self.assertNotEqual(first,second)
        self.assertTrue(self.app.state.image_store.path(first.removeprefix('/uploads/')).is_file())
        self.assertIn(second,self.visitor.get('/best').text)
        detail=self.visitor.get('/best/qa-best').text
        self.assertIn('best-detail-cover',detail); self.assertIn('https://www.jeju-know.com'+second,detail)
        self.assertEqual(before,(self.rows('SELECT * FROM places'),self.rows('SELECT * FROM best_collection_places')))
        self.assertEqual(schema,self.rows('SELECT * FROM sqlite_master ORDER BY name'))

    def test_preview_validates_without_persistent_file_or_db_write(self):
        before=self.row(); root=self.app.state.image_store.root
        files=set(root.rglob('*'))
        # Safari may send JPEG pixels under HEIC name and sequence MIME.
        response=self.preview(('IMG.HEIC',photo(),'image/heic-sequence'))
        self.assertEqual(response.status_code,200,response.text[:100] if response.status_code!=200 else '')
        self.assertEqual(response.headers['content-type'],'image/webp')
        self.assertEqual(self.row(),before); self.assertEqual(set(root.rglob('*')),files)

    def test_invalid_files_and_limits_keep_old_cover(self):
        self.save(('a.jpg',photo(),'image/jpeg')); before=self.row()
        files=set(self.app.state.image_store.root.rglob('*.webp'))
        for file in [('bad.jpg',b'MZ executable','image/jpeg'),('bad.png',b'\x89PNGbroken','image/png'),('a.jpg',photo(),'text/html'),('a.svg',b'<svg><script/></svg>','image/svg+xml')]:
            with self.subTest(name=file[0]): self.assertEqual(self.save(file).status_code,400)
        with patch('image_uploads.MAX_FILE_BYTES',10): self.assertIn('너무 큽니다',self.save(('a.jpg',photo(),'image/jpeg')).text)
        with patch('image_uploads.MAX_PIXELS',10): self.assertIn('화소',self.save(('a.jpg',photo(),'image/jpeg')).text)
        self.assertEqual(self.row(),before); self.assertEqual(set(self.app.state.image_store.root.rglob('*.webp')),files)

    def test_animation_rejected_by_actual_decoder(self):
        images=[Image.new('RGB',(40,40),c) for c in ('red','blue')]
        for fmt,mime in [('WEBP','image/webp'),('PNG','image/png'),('GIF','image/gif')]:
            buffer=io.BytesIO(); images[0].save(buffer,fmt,save_all=True,append_images=images[1:],duration=100,loop=0)
            response=self.preview(('still.jpg',buffer.getvalue(),mime))
            self.assertEqual(response.status_code,400); self.assertIn('애니메이션',response.text)

    def test_db_failure_discards_only_new_upload(self):
        self.save(('a.jpg',photo(),'image/jpeg')); before=self.row()
        files=set(self.app.state.image_store.root.rglob('*.webp'))
        with patch('admin_ui.audit',side_effect=sqlite3.OperationalError('test rollback')):
            response=self.save(('b.jpg',photo(),'image/jpeg'))
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.row(),before); self.assertEqual(set(self.app.state.image_store.root.rglob('*.webp')),files)

    def test_remove_shared_copy_retains_file_and_fallback(self):
        self.save(('a.jpg',photo(),'image/jpeg')); url=self.row()['image_url']
        self.post(self.admin,f'/admin/best/{self.cid}/copy')
        copied=self.rows('SELECT * FROM best_collections ORDER BY id DESC')[0]
        self.assertEqual(copied['image_url'],url)
        self.assertEqual(self.save(remove_image_0='1').status_code,303)
        self.assertEqual(self.row()['image_url'],''); self.assertIsNone(self.row()['deleted_at'])
        self.assertTrue(self.app.state.image_store.path(url.removeprefix('/uploads/')).is_file())
        self.assertEqual(self.visitor.get(url).status_code,404) # remaining copy is private
        self.assertEqual(self.admin.get(url).status_code,200)
        detail=self.visitor.get('/best/qa-best').text
        self.assertNotIn('class="best-detail-cover"',detail); self.assertNotIn(url,detail)
        self.assertIn('collection-emoji',self.visitor.get('/best').text)

    def test_new_private_cover_survives_store_restart_and_paths_are_guarded(self):
        response=self.save(('../../한글.jpg',photo(),'image/jpeg'),url='/admin/best/new',published='0')
        self.assertEqual(response.status_code,303,response.text)
        row=self.rows('SELECT * FROM best_collections ORDER BY id DESC')[0]; url=row['image_url']
        self.assertEqual(row['published'],0); self.assertEqual(self.visitor.get(url).status_code,404)
        self.assertEqual(self.member.get(url).status_code,404); self.assertEqual(self.admin.get(url).status_code,200)
        store=self.app.state.image_store; reopened=ImageStore(store.root,store.connect_db); reopened.collect()
        self.assertTrue(reopened.path(url.removeprefix('/uploads/')).is_file())
        self.assertEqual(self.admin.get('/uploads/best/'+'a'*32+'.webp').status_code,404)
        self.assertEqual(self.save(image_url='/uploads/best/../../secret').status_code,400)

    def test_csrf_permissions_and_request_size(self):
        before=self.row()
        for client in (self.member,self.visitor):
            response=client.post(f'/admin/best/{self.cid}/edit',files={'image_0':('a.jpg',photo(),'image/jpeg')},follow_redirects=False)
            self.assertIn(response.status_code,(303,403))
            response=client.post('/admin/best/image-preview',files={'image_0':('a.jpg',photo(),'image/jpeg')},follow_redirects=False)
            self.assertIn(response.status_code,(303,403))
        self.assertEqual(self.save(('a.jpg',photo(),'image/jpeg'),csrf_token='bad').status_code,400)
        self.assertEqual(self.preview(('a.jpg',photo(),'image/jpeg'),csrf_token='bad').status_code,400)
        response=self.admin.post(f'/admin/best/{self.cid}/edit',content=b'x',headers={'Content-Type':'multipart/form-data; boundary=x','Content-Length':str(33*1024*1024)})
        self.assertEqual(response.status_code,413); self.assertEqual(before,self.row())

    def test_stale_edit_and_missing_collection_roll_back_files(self):
        self.save(('a.jpg',photo(),'image/jpeg')); before=self.row()
        files=set(self.app.state.image_store.root.rglob('*.webp'))
        self.assertEqual(self.save(('b.jpg',photo(),'image/jpeg'),original_image='').status_code,409)
        self.assertEqual(self.save(('b.jpg',photo(),'image/jpeg'),url='/admin/best/999999/edit').status_code,404)
        self.assertEqual(self.row(),before); self.assertEqual(set(self.app.state.image_store.root.rglob('*.webp')),files)

    def test_fetch_save_preserves_success_notice_for_final_navigation(self):
        response=self.admin.post(f'/admin/best/{self.cid}/edit',data={
            'csrf_token':self.csrf(self.admin,'/admin'),'title':'저장 완료 안내','sort_order':'0'},
            files={'image_0':('cover.jpg',photo(),'image/jpeg')},headers={'X-Requested-With':'fetch'})
        self.assertEqual(response.status_code,200)
        self.assertIn('대표 이미지를 변경했습니다.',self.admin.get(response.json()['redirect']).text)

    def test_legacy_url_and_legacy_upload_shared_reference_survive_gc(self):
        external='https://example.com/legacy.jpg'
        self.execute('UPDATE best_collections SET image_url=? WHERE id=?',(external,self.cid))
        self.assertEqual(self.save().status_code,303); self.assertEqual(self.row()['image_url'],external)
        self.assertIn(external,self.admin.get(f'/admin/best/{self.cid}/edit').text)
        store=self.app.state.image_store; relative='places/'+'b'*32+'.webp'
        store.write(relative,photo('WEBP'))
        self.execute('UPDATE best_collections SET image_url=? WHERE id=?',('/uploads/'+relative,self.cid))
        self.execute('INSERT INTO image_file_gc VALUES (?)',(relative,)); store.collect()
        self.assertTrue(store.path(relative).is_file()); self.assertEqual(self.visitor.get('/uploads/'+relative).status_code,200)


if __name__ == '__main__': unittest.main()
