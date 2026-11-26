"""Disposable loopback-only fixture for admin_place_save_browser.cjs.

Run with the project Python. No production database or user session is used.
Windows environments without a working HEIF DLL can opt into QA_SKIP_HEIF=1;
the PNG browser fixture still exercises the real image store.
"""
import os
import sys
import tempfile
from pathlib import Path


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    folder = tempfile.TemporaryDirectory(prefix='admin-save-browser-')
    os.environ.update(JEJUNO_DATA_DIR=folder.name, JEJUNO_DATABASE=str(Path(folder.name)/'app.db'), JEJUNO_SKIP_SEED='1')
    if os.environ.get('QA_SKIP_HEIF') == '1':
        import pillow_heif
        pillow_heif.register_heif_opener = lambda **kwargs: None
        import image_uploads
        image_uploads.MIME_TYPES.pop('HEIF', None)
        image_uploads.INSPECT_FORMATS.remove('HEIF')
    from test_community import CommunityTest
    from fastapi.responses import RedirectResponse
    import uvicorn

    CommunityTest.setUpClass()
    fixture = CommunityTest()
    fixture.setUp()
    for category in ('cafe', 'food'):
        result = fixture.post(fixture.admin, '/admin/places/new', **fixture.form(
            name='저장 점검 '+category, category_id=fixture.category[category],
            address='제주시 점검로 '+category, info_source='원래 정보 출처', published='1'))
        assert result.status_code == 303, result.text[:200]
    fixture.execute('CREATE TABLE qa_updates (place_id INTEGER)')
    fixture.execute('CREATE TRIGGER qa_update AFTER UPDATE OF name ON places BEGIN INSERT INTO qa_updates VALUES (NEW.id); END')

    @fixture.app.get('/qa/admin')
    def login():
        response = RedirectResponse('/admin/places/1/edit')
        for cookie in fixture.admin.cookies.jar:
            response.set_cookie(cookie.name, cookie.value, httponly=True, samesite='lax')
        return response

    @fixture.app.get('/qa/state')
    def state():
        return {key: fixture.rows('SELECT * FROM '+table) for key, table in (
            ('places','places'), ('links','place_category_links'), ('updates','qa_updates'),
            ('logs','admin_activity_logs'), ('images','images'))}

    try:
        uvicorn.run(fixture.app, host='127.0.0.1', port=int(os.environ.get('QA_PORT','8957')))
    finally:
        fixture.tearDown()
        folder.cleanup()
