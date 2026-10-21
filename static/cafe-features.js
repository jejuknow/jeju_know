/* Member choices use server-provided taxonomy; the server enforces the same limit. */
(() => {
  const category = document.querySelector('#memberPlaceCategory');
  if (!category) return;
  document.querySelectorAll('[data-member-cafe-features]').forEach(group => {
    const choices = [...group.querySelectorAll('input[type="checkbox"]')];
    const feedback = group.querySelector('[data-cafe-feature-feedback]');
    const count = () => choices.filter(input => input.checked).length;
    const sync = () => {
      group.hidden = category.value !== group.dataset.parentId;
      group.querySelectorAll('input,select,textarea').forEach(input => { input.disabled = group.hidden; });
      feedback.textContent = `${count()}/5 선택`;
    };
    group.addEventListener('change', event => {
      if (event.target.checked && count() > 5) {
        event.target.checked = false;
        feedback.textContent = '카페 특징은 최대 5개까지 선택할 수 있어요.';
      } else feedback.textContent = `${count()}/5 선택`;
    });
    category.addEventListener('change', sync);
    sync();
  });
})();
