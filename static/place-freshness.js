// Keep unsaved place edits intact when the operator confirms the facts.
const confirmation = document.querySelector('#confirmInformation');
confirmation?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = confirmation.querySelector('button');
  if (button.disabled) return;
  button.disabled = true;
  const status = confirmation.querySelector('[role="status"]');
  status.textContent = '저장 중…';
  try {
    const response = await fetch(confirmation.action, {
      method: 'POST', body: new FormData(confirmation), headers: {Accept: 'application/json'},
    });
    if (!response.ok || response.redirected) throw new Error();
    const result = await response.json();
    document.querySelector('[data-checked-date]').textContent = result.checked_at;
    status.textContent = result.message;
  } catch {
    status.textContent = '저장 결과를 확인하지 못했습니다. 로그인 상태를 확인하고 다시 시도해주세요.';
  } finally {
    button.disabled = false;
  }
});
