(() => {
  const status = document.querySelector('#issueScheduleStatus');
  if (!status) return;
  const form = status.closest('form');
  const start = form.elements.event_start, end = form.elements.event_end;
  const memberDatesRequired = start.required;
  const today = status.dataset.today;
  function update() {
    start.required = end.required = memberDatesRequired || Boolean(form.elements.published?.checked);
    const a = start.value, b = end.value;
    const invalid = a && b && b < a;
    end.setCustomValidity(invalid ? '종료일은 시작일 이후 날짜를 선택해주세요.' : '');
    end.min = a || '';
    const days = a && Math.round((Date.parse(a) - Date.parse(today)) / 86400000);
    const label = invalid ? end.validationMessage : !a || !b ? '일정 미등록'
      : today < a ? '예정 D-' + days : today > b ? '종료' : '진행중';
    status.textContent = '현재 상태 · ' + label;
  }
  form.addEventListener('input', update);
  update();
})();
