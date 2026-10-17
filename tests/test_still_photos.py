"""Phone JPEG/MPO stills must not be confused with timed image animations."""
import io
import unittest
from unittest.mock import patch

from PIL import Image, JpegImagePlugin
from image_uploads import ImageError, optimize_image


def encoded_photo(fmt, *, multiple=False, **options):
    with Image.new('RGB', (120, 180), (220, 20, 20)) as primary, \
            Image.new('RGB', (120, 180), (20, 20, 220)) as other:
        output = io.BytesIO()
        if multiple:
            options.update(save_all=True, append_images=[other])
        primary.save(output, fmt, **options)
        return output.getvalue()


class StillPhotoTest(unittest.TestCase):
    def assert_still(self, raw, name, mime, blue=False):
        result, width, height = optimize_image(raw, name, mime)
        with Image.open(io.BytesIO(result)) as output:
            self.assertEqual(output.format, 'WEBP')
            self.assertEqual(getattr(output, 'n_frames', 1), 1)
            self.assertEqual((width, height), (120, 180))
            self.assertFalse(output.getexif())
            r, _, b = output.convert('RGB').getpixel((60, 90))
            self.assertGreater(b if blue else r, r if blue else b)

    def test_mpo_jpeg_extracts_primary_still_despite_animation_flag(self):
        raw = encoded_photo('MPO', multiple=True)
        with Image.open(io.BytesIO(raw)) as decoded:
            self.assertEqual(decoded.format, 'MPO')
            self.assertEqual(decoded.n_frames, 2)
            self.assertTrue(decoded.is_animated)
        for name, mime in [('IMG_1234.JPG', 'image/jpeg'), ('Live Photo.HEIC', 'image/heic'),
                           ('photo', 'application/octet-stream')]:
            with self.subTest(name=name):
                self.assert_still(raw, name, mime)

    def test_heif_uses_primary_not_auxiliary_image(self):
        self.assert_still(encoded_photo('HEIF', multiple=True, primary_index=1),
                          'Live Photo.HEIC', 'image/heic-sequence', blue=True)

    def test_supported_stills_and_mismatched_mobile_hints(self):
        exif = Image.Exif()
        exif[270] = 'Live Photo still metadata'
        for fmt in ('JPEG', 'PNG', 'WEBP', 'HEIF'):
            with self.subTest(fmt=fmt):
                self.assert_still(encoded_photo(fmt, exif=exif), 'Live Photo.HEIC', 'image/heic')

    def test_boolean_animation_hint_alone_does_not_reject_jpeg(self):
        with patch.object(JpegImagePlugin.JpegImageFile, 'is_animated', True, create=True):
            self.assert_still(encoded_photo('JPEG'), 'photo.jpg', 'image/jpeg')

    def test_actual_animation_frames_are_rejected(self):
        for fmt, mime in [('WEBP', 'image/webp'), ('PNG', 'image/png'), ('GIF', 'image/gif')]:
            with self.subTest(fmt=fmt):
                raw = encoded_photo(fmt, multiple=True, duration=100, loop=0)
                with Image.open(io.BytesIO(raw)) as decoded:
                    self.assertGreater(decoded.n_frames, 1)
                with self.assertRaisesRegex(ImageError, '애니메이션'):
                    optimize_image(raw, 'disguised.jpg', mime)

    def test_unsupported_corrupt_and_oversized_errors_are_distinct(self):
        for raw, expected in [(encoded_photo('GIF'), '지원하지 않는'),
                              (b'\xff\xd8broken', '손상'), (b'<svg/>', '지원되지 않는')]:
            with self.subTest(expected=expected), self.assertRaisesRegex(ImageError, expected):
                optimize_image(raw, 'photo.jpg', 'image/jpeg')
        with patch('image_uploads.MAX_FILE_BYTES', 10), self.assertRaisesRegex(ImageError, '너무 큽니다'):
            optimize_image(encoded_photo('JPEG'), 'photo.jpg', 'image/jpeg')


if __name__ == '__main__':
    unittest.main()
