/* Move the existing controls between their desktop and phone homes. Search,
   location permission and the Kakao instance stay owned by the map page. */
window.mobileMapDiscovery = function (workspace) {
  const $ = s => workspace.querySelector(s);
  const media = matchMedia('(max-width:767px), (max-width:940px) and (max-height:500px)');
  const dialog = $('#mapFilterSheet'), handle = $('#mapSheetToggle');
  const closeList = $('#mapSheetClose'), apply = $('#mapFilterApply'), radius = $('#mapRadiusMenu');
  const legend = $('#mapLegend'), nearby = $('.nearby-controls');
  const extra = $('.map-extra-filters'), filterButton = $('#mapOpenFilters');
  const homes = [];
  function movable(node, destination) {
    const home = document.createComment('desktop control');
    node.before(home);
    homes.push({node, home, destination});
  }
  movable($('#mapCuisines'), $('#mapCuisineSlot'));
  movable($('#mapDetails'), $('#mapDetailSlot'));
  movable($('.map-extra-options'), $('#mapFindSlot'));
  movable($('.map-flow-actions'), $('#mapFindSlot'));
  movable($('.map-filter-summary'), $('.map-filter-panel'));
  movable(nearby, $('#mapGeoDock'));
  for (const node of nearby.querySelectorAll('[data-near-toggle],label,[data-location-message]')) movable(node, $('#mapRadiusOptions'));
  nearby.querySelector('[data-locate]').setAttribute('aria-label','내 위치');
  let level = 0;
  function setLevel(n) {
    level = media.matches ? Math.max(0, Math.min(2,n)) : 0;
    workspace.dataset.listLevel = String(level);
    handle.setAttribute('aria-expanded', String(level > 0));
    handle.textContent = ['목록 펼치기 ↑','더 펼치기 ↑','목록 접기 ↓'][level];
    closeList.hidden = level !== 1;
    if (document.activeElement === closeList && closeList.hidden) handle.focus();
    if (level) { radius.open = false; legend.open = false; }
  }
  function layout() {
    if (dialog.open) dialog.close();
    for (const {node,home,destination} of homes) {
      if (media.matches) destination.append(node);
      else home.after(node);
    }
    extra.hidden = media.matches;
    legend.open = !media.matches;
    radius.open = false;
    setLevel(0);
  }
  function sections(state) {
    $('#mapCuisineSlot').hidden = $('#mapCuisines').hidden;
    $('#mapDetailSlot').hidden = $('#mapDetails').hidden;
    $('#mapDetailSlot h3').textContent = state.category === 'cafe' ? '카페 특징' : '세부 유형';
  }
  function openFilters() {
    if (!media.matches || dialog.open) return;
    legend.open = radius.open = false;
    dialog.showModal();
  }
  filterButton.onclick = $('#mapEditEmpty').onclick = openFilters;
  $('#mapFilterClose').onclick = () => dialog.close();
  dialog.addEventListener('keydown', e => {
    if(e.key!=='Tab') return;
    const focusable=Array.from(dialog.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),[tabindex="0"]')).filter(n=>n.getClientRects().length);
    const edge=e.shiftKey?focusable[0]:focusable.at(-1);
    if(document.activeElement===edge){e.preventDefault();(e.shiftKey?focusable.at(-1):focusable[0])?.focus();}
  });
  dialog.addEventListener('click', e => {
    if (e.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close();
  });
  $('#mapSheetReset').onclick = () => $('#mapReset').click();
  apply.onclick = () => { if(apply.hasAttribute('data-failed')) return; setLevel(0); dialog.close(); };
  handle.onclick = () => setLevel((level+1)%3);
  closeList.onclick = () => { setLevel(0); handle.focus(); };
  // Restrict swiping to the handles; the list and filter content scroll normally.
  function swipe(node, action) {
    let start = null;
    node.addEventListener('pointerdown', e => {
      if (!e.isPrimary || e.button !== 0 || (e.target.closest('button') && node !== e.target.closest('button'))) return;
      start = e.clientY;
      node.setPointerCapture(e.pointerId);
    });
    node.addEventListener('pointercancel', () => { start = null; });
    node.addEventListener('pointerup', e => {
      if (start === null) return;
      const delta = e.clientY - start; start = null;
      if (Math.abs(delta)<32) return;
      const stop = event => { event.preventDefault(); event.stopImmediatePropagation(); };
      node.addEventListener('click',stop,{capture:true,once:true});
      setTimeout(()=>node.removeEventListener('click',stop,true),0);
      action(Math.sign(delta));
    });
  }
  swipe(handle, direction => setLevel(level-direction));
  swipe(dialog.querySelector('header'), direction => { if(direction>0) dialog.close(); });
  document.addEventListener('pointerdown', e => {
    if (!media.matches) return;
    if (!radius.contains(e.target)) radius.open = false;
    if (!legend.contains(e.target)) legend.open = false;
  });
  workspace.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !media.matches || dialog.open) return;
    const open = [radius,legend].find(n=>n.open);
    if(open){open.open=false;open.querySelector('summary').focus();}
    else if(level){setLevel(0);handle.focus();}
  });
  media.addEventListener('change',layout);
  layout();
  return {
    media, sections,
    collapse() { if(level) setLevel(0); },
    closeMenus() { if(media.matches) legend.open = radius.open = false; },
    updateLocation(state, reason) {
      radius.querySelector('summary').textContent = '주변 '+state.radius+'km';
      radius.classList.toggle('near-active',state.active);
      if(media.matches && reason==='error') radius.open=true;
    },
    searching() { apply.disabled=true; apply.textContent='조회 중…'; $('#mapEditEmpty').hidden=true; },
    finished(total, failed=false) {
      apply.disabled=false;
      apply.toggleAttribute('data-failed',failed);
      apply.textContent=failed?'다시 시도':total+'곳 보기';
      $('#mapEditEmpty').hidden=failed || total!==0;
      if(!failed && !total) setLevel(0);
    }
  };
};
