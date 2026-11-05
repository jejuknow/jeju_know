"""Correction decisions preserve places, survive races and retain history."""
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier

from fastapi.testclient import TestClient
import test_community as fixtures
from correction_migrations import migrate, VERSION
from correction_review import original_value
from submission_suggestions import dumps, snapshot


class CorrectionResolutionTest(unittest.TestCase):
    setUp = fixtures.CommunityTest.setUp
    tearDown = fixtures.CommunityTest.tearDown
    setUpClass = classmethod(fixtures.CommunityTest.setUpClass.__func__)
    rows = fixtures.CommunityTest.rows
    execute = fixtures.CommunityTest.execute
    csrf = fixtures.CommunityTest.csrf
    post = fixtures.CommunityTest.post
    form = fixtures.CommunityTest.form
    create = fixtures.CommunityTest.create
    place = fixtures.CommunityTest.place

    def suggestion(self, p, field='parking_status', value='불가'):
        self.assertEqual(self.post(self.member,f'/places/{p["id"]}/suggest',field=field,new_value=value,reason='현장 안내를 확인했습니다.').status_code,303)
        return self.rows('SELECT * FROM place_correction_suggestions ORDER BY id DESC')[0]

    def review(self, s, action, client=None, **data):
        client = client or self.admin
        return client.post(f'/admin/suggestions/{s["id"]}/review',data={'csrf_token':self.csrf(client,'/admin'),'action':action,**data},follow_redirects=False)

    def counts(self, expected):
        page=self.admin.get('/admin')
        self.assertEqual(page.context['admin_counts']['suggestions'],expected)
        self.assertEqual(len(page.context['suggestions']),expected)
        self.assertEqual(self.admin.get('/admin/suggestions').context['pagination']['total'],expected)
        self.assertEqual(self.admin.get('/admin/places').context['admin_counts']['suggestions'],expected)

    def test_accept_resolve_reject_counts_history_and_member_labels(self):
        p=self.place(); a=self.suggestion(p); b=self.suggestion(p,'pet_status','불가'); c=self.suggestion(p,'child_status','가능')
        self.counts(3)
        self.assertEqual(self.review(a,'accept').status_code,303); self.counts(2)
        self.assertEqual(self.rows('SELECT parking_status FROM places')[0]['parking_status'],'불가')
        before=self.rows('SELECT * FROM places')
        self.assertEqual(self.review(b,'resolve',review_note='직접 확인 후 현재 정보 유지').status_code,303); self.counts(1)
        self.assertEqual(self.rows('SELECT * FROM places'),before)
        self.assertEqual(self.review(c,'reject',review_note='현장 확인 결과와 다름').status_code,303); self.counts(0)
        self.assertEqual(self.rows('SELECT * FROM places'),before)
        self.assertEqual(len(self.admin.get('/admin/suggestions?status=all').context['suggestions']),3)
        self.assertEqual(self.admin.get('/admin/suggestions?status=resolved').context['suggestions'][0]['id'],b['id'])
        record=self.rows('SELECT * FROM place_correction_suggestions WHERE id=?',(b['id'],))[0]
        self.assertEqual(record['status'],'resolved'); self.assertEqual(record['reviewed_by'],'admin:1')
        self.assertTrue(record['reviewed_at']); self.assertEqual(record['review_note'],'직접 확인 후 현재 정보 유지')
        member=self.member.get('/account/suggestions').text
        for text in ('반영','처리완료','반려','제안 내용을 확인하여 처리가 완료되었습니다.'): self.assertIn(text,member)
        for action in ('accept','resolve','reject'): self.assertEqual(self.review(b,action).status_code,409)
        logs=self.rows("SELECT * FROM admin_activity_logs WHERE target_type='수정 제안'")
        self.assertEqual([r['action'] for r in logs],['정보 바로잡기 반영','정보 바로잡기 처리완료','정보 바로잡기 반려'])
        self.assertIn('pending → resolved',logs[1]['target_name']); self.assertEqual(logs[1]['admin_id'],1)

    def test_stale_keeps_layout_latest_values_and_can_resolve_without_place_write(self):
        p=self.place(); s=self.suggestion(p,'name','제안 이름')
        self.execute("UPDATE places SET name='현재 이름' WHERE id=?",(p['id'],))
        page=self.admin.get(f'/admin/suggestions/{s["id"]}')
        for text in (p['name'],'현재 이름','제안 이름','admin-shell','직접 확인 후 처리완료','장소 수정 →'): self.assertIn(text,page.text)
        self.assertTrue(page.context['s']['stale']); self.assertIn('disabled>제안 반영',page.text)
        error=self.review(s,'accept',review_note='<script>safe</script>')
        self.assertEqual(error.status_code,409); self.assertIn('admin-shell',error.text)
        self.assertIn('&lt;script&gt;',error.text)
        before=self.rows('SELECT * FROM places')
        self.execute("CREATE TRIGGER no_place_update BEFORE UPDATE ON places BEGIN SELECT RAISE(ABORT,'unexpected place write'); END")
        self.assertEqual(self.review(s,'resolve').status_code,303)
        self.assertEqual(self.rows('SELECT * FROM places'),before)

    def test_verification_and_manual_edit_do_not_resolve_and_return_link_is_safe(self):
        p=self.place(); s=self.suggestion(p)
        self.assertEqual(self.post(self.admin,f'/admin/places/{p["id"]}/confirm-information').status_code,303)
        self.counts(1)
        destination=f'/admin/suggestions/{s["id"]}'
        page=self.admin.get(f'/admin/places/{p["id"]}/edit',params={'next':destination})
        self.assertIn(f'name="next" value="{destination}"',page.text)
        bad=self.admin.get(f'/admin/places/{p["id"]}/edit?next=https://evil.example')
        self.assertIn('name="next" value="/admin/places"',bad.text)
        response=self.post(self.admin,f'/admin/places/{p["id"]}/edit',**self.form(published='1',parking_status='가능',next=destination))
        self.assertEqual(response.headers['location'],destination); self.counts(1)
        before=self.rows('SELECT * FROM places')
        self.assertEqual(self.review(s,'resolve').status_code,303)
        self.assertEqual(self.rows('SELECT * FROM places'),before)

    def test_removed_and_admin_only_legacy_proposals_never_apply(self):
        p=self.place(); uid=self.rows("SELECT id FROM users WHERE username='member'")[0]['id']
        for field in ('reservation_status','waiting_status','companions','verification_status','is_pick'):
            sid=self.execute('INSERT INTO place_correction_suggestions(place_id,user_id,field,new_value,original_value,reason) VALUES(?,?,?,?,?,?)',(p['id'],uid,field,'가능',original_value(p,field),'과거 제안'))
            s={'id':sid}; before=self.rows('SELECT * FROM places')
            self.assertEqual(self.review(s,'accept').status_code,409)
            self.assertEqual(self.rows('SELECT * FROM places'),before)
            self.assertEqual(self.review(s,'resolve').status_code,303)
        for payload in ({'companions':['연인']},{'usage':{'reservation_status':'가능'}},{'usage':{'waiting_status':'없음'}},{'usage':{'parking_status':'제한'}}):
            proposal={'category_id':p['category_id'],'companions':[],'term_ids':[],'usage':{},**payload}
            with closing(sqlite3.connect(self.database)) as conn:
                conn.row_factory=sqlite3.Row
                old=snapshot(conn,p,proposal)
            sid=self.execute('INSERT INTO place_correction_suggestions(place_id,user_id,field,new_value,original_value,reason) VALUES(?,?,?,?,?,?)',(p['id'],uid,'submission_details',dumps(proposal),old,'과거 묶음 제안'))
            before=self.rows('SELECT * FROM places')
            self.assertEqual(self.review({'id':sid},'accept').status_code,409)
            self.assertEqual(self.rows('SELECT * FROM places'),before)
            self.assertEqual(self.review({'id':sid},'reject').status_code,303)

    def test_permissions_csrf_invalid_actions_and_deleted_place_resolution(self):
        p=self.place(); s=self.suggestion(p); path=f'/admin/suggestions/{s["id"]}/review'
        for client in (self.member,self.visitor):
            self.assertEqual(client.post(path,data={'action':'resolve'},follow_redirects=False).status_code,303)
        self.assertEqual(self.admin.post(path,data={'action':'resolve'}).status_code,400)
        self.assertEqual(self.review(s,'invalid').status_code,400)
        self.assertEqual(self.review(s,'resolve',review_note='a'*2001).status_code,400)
        self.execute('UPDATE places SET deleted_at=CURRENT_TIMESTAMP WHERE id=?',(p['id'],))
        self.assertEqual(self.review(s,'accept').status_code,409)
        self.assertEqual(self.review(s,'resolve').status_code,303)

    def test_admin_only_feature_and_malformed_batch_are_blocked_with_layout(self):
        p=self.place(); uid=self.rows("SELECT id FROM users WHERE username='member'")[0]['id']
        term=self.rows("SELECT id FROM categories WHERE type='cafe_feature' AND member_policy='admin'")[0]['id']
        proposal={'category_id':p['category_id'],'companions':[],'term_ids':[term],'usage':{'parking_status':'가능'}}
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory=sqlite3.Row
            old=snapshot(conn,p,proposal)
        for value in (dumps(proposal),'[]','{"usage":null}'):
            sid=self.execute('INSERT INTO place_correction_suggestions(place_id,user_id,field,new_value,original_value,reason) VALUES(?,?,?,?,?,?)',(p['id'],uid,'submission_details',value,old,'검증'))
            before=self.rows('SELECT * FROM places'); links=self.rows('SELECT * FROM place_category_links')
            response=self.review({'id':sid},'accept')
            self.assertEqual(response.status_code,409); self.assertIn('admin-shell',response.text)
            self.assertEqual(self.rows('SELECT * FROM places'),before)
            self.assertEqual(self.rows('SELECT * FROM place_category_links'),links)
            self.assertEqual(self.review({'id':sid},'resolve').status_code,303)

    def test_two_admins_cannot_process_same_suggestion_twice(self):
        p=self.place(); s=self.suggestion(p)
        self.execute("INSERT INTO admins(username,password_hash,role) VALUES (?,?,'sub_admin')",('sub',self.password_hash))
        sub=TestClient(self.app); self.clients.append(sub)
        sub.post('/admin/login',data={'csrf_token':self.csrf(sub,'/admin/login'),'username':'sub','password':'community-test-secret'})
        tokens=[self.csrf(self.admin,'/admin'),self.csrf(sub,'/admin')]
        barrier=Barrier(2)
        def run(client,token,action):
            barrier.wait()
            return client.post(f'/admin/suggestions/{s["id"]}/review',data={'csrf_token':token,'action':action},follow_redirects=False).status_code
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(run,self.admin,tokens[0],'accept'); b=pool.submit(run,sub,tokens[1],'reject')
            self.assertEqual(sorted([a.result(),b.result()]),[303,409])
        self.assertEqual(len(self.rows("SELECT * FROM admin_activity_logs WHERE target_type='수정 제안'")),1)
        final=self.rows('SELECT status FROM place_correction_suggestions')[0]['status']
        self.assertEqual(self.rows('SELECT parking_status FROM places')[0]['parking_status'],'불가' if final=='accepted' else '미확인')


class CorrectionMigrationTest(unittest.TestCase):
    def test_backup_preserves_rows_indexes_sequence_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'jejuno.db'
            with closing(sqlite3.connect(db)) as c,c:
                c.executescript("""CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,backup_path TEXT);
                CREATE TABLE places(id INTEGER PRIMARY KEY); CREATE TABLE users(id INTEGER PRIMARY KEY);
                INSERT INTO places VALUES(1); INSERT INTO users VALUES(1);
                CREATE TABLE place_correction_suggestions(id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id INTEGER REFERENCES places(id),user_id INTEGER REFERENCES users(id),field TEXT,new_value TEXT,original_value TEXT,reason TEXT,
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected')),
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,reviewed_at TEXT,reviewed_by TEXT,review_note TEXT DEFAULT '');
                CREATE INDEX place_suggestions_status_idx ON place_correction_suggestions(status,place_id);""")
                for status in ('pending','accepted','rejected'):
                    c.execute("INSERT INTO place_correction_suggestions(place_id,user_id,field,new_value,original_value,reason,status) VALUES(1,1,'name','새 이름','예전 이름','검증',?)",(status,))
                c.execute("UPDATE sqlite_sequence SET seq=100 WHERE name='place_correction_suggestions'")
                before=c.execute('SELECT * FROM place_correction_suggestions').fetchall()
            migrate(db); migrate(db)
            with closing(sqlite3.connect(db)) as c,c:
                self.assertEqual(c.execute('SELECT * FROM place_correction_suggestions').fetchall(),before)
                backup=c.execute('SELECT backup_path FROM schema_migrations WHERE version=?',(VERSION,)).fetchone()[0]
                self.assertTrue(Path(backup).exists())
                self.assertEqual(c.execute("INSERT INTO place_correction_suggestions(status) VALUES('resolved')").lastrowid,101)
                self.assertTrue(c.execute("SELECT 1 FROM sqlite_master WHERE name='place_suggestions_status_idx'").fetchone())
                self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            with closing(sqlite3.connect(backup)) as c:
                self.assertEqual(c.execute('SELECT * FROM place_correction_suggestions').fetchall(),before)
                self.assertNotIn("'resolved'",c.execute("SELECT sql FROM sqlite_master WHERE name='place_correction_suggestions'").fetchone()[0])
