"""Smartphone input, four-slot member uploads, and existing-data regression coverage."""
import io
import struct
import unittest
import zlib
from unittest.mock import patch

from PIL import Image
import test_community
from auth_security import hash_password
from image_uploads import optimize_image, ImageError
from image_limits import MAX_FILE_BYTES, MAX_PIXELS, FORM_BYTES
from legacy_images import add_fifth


def photo(fmt='JPEG', size=(800,600), exif=None):
    with Image.new('RGB',size,(35,110,170)) as image:
        result=io.BytesIO()
        image.save(result,fmt,quality=80,**({'exif':exif} if exif else {}))
        return result.getvalue()


class PhoneImageTest(unittest.TestCase):
    def test_48mp_jpeg_and_heif_orientation_and_metadata(self):
        for fmt,mime,ext in [('JPEG','image/jpeg','jpg'),('HEIF','image/heic','heic')]:
            exif=Image.Exif();exif[274]=6;exif[270]='private test metadata';exif[34853]={1:'N',2:(33,30,0),3:'E',4:(126,30,0)}
            # HEIF uses container transforms, not JPEG's EXIF-only rotation.
            # Encode canonical portrait pixels for HEIF; JPEG exercises EXIF rotation.
            if fmt == 'HEIF': exif[274]=1
            raw=photo(fmt,(6048,8064) if fmt == 'HEIF' else (8064,6048),exif)
            output,w,h=optimize_image(raw,'phone.'+ext,mime)
            self.assertEqual((w,h),(1440,1920),fmt)
            self.assertLess(len(output),3*1024*1024)
            with Image.open(io.BytesIO(output)) as image:
                self.assertEqual(image.format,'WEBP')
                self.assertFalse(image.getexif())
                self.assertFalse(any(key in image.info for key in ('exif','xmp','icc_profile')))

    def test_over_ten_mb_and_generic_mobile_mime(self):
        raw=photo()+b'\0'*(11*1024*1024)
        output,w,h=optimize_image(raw,'phone.jpg','application/octet-stream')
        self.assertEqual((w,h),(800,600));self.assertLess(len(output),len(raw))
        self.assertTrue(optimize_image(photo('HEIF'),'phone.heif','')[0])

    def test_invalid_mime_signature_truncation_and_pixel_bomb(self):
        raw=bytearray(photo('PNG'))
        raw[16:24]=struct.pack('>II',10000,6000)
        raw[29:33]=struct.pack('>I',zlib.crc32(raw[12:29]))
        cases=[(bytes(raw),'bomb.png','image/png'),
               (photo(),'fake.jpg','text/html'),(b'broken','bad.heic','image/heic'),
               (photo('HEIF')[:40],'cut.heic','image/heic')]
        for data,name,mime in cases:
            with self.subTest(name=name),self.assertRaises(ImageError):optimize_image(data,name,mime)


class MemberMobileUploadTest(unittest.TestCase):
    setUp=test_community.CommunityTest.setUp
    tearDown=test_community.CommunityTest.tearDown
    rows=test_community.CommunityTest.rows
    execute=test_community.CommunityTest.execute
    csrf=test_community.CommunityTest.csrf
    post=test_community.CommunityTest.post
    form=test_community.CommunityTest.form
    create=test_community.CommunityTest.create
    place=test_community.CommunityTest.place

    @classmethod
    def setUpClass(cls):cls.password_hash=hash_password('community-test-secret')

    def submit(self, files, **changes):
        return self.member.post('/map/register',data={**self.form(), 'csrf_token':self.csrf(self.member),**changes},files=files,follow_redirects=False)

    def test_four_images_coordinates_find_new_map_and_restart(self):
        files=[(f'image_{n}',('phone.heic',photo('HEIF'),'image/heic')) for n in range(4)]
        response=self.submit(files,latitude='33.4507',longitude='126.5707',opened_year=str(self.now.year),opened_month=self.now.month)
        self.assertEqual(response.status_code,303,response.text)
        p=self.rows('SELECT * FROM places')[0]
        self.assertEqual((p['latitude'],p['longitude']),(33.4507,126.5707))
        images=self.rows('SELECT * FROM images ORDER BY sort_order')
        self.assertEqual(len(images),4)
        for query in ('','?new=current','?category=cafe'):
            items=self.visitor.get('/api/places'+query).json()
            self.assertEqual(items[0]['id'],p['id']);self.assertTrue(items[0]['has_coordinates'])
        html=self.visitor.get(response.headers['location']).text
        for image in images:self.assertIn('/uploads/'+image['file_path'],html)
        from app import create_app
        create_app(self.config)
        self.assertEqual(images,self.rows('SELECT * FROM images ORDER BY sort_order'))
        self.assertTrue(all(self.app.state.image_store.path(i['file_path']).is_file() for i in images))

    def test_member_fifth_upload_rejected_but_existing_fifth_and_map_url_preserved(self):
        files=[(f'image_{n}',('photo.jpg',photo(),'image/jpeg')) for n in range(5)]
        self.assertEqual(self.submit(files).status_code,400)
        self.assertEqual(self.rows('SELECT * FROM places'),[])
        p=self.place(map_url='https://map.kakao.com/example')
        response=self.admin.post(f"/admin/places/{p['id']}/edit",data={**self.form(), 'csrf_token':self.csrf(self.admin,'/admin'),'published':'1','map_url':p['map_url']},files=files[:4],follow_redirects=False)
        self.assertEqual(response.status_code,303)
        add_fifth(self.app.state.image_store, self.database, p['id'])
        before=self.rows('SELECT * FROM images ORDER BY sort_order')
        html=self.member.get(f"/places/{p['id']}/edit").text
        self.assertEqual(html.count('type="file"'),4)
        self.assertIn('기존 추가사진 1장 보존 중',html)
        self.assertNotIn('name="map_url"',html)
        response=self.post(self.member,f"/places/{p['id']}/edit",**self.form(name='수정된 이름'))
        self.assertEqual(response.status_code,303)
        self.assertEqual(before,self.rows('SELECT * FROM images ORDER BY sort_order'))
        self.assertEqual(self.rows('SELECT map_url FROM places')[0]['map_url'],p['map_url'])

    def test_form_and_total_request_limit(self):
        html=self.member.get('/map/register').text
        self.assertEqual(html.count('type="file"'),4)
        self.assertIn('data-auto-address',html)
        self.assertNotIn('capture=',html)
        self.assertNotIn('2,500만',html)
        self.assertNotIn('name="map_url"',html)
        response=self.member.post('/map/register',content=b'bad',headers={'Content-Type':'multipart/form-data; boundary=test','Content-Length':str(4*MAX_FILE_BYTES+FORM_BYTES+1)},follow_redirects=False)
        self.assertEqual(response.status_code,413)

    def test_disk_failure_has_clear_error_and_no_partial_content(self):
        with patch.object(self.app.state.image_store,'write',side_effect=OSError('disk test')):
            response=self.submit([('image_0',('photo.jpg',photo(),'image/jpeg'))])
        self.assertEqual(response.status_code,400)
        self.assertIn('사진 업로드 중 문제가 발생했습니다',response.text)
        self.assertEqual(self.rows('SELECT * FROM places'),[])
