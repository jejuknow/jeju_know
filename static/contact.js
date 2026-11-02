// The unique form token also prevents duplicates if a browser retries the POST.
(() => {
  const form = document.querySelector('.contact-form');
  const submit = form?.querySelector('button[type=submit]');
  if (!submit) return;
  form.addEventListener('submit', () => { submit.disabled = true; submit.textContent = '등록 중…'; });
  window.addEventListener('pageshow', () => { submit.disabled = false; submit.textContent = '문의 등록하기'; });
})();
