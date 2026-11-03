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
    const expired = memberDatesRequired && b && b < today;
    end.setCustomValidity(invalid ? '종료일은 시작일 이후 날짜를 선택해주세요.' : expired ? '이미 종료된 ISSUE는 등록할 수 없습니다.' : '');
    end.min = memberDatesRequired && (!a || a < today) ? today : a || '';
    const days = a && Math.round((Date.parse(a) - Date.parse(today)) / 86400000);
    const labels = status.dataset;
    const label = invalid ? end.validationMessage : !a || !b ? labels.undated
      : today < a ? labels.upcoming + ' D-' + days : today > b ? labels.ended : labels.ongoing;
    status.textContent = '현재 상태 · ' + label;
  }
  form.addEventListener('input', update);
  update();
})();
