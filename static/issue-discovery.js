(() => {
  'use strict';
  const root = document.querySelector('.issue-discovery');
  if (!root) return;
  const mobile = matchMedia('(max-width:760px)');
  const canvas = document.getElementById('issueMap');
  const notice = document.getElementById('issueMapNotice');
  const nearby = window.JejuNearby;
  const results = root.querySelector('#issueResults');
  let entries = readEntries();
  const markers = new Map();
  const locationState = nearby.controls(root.querySelector('.nearby-controls'), reason => {
    if (reason === 'location' && mobile.matches) display('map', true);
    renderResults(reason === 'location');
  });
  const buttons = [...root.querySelectorAll('[data-view-choice]')];
  let sdk, map, popup, loading, locator;

  function readEntries() {
    return [...results.querySelectorAll('.issue-card')].map(card => ({
      card,
      latitude: card.dataset.lat ? Number(card.dataset.lat) : null,
      longitude: card.dataset.lng ? Number(card.dataset.lng) : null
    }));
  }

  function renderResults(focusLocation = false) {
    const {active, point, radius} = locationState;
    const shown = active ? nearby.within(entries, point, radius) : entries;
    const visibleCards = new Set(shown.map(entry => entry.card));
    const mapped = shown.filter(nearby.hasCoordinates);
    const nearbyEmpty = results.querySelector('[data-near-empty]');
    const normalEmpty = results.querySelector('.issue-empty');
    if (normalEmpty) normalEmpty.hidden = active;
    nearbyEmpty.hidden = !active || shown.length > 0;
    results.querySelector('[data-results-title]').textContent = active ? '내 주변 ISSUE' : '제주 ISSUE';
    results.querySelector('h2 span').textContent = shown.length + '건';
    results.querySelector('[data-map-count]').textContent = active ? radius + 'km · 직선거리순' : '지도 위치 ' + mapped.length + '곳';
    for (const {card} of entries) {
      card.hidden = !visibleCards.has(card);
      markers.get(card)?.setMap(card.hidden ? null : map);
    }
    for (const {card, distance} of shown) {
      const label = card.querySelector('[data-distance]');
      label.hidden = !active;
      label.textContent = active ? nearby.formatDistance(distance) : '';
      results.insertBefore(card, nearbyEmpty);
    }
    if (!map) return;
    popup?.setMap(null);
    locator.mark(point);
    notice.hidden = mapped.length > 0 || (!!point && !active);
    notice.textContent = active ? '반경 안에 ISSUE가 없습니다.' : '지도에 표시할 수 있는 ISSUE가 없습니다.';
    if (point && (active || focusLocation)) locator.frame(point, active ? mapped : []);
    else if (mapped.length === 1) {
      map.setCenter(new sdk.LatLng(mapped[0].latitude, mapped[0].longitude));
      map.setLevel(5);
    } else if (mapped.length) {
      const bounds = new sdk.LatLngBounds();
      mapped.forEach(p => bounds.extend(new sdk.LatLng(p.latitude,p.longitude)));
      map.setBounds(bounds);
    }
  }

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
      const source = card.querySelector(selector);
      element.textContent = source.textContent;
      if (selector === '[data-badge]') element.className = source.className;
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
    locator = nearby.locationLayer(sdk, map);
    createMarkers();
    renderResults(!!locationState.point);
    sdk.event.addListener(map, 'click', () => popup?.setMap(null));
    new ResizeObserver(() => {
      if (canvas.clientWidth) { const center = map.getCenter(); map.relayout(); map.setCenter(center); }
    }).observe(canvas);
  }

  function createMarkers() {
    for (const {card, latitude, longitude} of entries.filter(nearby.hasCoordinates)) {
      const position = new sdk.LatLng(latitude, longitude);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'issue-pin';
      const kind = card.querySelector('[data-category]').textContent;
      button.dataset.kind = kind;
      button.textContent = kind.split('·')[0];
      button.setAttribute('aria-label', card.querySelector('h3').textContent + ' ISSUE 지도 마커');
      const marker = new sdk.CustomOverlay({map, position, content:button, yAnchor:1, clickable:true});
      markers.set(card, marker);
      const highlight = on => { button.classList.toggle('is-highlighted',on); marker.setZIndex(on ? 3 : 1); };
      card.addEventListener('mouseenter', () => highlight(true));
      card.addEventListener('mouseleave', () => highlight(false));
      card.addEventListener('focusin', () => highlight(true));
      card.addEventListener('focusout', () => highlight(false));
      button.onclick = () => popupFor(card, position, button);
    }
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
    if (view === 'map' && map && canvas.clientWidth) {
      map.relayout();
      if (locationState.point) renderResults(true);
    }
    if ((!mobile.matches || view === 'map') && !loading) {
      loading = initMap().catch(() => { notice.hidden = false; notice.textContent = '지도를 불러오지 못했습니다.'; });
    }
  }
  // Keep a permissioned location in this document while the server applies its normal ISSUE filters.
  let filterRequest;
  async function changeFilters(url, push) {
    filterRequest?.abort();
    const request = new AbortController();
    filterRequest = request;
    results.setAttribute('aria-busy','true');
    try {
      const response = await fetch(url, {signal:request.signal});
      if (!response.ok) throw Error('filter');
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const nextResults = doc.querySelector('#issueResults');
      const nextFilters = doc.querySelector('.issue-filters');
      if (!nextResults || !nextFilters) throw Error('filter');
      if (request !== filterRequest) return;
      // Leave the location controls mounted: a GPS request may still be in flight.
      const filters = root.querySelector('.issue-filters');
      const controls = filters.querySelector('.nearby-controls');
      nextFilters.querySelector('.nearby-controls').replaceWith(controls);
      filters.replaceWith(nextFilters);
      markers.forEach(marker => marker.setMap(null));
      markers.clear();
      results.replaceChildren(...nextResults.children);
      entries = readEntries();
      if (map) createMarkers();
      if (push) history.pushState(null, '', url);
      display(new URL(url).searchParams.get('view') === 'map' ? 'map' : 'list');
      renderResults();
    } catch (error) {
      if (error.name !== 'AbortError') location.assign(url);
    } finally {
      if (request === filterRequest) results.removeAttribute('aria-busy');
    }
  }
  root.addEventListener('click', event => {
    const link = event.target.closest('[data-filter]');
    if (!link || !locationState.point || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    changeFilters(link.href, true);
  });
  root.addEventListener('submit', event => {
    if (!event.target.matches('.issue-query') || !locationState.point) return;
    event.preventDefault();
    const url = new URL(event.target.action);
    url.search = new URLSearchParams(new FormData(event.target)).toString();
    changeFilters(url.href, true);
  });
  addEventListener('popstate', () => {
    if (filterRequest) changeFilters(location.href, false);
  });
  buttons.forEach(b => b.addEventListener('click', () => display(b.dataset.viewChoice,true)));
  mobile.addEventListener('change', () => display(root.dataset.view));
  display(root.dataset.view);
})();
