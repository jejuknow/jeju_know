"""Public grouping changes queries, never stored categories or publication flags."""
import re
import unittest
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from community_migrations import KST
from issue_discovery import EVENT_CATEGORIES, ISSUE_MARKERS, LABELS
from test_issue_priority import IssuePriorityTest as Base


class EventGroupsTest(unittest.TestCase):
    setUpClass = classmethod(Base.setUpClass.__func__)
    setUp, tearDown = Base.setUp, Base.tearDown
    execute, rows, issue, csrf = Base.execute, Base.rows, Base.issue, Base.csrf
    instant = datetime(2026, 11, 27, 12, tzinfo=KST)

    def page(self, **query):
        with patch('app.issue_now', return_value=self.instant):
            page = self.visitor.get('/news', params=query)
        self.assertEqual(page.status_code, 200)
        return page

    def seed(self):
        for key, label in LABELS.items():
            self.issue(key, '2026-11-27', '2026-11-29', category=label)
        self.issue('sponsor', '2026-11-27', '2026-11-28', category='혜택·할인', is_sponsored=1)
        for slug, changes in {'private':{'published':0}, 'pending':{'approval_status':'pending'},
                              'rejected':{'approval_status':'rejected'}, 'deleted':{'deleted_at':'2026-11-27'}}.items():
            self.issue(slug, '2026-11-27', '2026-11-28', category='혜택·할인', **changes)

    def test_group_query_counts_closing_and_data_preservation(self):
        self.seed()
        before = self.rows('SELECT * FROM news_posts ORDER BY id')
        schema = self.rows('SELECT * FROM sqlite_master ORDER BY name')
        for group, expected in [('event', set(EVENT_CATEGORIES)), ('benefit', {'benefit-discount','sponsor'})]:
            page = self.page(group=group)
            self.assertEqual({x['slug'] for x in page.context['news_items']}, expected)
            self.assertEqual({x['slug'] for x in page.context['ending_items']}, expected)
            self.assertEqual(page.context['map_count'], len(expected))
            self.assertIn('곧 끝나는 혜택' if group=='benefit' else '곧 끝나요', page.text)
            self.assertEqual('aria-label="카테고리"' in page.text, group=='event')
        self.assertEqual(self.page().context['selected']['group'], 'event')
        self.assertEqual(before, self.rows('SELECT * FROM news_posts ORDER BY id'))
        self.assertEqual(schema, self.rows('SELECT * FROM sqlite_master ORDER BY name'))
        with patch('app.issue_now', return_value=self.instant):
            self.assertEqual(self.visitor.get('/news/local').status_code, 200)
            for slug in ('pending','private','rejected','deleted'):
                self.assertEqual(self.visitor.get('/news/'+slug).status_code, 404)

    def test_legacy_urls_normalization_and_switch_keep_shared_filters(self):
        self.seed()
        for category in ('benefit-discount','혜택·할인'):
            page = self.page(category=category, period='weekend', region='애월', q='benefit', view='map')
            self.assertEqual(page.context['selected']['group'], 'benefit')
            self.assertEqual([x['slug'] for x in page.context['news_items']], ['benefit-discount'])
            link = page.context['issue_link'](group='event', category='')
            query = parse_qs(urlsplit(link).query)
            self.assertNotIn('category', query)
            for key in ('period','region','q','view'):
                self.assertEqual(query[key], [page.context['selected'][key]])
        for category in ('performance','공연','체험•교육'):
            self.assertEqual(self.page(category=category, group='benefit').context['selected']['group'], 'event')
        self.assertEqual(self.page(view='benefit').context['selected']['group'], 'benefit')
        self.assertEqual(self.page(group='invalid').context['selected']['group'], 'event')
        self.assertEqual(self.page(category="' OR 1=1 --").context['news_items'], [])

    def test_intersections_and_unmapped_count(self):
        self.issue('performance', '2026-11-28', '2026-11-29', category='공연', fee_status='free')
        self.issue('offer', '2026-11-27', '2026-11-29', category='혜택·할인', fee_status='free',
                   venue='할인 장소', show_on_map=0)
        self.issue('other-region', '2026-11-27', '2026-11-29', category='혜택·할인', region='조천')
        for group, category, keyword, mapped in [('event','performance','performance',1), ('benefit','','할인 장소',0)]:
            for period in ('all','week','weekend','month'):
                page = self.page(group=group, category=category, period=period, region='애월', q=keyword, free='1', closing='1')
                self.assertEqual(len(page.context['news_items']),1)
                self.assertEqual(page.context['map_count'],mapped)
                self.assertEqual(len(page.context['ending_items']),1)
        self.assertEqual(self.page(group='benefit',period='today').context['map_count'],1)

    def test_disclosure_marker_legend_and_canonical(self):
        self.seed()
        event, benefit = self.page(), self.page(group='benefit')
        cards = re.findall(r'<article[^>]*class="issue-card".*?</article>', benefit.text, re.S)
        normal = next(c for c in cards if '/news/benefit-discount' in c)
        paid = next(c for c in cards if '/news/sponsor' in c)
        self.assertNotIn('광고·협찬', normal)
        self.assertIn('data-disclosure>광고·협찬', paid)
        for page in (event,benefit):
            self.assertIn('rel="canonical" href="https://www.jeju-know.com/news"',page.text)
            self.assertIn('aria-label="콘텐츠 탐색"',page.text)
            tabs = re.search(r'<nav class="issue-group-tabs".*?</nav>',page.text,re.S)[0]
            self.assertEqual(tabs.count('aria-current="page"'),1)
            legend = re.search(r'id="issueMapLegend".*?</div>',page.text,re.S)[0]
            for item in page.context['news_items']:
                marker = ISSUE_MARKERS[item['category']]
                self.assertIn('data-marker-text="'+marker['text']+'"',page.text)
                self.assertIn(marker['color'],legend)
            self.assertEqual('혜택</span>' in legend,page is benefit)

    def test_marker_text_color_contrast(self):
        self.assertEqual([m['text'] for m in ISSUE_MARKERS.values()],['행사','전시','공연','체험','혜택'])
        self.assertEqual(len({m['color'] for m in ISSUE_MARKERS.values()}),5)
        for marker in ISSUE_MARKERS.values():
            rgb = [int(marker['color'][i:i+2],16)/255 for i in (1,3,5)]
            rgb = [c/12.92 if c<=0.04045 else ((c+.055)/1.055)**2.4 for c in rgb]
            luminance = sum(c*w for c,w in zip(rgb,(.2126,.7152,.0722)))
            self.assertGreaterEqual(1.05/(luminance+.05),4.5)
