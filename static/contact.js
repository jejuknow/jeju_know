// The unique form token also prevents duplicates if a browser retries the POST.
(() => {
  const form = document.querySelector('.contact-form');
  const submit = form?.querySelector('button[type=submit]');
  if (!submit) return;
  const type = form.querySelector('select[name=inquiry_type]');
  const hint = form.querySelector('.contact-partnership-hint');
  const updateHint = () => {
    if (type && hint) hint.hidden = !['광고·제휴', '행사·공간 협업'].includes(type.value);
  };
  type?.addEventListener('change', updateHint);
  window.addEventListener('pageshow', updateHint);
  updateHint();
  form.addEventListener('submit', () => { submit.disabled = true; submit.textContent = '등록 중…'; });
  window.addEventListener('pageshow', () => { submit.disabled = false; submit.textContent = '문의 등록하기'; });
})();
