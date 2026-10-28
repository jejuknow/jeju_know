/* Reuses the same Kakao address lookup as single-place forms; never saves places. */
(function () {
  'use strict';
  document.querySelectorAll('[data-hours-editor]').forEach(editor => {
    editor.addEventListener('change', () => {
      const unknown = editor.querySelector('input[type=radio]:checked')?.value === '미확인';
      editor.querySelectorAll('select').forEach(select => { select.disabled = unknown; });
    });
  });
  const form = document.getElementById('csvGeocode');
  if (!form) return;
  const button = document.getElementById('csvGeocodeButton');
  const status = document.getElementById('csvGeocodeStatus');
  const commit = document.querySelector('#csvCommit button[type=submit]');
  button.addEventListener('click', async () => {
    const rows = [...document.querySelectorAll('[data-geocode-line]')];
    if (!rows.length) { status.textContent = '검색할 주소가 없습니다.'; return; }
    button.disabled = true; commit.disabled = true;
    let success = 0;
    try {
      await window.JejunoKakao.load(form.dataset.kakaoKey);
      for (let index = 0; index < rows.length; index++) {
        status.textContent = `${index + 1}/${rows.length} 주소 확인 중 · 성공 ${success}건`;
        const row = rows[index];
        try {
          const result = await window.JejunoKakao.geocode(form.dataset.kakaoKey, row.dataset.address);
          for (const key of ['lat', 'lng']) {
            const input = document.createElement('input');
            input.type = 'hidden'; input.name = `${key}_${row.dataset.geocodeLine}`;
            input.value = String(result[key]); form.appendChild(input);
          }
          success++;
        } catch (_) { /* Unresolved addresses retain their visible warning. */ }
      }
      if (success) form.requestSubmit();
      else status.textContent = '정확한 위치를 찾지 못했습니다. 좌표 없이 등록하거나 CSV의 주소·좌표를 수정해주세요.';
    } catch (error) { status.textContent = error.message; }
    finally { if (!success) { button.disabled = false; commit.disabled = false; } }
  });
})();
