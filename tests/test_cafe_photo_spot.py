"""The rename preserves persisted identities, imports and historical analytics."""
import io
import json
import sqlite3
import time
import unittest
from contextlib import closing
from unittest.mock import patch
from zipfile import ZipFile

from analytics import report
from cafe_photo_spot_migrations import migrate, VERSION, OLD_NAME, NEW_NAME
from auth_security import hash_password
import test_community as helpers
import test_csv_import as csv_helpers


class PhotoSpotTest(unittest.TestCase):
    setUp = helpers.CommunityTest.setUp
    tearDown = helpers.CommunityTest.tearDown
    rows = helpers.CommunityTest.rows
    execute = helpers.CommunityTest.execute
    csrf = helpers.CommunityTest.csrf
    post = helpers.CommunityTest.post
    form = helpers.CommunityTest.form
    create = helpers.CommunityTest.create
    place = helpers.CommunityTest.place
    api = helpers.CommunityTest.api
    row = csv_helpers.CsvImportTest.row
    upload = csv_helpers.CsvImportTest.upload
    preview = csv_helpers.CsvImportTest.preview
    commit = csv_helpers.CsvImportTest.commit

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('community-test-secret')

    def old_feature(self):
        self.execute('DELETE FROM schema_migrations WHERE version=?', (VERSION,))
        self.execute("UPDATE categories SET name=?,legacy_aliases='[]' WHERE slug='cafe-premium'", (OLD_NAME,))
        return self.rows("SELECT * FROM categories WHERE slug='cafe-premium'")[0]

    def old_event(self):
        self.execute("""INSERT INTO analytics_events(event_key,event_type,visitor_id,session_id,path,
            feature,device_type,filters,created_at) VALUES ('photo-test','find_search','visitor','session',
            '/find','find','mobile',?,?)""", (json.dumps({'category':'cafe','cafe_features':['cafe-premium']}), int(time.time())))

    def test_migration_preserves_rows_backup_and_idempotence(self):
        feature = self.old_feature()
        p = self.place()
        self.execute('INSERT INTO place_category_links VALUES (?,?)', (p['id'],feature['id']))
        self.old_event()
        # A custom slug/order and inactive state must survive too.
        self.execute("UPDATE categories SET slug='custom-premium',sort_order=42,is_active=0 WHERE id=?", (feature['id'],))
        before = self.rows('SELECT * FROM categories WHERE id=?', (feature['id'],))[0]
        preserved = {table:self.rows('SELECT * FROM '+table) for table in ('places','place_category_links','analytics_events')}
        migrate(self.database)
        after = self.rows('SELECT * FROM categories WHERE id=?', (feature['id'],))[0]
        self.assertEqual(after['name'], NEW_NAME)
        self.assertIn(OLD_NAME, json.loads(after['legacy_aliases']))
        self.assertEqual({k:v for k,v in before.items() if k not in ('name','legacy_aliases')},
                         {k:v for k,v in after.items() if k not in ('name','legacy_aliases')})
        for table, rows in preserved.items():
            self.assertEqual(self.rows('SELECT * FROM '+table), rows)
        receipt = self.rows('SELECT * FROM schema_migrations WHERE version=?', (VERSION,))[0]
        with closing(sqlite3.connect(receipt['backup_path'])) as backup:
            self.assertEqual(backup.execute('SELECT name FROM categories WHERE id=?', (feature['id'],)).fetchone()[0], OLD_NAME)
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        snapshot = self.rows('SELECT * FROM categories')
        migrate(self.database)
        self.assertEqual(self.rows('SELECT * FROM categories'), snapshot)
        self.assertEqual(len(self.rows('SELECT * FROM schema_migrations WHERE version=?', (VERSION,))), 1)

    def test_backup_failure_does_not_rename(self):
        feature = self.old_feature()
        with patch('cafe_photo_spot_migrations.Path.mkdir', side_effect=OSError('backup unavailable')):
            with self.assertRaises(OSError):
                migrate(self.database)
        self.assertEqual(self.rows('SELECT name FROM categories WHERE id=?', (feature['id'],))[0]['name'], OLD_NAME)
        self.assertEqual(self.rows('SELECT * FROM schema_migrations WHERE version=?', (VERSION,)), [])

    def test_ambiguous_rows_stop_without_merging(self):
        self.old_feature()
        self.execute("INSERT INTO categories(name,slug,type,parent_id) VALUES (?,'other-photo','cafe_feature',?)", (NEW_NAME,self.category['cafe']))
        before = self.rows('SELECT * FROM categories')
        with self.assertRaisesRegex(RuntimeError, 'Ambiguous'):
            migrate(self.database)
        self.assertEqual(self.rows('SELECT * FROM categories'), before)

    def test_existing_place_forms_find_map_detail_and_analytics(self):
        feature = self.old_feature()
        p = self.place(latitude='33.4', longitude='126.5')
        self.execute('INSERT INTO place_category_links VALUES (?,?)', (p['id'],feature['id']))
        self.old_event()
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(report(conn,{})['rankings']['cafe_features'], [{'label':OLD_NAME,'count':1}])
        links = self.rows('SELECT * FROM place_category_links')
        events = self.rows('SELECT * FROM analytics_events')
        migrate(self.database)
        for client, path in ((self.admin,'/admin/places/new'), (self.admin,f"/admin/places/{p['id']}/edit"),
                             (self.admin,'/admin/classifications/cafe_feature?per_page=50'),
                             (self.member,'/map/register?category=cafe'), (self.visitor,'/place/'+p['slug'])):
            page = client.get(path)
            self.assertEqual(page.status_code,200,path)
            self.assertIn(NEW_NAME,page.text,path)
            self.assertNotIn('>'+OLD_NAME+'<',page.text,path)
        edit = self.admin.get(f"/admin/places/{p['id']}/edit").text
        self.assertRegex(edit, rf'name="term_ids" value="{feature["id"]}" checked')
        terms = self.visitor.get('/api/place-taxonomy').json()['terms']
        official = [t for t in terms if t['type']=='cafe_feature']
        self.assertEqual(len(official),15)
        self.assertEqual(official[11]['name'],NEW_NAME)
        self.assertEqual((official[11]['id'],official[11]['slug']),(feature['id'],feature['slug']))
        for path in ('/find?category=cafe&detail=cafe-premium','/map?category=cafe&detail=cafe-premium'):
            self.assertEqual(self.visitor.get(path).status_code,200)
        filtered = self.api('?category=cafe&detail=cafe-premium')
        self.assertEqual([r['id'] for r in filtered],[p['id']])
        self.assertIn(NEW_NAME,filtered[0]['features_public'])
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(report(conn,{})['rankings']['cafe_features'], [{'label':NEW_NAME,'count':1}])
        self.assertEqual(self.rows('SELECT * FROM place_category_links'),links)
        self.assertEqual(self.rows('SELECT * FROM analytics_events'),events)

    def test_csv_new_legacy_and_duplicate_alias_same_id(self):
        feature = self.rows("SELECT * FROM categories WHERE slug='cafe-premium'")[0]
        for index, value in enumerate((NEW_NAME,OLD_NAME,NEW_NAME+'|'+OLD_NAME)):
            path, preview = self.preview([self.row(장소명=f'포토 테스트 {index}',주소=f'제주 검증로 {index}',카페_특징=value)])
            self.assertNotIn('활성 분류에 없습니다',preview.text)
            self.assertIn('등록 완료',self.commit(path,action_2='create').text)
        links = self.rows('SELECT * FROM place_category_links')
        self.assertEqual(len(links),3)
        self.assertEqual({r['category_id'] for r in links},{feature['id']})
        self.assertEqual(len(self.rows("SELECT * FROM categories WHERE type='cafe_feature' AND name IN (?,?)",(OLD_NAME,NEW_NAME))),1)
        self.execute('UPDATE categories SET is_active=0 WHERE id=?',(feature['id'],))
        for name in (NEW_NAME,OLD_NAME):
            _,preview = self.preview([self.row(카페_특징=name)])
            self.assertIn('활성 분류에 없습니다',preview.text)

    def test_excel_uses_current_fifteen_labels(self):
        response = self.admin.get('/admin/imports/template.xlsx')
        self.assertEqual(response.status_code,200)
        with ZipFile(io.BytesIO(response.content)) as archive:
            choices = archive.read('xl/worksheets/sheet4.xml').decode()
            self.assertIn(NEW_NAME,choices)
            self.assertNotIn(OLD_NAME,choices)
            for row in self.rows("SELECT name FROM categories WHERE type='cafe_feature' AND is_active=1 AND is_legacy=0"):
                self.assertIn(row['name'],choices)
