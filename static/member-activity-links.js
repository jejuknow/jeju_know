// Older bookmarks used fragment links on the single-page account.
(() => {
  const routes = new Map([
    ['#my-posts','/account/issues'], ['#my-inquiries','/account/inquiries'],
    ...['profile','password','withdraw'].map(id => ['#' + id, '/account/settings#' + id])
  ]);
  function openOldSection() {
    const target = routes.get(location.hash);
    if (!target) return;
    const page = new URLSearchParams(location.search).get('inquiry_page');
    location.replace(target + (location.hash === '#my-inquiries' && /^\d+$/.test(page || '') ? '?page=' + page : ''));
  }
  window.addEventListener('hashchange', openOldSection);
  openOldSection();
})();
