"""Additional ISSUE images stay separate from the existing primary-poster relation."""
import json
import re
from contextlib import closing

from starlette.datastructures import FormData, UploadFile
from image_uploads import ImageError, ImagePlan
from sqlite_migrations import verified_migration

VERSION = '20261124_issue_gallery_schedule'


def migrate(database):
    with verified_migration(database, VERSION, 'jejuno-before-issue-gallery') as conn:
        if conn is None:
            return
        if 'time_mode' not in {r[1] for r in conn.execute('PRAGMA table_info(news_posts)')}:
            # NULL means a historical row: infer the display without rewriting its times.
            conn.execute("ALTER TABLE news_posts ADD COLUMN time_mode TEXT CHECK(time_mode IN ('specified','all_day','unknown'))")
        conn.execute('''CREATE TABLE IF NOT EXISTS issue_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER NOT NULL REFERENCES news_posts(id) ON DELETE CASCADE,
            file_path TEXT NOT NULL,
            width INTEGER NOT NULL CHECK(width>0), height INTEGER NOT NULL CHECK(height>0),
            byte_size INTEGER NOT NULL CHECK(byte_size>0),
            sort_order INTEGER NOT NULL CHECK(sort_order BETWEEN 1 AND 5),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(issue_id,sort_order))''')
        conn.execute('CREATE INDEX IF NOT EXISTS issue_images_path ON issue_images(file_path)')


def gallery(store, issue_id):
    with closing(store.connect_db()) as conn:
        return [{**dict(r), 'url': '/uploads/' + r['file_path']} for r in conn.execute(
            'SELECT * FROM issue_images WHERE issue_id=? ORDER BY sort_order', (issue_id,))]


class IssueImagePlan(ImagePlan):
    def __init__(self, store, poster, additional, order, original):
        super().__init__(store, 'issues')
        self.new, self.remove = poster.new, poster.remove
        self.additional, self.order, self.original = additional, order, original

    def discard(self):
        super().discard()
        self.additional.discard()

    def apply(self, conn, owner_id):
        if self.order is not None:
            rows = conn.execute('SELECT * FROM issue_images WHERE issue_id=? ORDER BY sort_order', (owner_id,)).fetchall()
            current = ['saved:' + str(r['id']) for r in rows]
            if current != self.original:
                raise ImageError('추가 이미지가 다른 화면에서 변경되었습니다. 새로고침 후 다시 시도해주세요.')
            saved = {f"saved:{r['id']}": dict(r) for r in rows}
            if any(token.startswith('saved:') and token not in saved for token in self.order):
                raise ImageError('이 EVENT에 등록된 추가 이미지만 사용할 수 있습니다.')
            # Reinsert retained IDs in one transaction to avoid UNIQUE order collisions.
            # Removed file bytes are retained: legacy/shared references may still use them.
            conn.execute('DELETE FROM issue_images WHERE issue_id=?', (owner_id,))
            for position, token in enumerate(self.order, 1):
                image = saved[token] if token in saved else self.additional.new[int(token[4:])]
                conn.execute('''INSERT INTO issue_images(id,issue_id,file_path,width,height,byte_size,sort_order,created_at)
                    VALUES (?,?,?,?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP))''',
                    (image.get('id'),owner_id,image['file_path'],image['width'],image['height'],
                     image['byte_size'],position,image.get('created_at')))
        super().apply(conn, owner_id)


async def prepare(store, form):
    uploads = [(k,v) for k,v in form.multi_items() if isinstance(v,UploadFile) and v.filename]
    if any(k != 'image_0' and not re.fullmatch(r'gallery_[0-4]', k) for k,_ in uploads):
        raise ImageError('대표 포스터 1장과 추가 이미지 최대 5장만 등록할 수 있습니다.')
    order = original = None
    if 'gallery_order' in form:
        try:
            order, original = json.loads(form['gallery_order']), json.loads(form.get('gallery_original','[]'))
            for values in (order, original):
                if not isinstance(values,list) or len(values)>5 or any(not isinstance(v,str) or not re.fullmatch(r'saved:[1-9][0-9]*|new:[0-4]',v) for v in values) or len(set(values))!=len(values):
                    raise ValueError
            if any(not v.startswith('saved:') for v in original):
                raise ValueError
            new = [f'new:{k[8:]}' for k,_ in uploads if k.startswith('gallery_')]
            if sorted(v for v in order if v.startswith('new:')) != sorted(new):
                raise ValueError
        except (TypeError,ValueError):
            raise ImageError('추가 이미지는 중복 없이 최대 5장까지 등록할 수 있습니다.') from None
    elif any(k.startswith('gallery_') for k,_ in uploads):
        raise ImageError('추가 이미지 순서를 확인해주세요.')
    poster = await store.prepare(FormData([(k,v) for k,v in form.multi_items() if not k.startswith('gallery_')]), 'issues')
    try:
        additional = await store.prepare(FormData([('image_'+k[8:],v) for k,v in uploads if k.startswith('gallery_')]), 'issues', slots=5)
    except BaseException:
        poster.discard()
        raise
    return IssueImagePlan(store, poster, additional, order, original)
