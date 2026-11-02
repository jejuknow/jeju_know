(() => {
  'use strict';
  const root = document.querySelector('.issue-discovery');
  if (!root) return;
  const mobile = matchMedia('(max-width:760px)');
  const canvas = document.getElementById('issueMap');
  const notice = document.getElementById('issueMapNotice');
  const cards = [...root.querySelectorAll('.issue-card[data-lat][data-lng]')];
  const buttons = [...root.querySelectorAll('[data-view-choice]')];
  let sdk, map, popup, loading;

  function popupFor(card, position, button) {
    popup?.setMap(null);
    const box = document.createElement('div');
    box.className = 'issue-popup';
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', 'ISSUE 정보');
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = '×';
    close.setAttribute('aria-label', '팝업 닫기');
    close.onclick = () => { popup.setMap(null); button.focus(); };
    box.append(close);
    for (const [tag, selector] of [['small','[data-category]'],['strong','h3'],['span','[data-dates]'],['small','[data-badge]']]) {
      const element = document.createElement(tag);
      element.textContent = card.querySelector(selector).textContent;
      box.append(element);
    }
    const link = document.createElement('a');
    link.href = card.querySelector('a').href;
    link.textContent = '자세히 보기 →';
    box.append(link);
    // Center the chosen marker so a popup is also reachable on a narrow map.
    map.panTo(position);
    popup = new sdk.CustomOverlay({map, position, content:box, yAnchor:1.24, zIndex:10, clickable:true});
    close.focus({preventScroll:true});
  }

  async function initMap() {
    sdk = await window.JejunoKakao.load(root.dataset.kakaoKey);
    map = new sdk.Map(canvas, {center:new sdk.LatLng(33.38,126.54), level:10});
    const bounds = new sdk.LatLngBounds();
    for (const card of cards) {
      const position = new sdk.LatLng(Number(card.dataset.lat), Number(card.dataset.lng));
      bounds.extend(position);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'issue-pin';
      const kind = card.querySelector('[data-category]').textContent;
      button.dataset.kind = kind;
      button.textContent = kind.split('·')[0];
      button.setAttribute('aria-label', card.querySelector('h3').textContent + ' ISSUE 지도 마커');
      const marker = new sdk.CustomOverlay({map, position, content:button, yAnchor:1, clickable:true});
      const highlight = on => { button.classList.toggle('is-highlighted',on); marker.setZIndex(on ? 3 : 1); };
      card.addEventListener('mouseenter', () => highlight(true));
      card.addEventListener('mouseleave', () => highlight(false));
      card.addEventListener('focusin', () => highlight(true));
      card.addEventListener('focusout', () => highlight(false));
      button.onclick = () => popupFor(card, position, button);
    }
    if (cards.length === 1) { map.setCenter(bounds.getSouthWest()); map.setLevel(5); }
    else if (cards.length) map.setBounds(bounds);
    sdk.event.addListener(map, 'click', () => popup?.setMap(null));
    notice.hidden = cards.length > 0;
    if (!cards.length) notice.textContent = '지도에 표시할 수 있는 ISSUE가 없습니다.';
    new ResizeObserver(() => {
      if (canvas.clientWidth) { const center = map.getCenter(); map.relayout(); map.setCenter(center); }
    }).observe(canvas);
  }

  function display(view, modifyUrl=false) {
    root.dataset.view = view;
    buttons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.viewChoice === view)));
    root.querySelector('input[name="view"]').value = view;
    for (const link of root.querySelectorAll('[data-filter]')) {
      const url = new URL(link.href);
      if (view === 'map') url.searchParams.set('view', view);
      else url.searchParams.delete('view');
      link.href = url.href;
    }
    if (modifyUrl) {
      const url = new URL(location.href);
      if (view === 'map') url.searchParams.set('view', view);
      else url.searchParams.delete('view');
      history.replaceState(null,'',url);
    }
    if ((!mobile.matches || view === 'map') && !loading) {
      loading = initMap().catch(() => { notice.hidden = false; notice.textContent = '지도를 불러오지 못했습니다.'; });
    }
  }
  buttons.forEach(b => b.addEventListener('click', () => display(b.dataset.viewChoice,true)));
  mobile.addEventListener('change', () => display(root.dataset.view));
  display(root.dataset.view);
})();
