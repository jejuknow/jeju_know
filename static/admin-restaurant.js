/* Restaurant-only disclosure state; all mutations use existing guarded routes. */
(() => {
  'use strict';
  const root = document.getElementById('restaurantTaxonomy');
  if (!root) return;
  const groups = [...root.querySelectorAll('[data-taxonomy-group]')];
  const opened = () => groups.filter(g => g.open).map(g => g.dataset.taxonomyGroup).join(',');
  let restoring = false;
  function remember() {
    if (restoring) return;
    const url = new URL(location.href);
    url.searchParams.set('open', opened());
    const target = document.getElementById(url.hash.slice(1));
    const group = target?.closest('[data-taxonomy-group]');
    if (group && !group.open) url.hash = '';
    history.replaceState(null, '', url);
  }
  function restore() {
    restoring = true;
    const url = new URL(location.href);
    if (url.searchParams.has('open')) {
      const ids = new Set(url.searchParams.get('open').split(','));
      groups.forEach(g => { g.open = ids.has(g.dataset.taxonomyGroup); });
    } else {
      groups.forEach((g, index) => { g.open = Boolean(url.searchParams.get('q')) || index === 0; });
    }
    const target = document.getElementById(url.hash.slice(1));
    if (target && root.contains(target)) {
      const group = target.closest('[data-taxonomy-group]');
      if (group) group.open = true;
      if (target.matches('details') && !target.matches('[data-taxonomy-group]')) target.open = true;
      target.scrollIntoView({block: 'start'});
    }
    restoring = false;
  }
  restore();
  groups.forEach(g => g.addEventListener('toggle', remember));
  for (const [selector, value] of [['[data-taxonomy-expand]', true], ['[data-taxonomy-collapse]', false]]) {
    root.querySelector(selector)?.addEventListener('click', () => {
      groups.forEach(g => { g.open = value; });
      // A stale row anchor must not reopen a group when a collapsed URL reloads.
      const url = new URL(location.href); url.hash = ''; url.searchParams.delete('new');
      history.replaceState(null, '', url); remember();
    });
  }
  root.querySelectorAll('[data-taxonomy-jump]').forEach(link => link.addEventListener('click', event => {
    event.preventDefault();
    const target = document.getElementById('restaurant-' + link.dataset.taxonomyJump);
    target.open = true;
    const url = new URL(location.href); url.searchParams.set('open', opened());
    url.hash = target.id; history.pushState(null, '', url);
    target.scrollIntoView({block: 'start'});
    target.querySelector('summary').focus({preventScroll: true});
  }));
  root.addEventListener('submit', event => {
    const form = event.target;
    if (form.method !== 'post') return;
    const next = form.elements.namedItem('next');
    if (!next) return;
    const url = new URL(next.value, location.origin);
    url.searchParams.set('open', opened());
    next.value = url.pathname + url.search + url.hash;
  });
  window.addEventListener('popstate', restore);
  window.addEventListener('hashchange', restore);
})();
