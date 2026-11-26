"""Closing summaries respect filtered public data without changing main ordering."""
import re
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from community_migrations import KST
from issue_discovery import PUBLIC_CATEGORIES
from test_issue_priority import IssuePriorityTest as Base


class EventCompactTest(unittest.TestCase):
    setUpClass = classmethod(Base.setUpClass.__func__)
    setUp, tearDown = Base.setUp, Base.tearDown
    execute, rows, issue = Base.execute, Base.rows, Base.issue
    csrf = Base.csrf
    instant = datetime(2026, 11, 26, 12, tzinfo=KST)

    def page(self, **params):
        with patch('app.issue_now', return_value=self.instant):
            response = self.visitor.get('/news', params=params)
        self.assertEqual(response.status_code, 200)
        return response

    def test_end_instant_sort_limit_visibility_and_no_writes(self):
        # Registration order differs from end order; upcoming events can also end soon.
        self.issue('d3', '2026-11-28', '2026-11-29')
        self.issue('d2', '2026-11-26', '2026-11-28')
        self.issue('d1', '2026-11-27', '2026-11-27')
        self.issue('today-early', '2026-11-25', '2026-11-26', end_time='14:00')
        self.issue('today-late', '2026-11-25', '2026-11-26', end_time='18:00', created_at='2026-11-26')
        for slug, changes in {'private':{'published':0}, 'pending':{'approval_status':'pending'},
                              'rejected':{'approval_status':'rejected'}, 'deleted':{'deleted_at':'2026-11-26'},
                              'legacy':{'category':'지역소식'}, 'ended-today':{'end_time':'11:30'}}.items():
            self.issue(slug, '2026-11-25', '2026-11-26', **changes)
        self.issue('d4', '2026-11-26', '2026-11-30')
        self.issue('ended-yesterday', '2026-11-25', '2026-11-25')
        before = self.rows('SELECT * FROM news_posts ORDER BY id')
        schema = self.rows('SELECT * FROM sqlite_master ORDER BY name')
        page = self.page()
        self.assertEqual([x['slug'] for x in page.context['ending_items']], ['today-early','today-late','d1','d2'])
        self.assertEqual([x['end_badge'] for x in page.context['ending_items']], ['오늘 종료','오늘 종료','종료 D-1','종료 D-2'])
        self.assertIn('d4', [x['slug'] for x in page.context['news_items']])
        self.assertEqual(before, self.rows('SELECT * FROM news_posts ORDER BY id'))
        self.assertEqual(schema, self.rows('SELECT * FROM sqlite_master ORDER BY name'))

    def test_filters_and_legacy_urls_share_one_result_set(self):
        self.issue('performance-weekend', '2026-11-28', '2026-11-29', category='공연',fee_status='free')
        self.issue('performance-today', '2026-11-26', '2026-11-26', category='공연',region='조천',fee_status='paid')
        self.issue('benefit', '2026-11-26', '2026-11-27', category='혜택·할인',fee_status='free')
        for params, expected in [({'category':'performance','period':'weekend','region':'애월','q':'weekend','view':'map'},['performance-weekend']),
                                 ({'category':'benefit-discount'},['benefit']),
                                 ({'period':'today','free':'1'},['benefit']),
                                 ({'period':'week','closing':'1'},['performance-today','benefit','performance-weekend'])]:
            page = self.page(**params)
            self.assertEqual([x['slug'] for x in page.context['ending_items']], expected)
            self.assertEqual(page.context['map_count'], len(expected))
            link = page.context['issue_link'](closing='1')
            for key,value in params.items():
                self.assertEqual(page.context['selected'][key], '공연' if value=='performance' else '혜택·할인' if value=='benefit-discount' else value)
            self.assertIn('closing=1', link)
            self.assertNotIn('aria-label="빠른 조건"', page.text)
            self.assertNotIn('이번 주 제주', page.text)
        for params in ({'free':'1'}, {'closing':'1'}, {'free':'1','closing':'1'}):
            page = self.page(**params)
            for key in params:self.assertIn(key+'=1', page.context['issue_link'](period='week'))

    def test_compact_content_links_ads_and_empty_section(self):
        self.issue('sponsor', '2026-11-26', '2026-11-27', category='공연',title='공연 <안내>',
                   image_url='https://www.jeju-know.com/static/images/icons/icon-issue.webp',venue='제주 공연장',
                   summary='긴 소개문을 표시하지 않습니다',source_name='주최 측 이름',is_sponsored=1)
        self.issue('promoted', '2026-11-26', '2026-11-28', category='혜택·할인',is_promoted=1)
        page = self.page()
        section = re.search(r'<section class="event-selection".*?</section>', page.text, re.S)[0]
        for value in ('광고·협찬','홍보·제휴','D-1','제주 공연장','alt="공연 &lt;안내&gt;"','/news/sponsor'):
            self.assertIn(value, section)
        for value in ('긴 소개문','주최 측 이름','상세보기','event-card-action'):
            self.assertNotIn(value, section)
        self.assertEqual(section.count('class="event-ending-card"'),2)
        empty = self.page(q='no matching event')
        self.assertEqual(empty.context['ending_items'],[])
        self.assertIn('id="eventHighlights"></div>',empty.text)
        self.assertNotIn('id="endingSoonTitle"',empty.text)

    def test_all_categories_d3_inclusive_and_kst_day_boundary(self):
        for i,category in enumerate(PUBLIC_CATEGORIES.values()):
            self.issue('category-'+str(i), '2026-11-29', '2026-11-29', category=category)
            page = self.page(category=category)
            self.assertEqual([x['slug'] for x in page.context['ending_items']], ['category-'+str(i)])
            self.assertEqual(page.context['ending_items'][0]['end_badge'],'종료 D-3')
        self.issue('midnight','2026-11-26','2026-11-26')
        for instant,expected in [(datetime(2026,11,26,14,59,59,tzinfo=timezone.utc),1),
                                 (datetime(2026,11,26,15,tzinfo=timezone.utc),0)]:
            with patch('app.issue_now',return_value=instant):
                page=self.visitor.get('/news?q=midnight')
                self.assertEqual(len(page.context['ending_items']),expected)
