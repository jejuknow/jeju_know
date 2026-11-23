/* One cover, staged in the browser until the existing BEST form is saved. */
(() => {
  'use strict';
  const form = document.querySelector('[data-best-cover-form]');
  if (!form) return;
  const input = form.elements.image_0, remove = form.elements.remove_image_0;
  const image = form.querySelector('[data-cover-image]'), empty = form.querySelector('[data-cover-empty]');
  const status = form.querySelector('[data-cover-status]'), saveStatus = form.querySelector('[data-cover-save-status]');
  const removeButton = form.querySelector('[data-cover-remove]'), reset = form.querySelector('[data-cover-reset]');
  const label = form.querySelector('[data-cover-pick-label]'), heading = form.querySelector('[data-cover-heading]');
  const filename = form.querySelector('[data-cover-filename]'), save = form.querySelector('button[type=submit]');
  const dialog = document.querySelector('[data-cover-dialog]'), original = form.elements.original_image.value;
  let previewURL = '', revision = 0, controller, processing = false, sending = false;
  function busy() {
    save.disabled = processing || sending;
    input.disabled = sending; removeButton.disabled = sending; reset.disabled = sending;
    form.setAttribute('aria-busy', String(processing || sending));
  }
  function render() {
    const url = remove.value === '1' ? '' : previewURL || original;
    image.hidden = !url; empty.hidden = !!url;
    if (url) image.src = url; else image.removeAttribute('src');
    empty.textContent = remove.value === '1' ? '저장하면 대표 이미지가 제거됩니다.' : '현재 등록된 이미지가 없습니다.';
    heading.textContent = previewURL ? '새 대표 이미지 미리보기' : '현재 대표 이미지';
    label.textContent = input.files.length ? '다시 선택' : url ? '이미지 변경' : '이미지 업로드';
    removeButton.hidden = !url; reset.hidden = !input.files.length && remove.value !== '1';
    filename.textContent = input.files[0]?.name || '';
  }
  function clearPreview() {
    revision++; controller?.abort(); processing = false;
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = ''; input.setCustomValidity('');
  }
  async function errorMessage(response) {
    if (response.status === 413) return '파일이 너무 큽니다. 사진은 한 장당 32MB까지 처리할 수 있어요.';
    if (response.redirected) return '관리자 로그인이 만료되었습니다. 다시 로그인한 뒤 시도해주세요.';
    if (response.headers.get('content-type')?.includes('application/json')) {
      const result = await response.json(); return result.detail || '이미지를 처리하지 못했습니다.';
    }
    const text = await response.text();
    return text.length < 500 && !/<(?:html|body)/i.test(text) ? text : '이미지를 처리하지 못했습니다. 잠시 후 다시 시도해주세요.';
  }
  input.addEventListener('change', async () => {
    clearPreview(); const version = revision, file = input.files[0];
    remove.value = '0'; status.textContent = ''; saveStatus.textContent = ''; render(); busy();
    if (!file) return;
    if (new URLSearchParams(location.search).get('photo_debug') === '1') console.debug('[JEJU_KNOW photo selection]', {name:file.name,type:file.type,size:file.size});
    if (file.size > 32*1024*1024) {
      status.textContent = '파일이 너무 큽니다. 사진은 한 장당 32MB까지 처리할 수 있어요.';
      input.setCustomValidity(status.textContent); return;
    }
    processing = true; busy(); status.textContent = '이미지 확인·미리보기 준비 중...';
    controller = new AbortController();
    try {
      const data = new FormData(); data.set('csrf_token', form.elements.csrf_token.value); data.set('image_0', file, file.name);
      const response = await fetch('/admin/best/image-preview', {method:'POST',body:data,credentials:'same-origin',signal:controller.signal});
      if (!response.ok || response.redirected) throw new Error(await errorMessage(response));
      const blob = await response.blob();
      if (blob.type !== 'image/webp') throw new Error('이미지 미리보기를 준비하지 못했습니다. 다시 선택해주세요.');
      if (version !== revision) return;
      previewURL = URL.createObjectURL(blob); render();
      status.textContent = '이미지 확인 완료 · 저장하면 반영됩니다.';
    } catch (error) {
      if (version !== revision) return;
      status.textContent = error.name === 'TypeError' ? '연결 문제로 이미지를 확인하지 못했습니다. 다시 선택해주세요.' : error.message;
      input.setCustomValidity(status.textContent);
    } finally { if (version === revision) { processing = false; busy(); } }
  });
  reset.addEventListener('click', () => {
    clearPreview(); input.value = ''; remove.value = '0'; render(); busy();
    status.textContent = '변경을 취소했습니다. 기존 대표이미지를 유지합니다.';
  });
  removeButton.addEventListener('click', () => dialog.showModal());
  dialog.querySelector('[data-cover-cancel]').addEventListener('click', () => dialog.close());
  dialog.querySelector('[data-cover-confirm]').addEventListener('click', () => {
    clearPreview(); input.value = ''; remove.value = '1'; render(); busy(); dialog.close();
    status.textContent = '저장하면 대표 이미지만 제거됩니다. BEST와 장소 목록은 유지됩니다.';
  });
  image.addEventListener('error', () => {
    image.hidden = true; empty.hidden = false; empty.textContent = '이미지를 불러오지 못했습니다. 저장된 경로는 유지됩니다.';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (processing || sending || !form.reportValidity()) return;
    // Capture the original file before disabling its input; server validation is authoritative.
    const data = new FormData(form);
    sending = true; busy(); saveStatus.textContent = '이미지와 BEST 내용을 저장 중...'; saveStatus.setAttribute('role','status');
    try {
      const response = await fetch(form.action || location.href, {method:'POST',body:data,credentials:'same-origin',headers:{'X-Requested-With':'fetch'}});
      if (response.ok && !response.redirected && response.headers.get('content-type')?.includes('application/json')) {
        const result = await response.json();
        if (typeof result.redirect === 'string' && /^\/admin\/best\/\d+\/edit$/.test(result.redirect)) {
          location.assign(result.redirect); return;
        }
        throw new Error('저장 결과를 확인하지 못했습니다. BEST 목록에서 확인해주세요.');
      }
      const destination = new URL(response.url);
      if (response.ok && response.redirected && destination.origin === location.origin && /^\/admin\/best\/\d+\/edit$/.test(destination.pathname)) {
        location.assign(response.url); return;
      }
      throw new Error(await errorMessage(response));
    } catch (error) {
      saveStatus.textContent = error.name === 'TypeError' ? '연결이 끊겼습니다. 다시 저장하기 전에 BEST 목록에서 저장 여부를 확인해주세요.' : error.message;
      saveStatus.setAttribute('role','alert');
    } finally {sending = false; busy();}
  });
  window.addEventListener('pagehide', () => {controller?.abort(); if (previewURL) URL.revokeObjectURL(previewURL);});
  window.addEventListener('pageshow', () => {sending = false; busy();});
})();
