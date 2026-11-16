(() => {
  'use strict';
  const subtype = document.querySelector('[data-ranking-subtype]');
  const title = document.querySelector('[data-ranking-title]');
  if (!subtype || !title) return;
  let suggested = '';
  const suggest = () => {
    const name = subtype.selectedOptions[0]?.dataset.name;
    if (!name) return;
    if (!title.value.trim() || title.value === suggested) {
      suggested = `제주 ${name} BEST`;
      title.value = suggested;
    }
  };
  subtype.addEventListener('change', suggest);
  suggest();
})();
