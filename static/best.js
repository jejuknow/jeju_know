/* Hide existing server-rendered cards; never sort, fetch or mutate membership. */
(() => {
  'use strict';
  const root = document.querySelector('[data-best-places]');
  if (!root) return;
  const filters = root.querySelector('.best-filters');
  const buttons = [...filters.querySelectorAll('[data-best-filter]')];
  const cards = [...root.querySelectorAll('[data-best-category]')];
  const status = root.querySelector('.best-filter-status');
  const empty = root.querySelector('[data-best-empty]');
  filters.hidden = false;
  buttons.forEach(button => button.addEventListener('click', () => {
    const category = button.dataset.bestFilter;
    let count = 0;
    cards.forEach(card => {
      card.hidden = category !== 'all' && card.dataset.bestCategory !== category;
      if (!card.hidden) count += 1;
    });
    buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    empty.hidden = count > 0;
    empty.textContent = category === 'all' ? '이 모음에 공개된 장소를 준비 중입니다.' : '이 모음에는 해당 카테고리의 장소가 없습니다.';
    status.textContent = `${button.textContent.trim()} · ${count}곳을 표시합니다.`;
  }));
})();
