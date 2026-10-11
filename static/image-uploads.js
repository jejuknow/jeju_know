/* Native file inputs work without JavaScript; previews stay local until form submission. */
document.querySelectorAll('[data-image-slot]').forEach(slot => {
  const input = slot.querySelector('input[type=file]');
  const remove = slot.querySelector('input[type=checkbox]');
  const preview = slot.querySelector('.image-preview');
  const image = preview.querySelector('img');
  const placeholder = preview.querySelector('.image-placeholder');
  const status = slot.querySelector('.image-feedback');
  const label = slot.querySelector('.image-file-button span');
  let objectUrl;
  function render() {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = undefined;
    const file = input.files[0];
    const url = !remove.checked && (file ? (objectUrl = URL.createObjectURL(file)) : preview.dataset.original);
    image.hidden = !url;
    placeholder.hidden = !!url;
    if (url) image.src = url; else image.removeAttribute('src');
    label.textContent = url ? '이미지 변경' : '+ 이미지 업로드';
  }
  input.addEventListener('change', () => {
    const file = input.files[0];
    status.textContent = '';
    input.setCustomValidity('');
    if (file && (!/\.(jpe?g|png|webp)$/i.test(file.name) || !['image/jpeg','image/png','image/webp'].includes(file.type))) {
      status.textContent = 'JPG·PNG·WebP만 지원합니다. HEIC 등은 JPG로 변환해주세요.';
      input.value = '';
    } else if (file && file.size > 10 * 1024 * 1024) {
      status.textContent = '이미지는 파일당 10MB 이하로 선택해주세요.';
      input.value = '';
    } else if (file) {
      remove.checked = false;
      status.textContent = '새 이미지를 선택했어요. 저장하면 반영됩니다.';
    }
    render();
  });
  image.addEventListener('error', () => {
    if (!objectUrl) return;
    input.setCustomValidity('이미지 파일을 읽을 수 없습니다. 다른 이미지를 선택해주세요.');
    status.textContent = input.validationMessage;
    image.hidden = true;
    placeholder.hidden = false;
  });
  remove.addEventListener('change', () => {
    if (remove.checked) {
      input.value = '';
      input.setCustomValidity('');
    }
    status.textContent = remove.checked ? '저장하면 업로드 이미지가 삭제됩니다.' : '';
    render();
  });
  window.addEventListener('pagehide', () => { if (objectUrl) URL.revokeObjectURL(objectUrl); });
});
