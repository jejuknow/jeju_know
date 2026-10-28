import unittest

from business_hours import normalize_business_time, business_hours_range, csv_business_hours


class BusinessHoursTest(unittest.TestCase):
    def test_supported_formats_and_rounding(self):
        for raw, expected in [('09:30', '09:30'), ('9:30', '09:30'), ('09:30:00', '09:30'),
                              ('9:30:59', '09:30'), ('0.395833', '09:30'), ('0.666667', '16:00'),
                              ('0.416667', '10:00'), ('0.75', '18:00'), ('0', '00:00'),
                              ('0.999999', '00:00'), (' 8:00 ', '08:00')]:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_business_time(raw), expected)

    def test_invalid_times(self):
        for raw in ('', '24:00', '09:60', '09:30:60', '9:3', '9시30분', '-0.5', '1', '1.5', 'NaN', 'Infinity', '12:00 PM'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_business_time(raw)
        with self.assertRaisesRegex(ValueError, "영업 시작시간 '9시30분'"):
            business_hours_range('9시30분', '16:00')

    def test_status_and_missing_endpoint(self):
        for status, start, end, expected in [
            ('시간 입력', '9:30', '16:00', '09:30 ~ 16:00'),
            ('시간 입력', '09:30:00', '16:00:00', '09:30 ~ 16:00'),
            ('시간 입력', '0.395833', '0.666667', '09:30 ~ 16:00'),
            ('미확인', '', '', ''), ('', '09:30', '16:00', '09:30 ~ 16:00'), ('', '', '', '')]:
            with self.subTest(status=status, start=start):
                self.assertEqual(csv_business_hours({'영업시간_상태': status, '영업_시작': start, '영업_종료': end}), expected)
        for status in ('', '시간 입력'):
            for start, end, label in [('09:30', '', '종료'), ('', '16:00', '시작')]:
                with self.assertRaisesRegex(ValueError, f'영업 {label}시간이 비어'):
                    csv_business_hours({'영업시간_상태': status, '영업_시작': start, '영업_종료': end})
        for row in ({'영업시간_상태': '모름'}, {'영업시간_상태': '미확인', '영업_시작': '10:00'}):
            with self.assertRaises(ValueError):
                csv_business_hours(row)
        self.assertIsNone(csv_business_hours({}, previous={'business_hours': '기존 시간'}))

    def test_reported_six_places(self):
        cases = [('아구아비바', '9:30', '16:00', '09:30 ~ 16:00'),
                 ('무우수 커피 로스터스', '8:00', '17:00', '08:00 ~ 17:00'),
                 ('동림', '10:00', '18:00', '10:00 ~ 18:00'),
                 ('김녕에서는김영훈', '9:30', '17:30', '09:30 ~ 17:30'),
                 ('부이크브루어스', '10:00', '17:00', '10:00 ~ 17:00'),
                 ('한동리화수', '9:00', '17:00', '09:00 ~ 17:00')]
        for name, start, end, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(business_hours_range(start, end), expected)
