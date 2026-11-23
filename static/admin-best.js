(() => {
  'use strict';
  const root = document.querySelector('[data-best-editor]');
  if (!root) return;
  const find = key => root.querySelector(`[data-${key}]`);
  const options = JSON.parse(document.getElementById('bestEditorFilters').textContent);
  let state = JSON.parse(document.getElementById('bestEditorState').textContent);
  const csrf = document.querySelector('input[name="csrf_token"]').value;
  const form = find('search-form'), results = find('results'), list = find('included');
  const selected = new Map();
  let category = '', includedCategory = '', page = 1, pages = 1;
  let busy = false, stale = false, loading = false, timer, controller, sequence = 0, drag;
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  function notice(message, error = false) {
    find('notice').textContent = message;
    find('notice').classList.toggle('is-error', error);
  }
  function controls() {
    const locked = busy || stale;
    form.querySelectorAll('input,select,button').forEach(input => { input.disabled = busy; });
    root.querySelectorAll('[data-row-action]').forEach(button => {
      const row = button.closest('[data-place-id]');
      const index = state.items.findIndex(p => p.id === Number(row.dataset.placeId));
      const action = button.dataset.rowAction;
      button.disabled = locked || (action !== 'remove' && (!!includedCategory || state.items.length < 2)) ||
        (action === 'up' && index === 0) || (action === 'down' && index === state.items.length - 1);
    });
    root.querySelectorAll('[data-category],[data-included-category]').forEach(button => { button.disabled = busy; });
    results.querySelectorAll('input').forEach(input => {
      input.disabled = locked || input.dataset.alreadyIncluded === 'true';
    });
    find('add').disabled = locked || !selected.size;
    find('clear').disabled = busy || !selected.size;
    find('prev').disabled = loading || busy || page <= 1;
    find('next').disabled = loading || busy || page >= pages;
    find('refresh').hidden = !stale;
    list.setAttribute('aria-busy', String(busy));
  }
  function selection() {
    find('add').textContent = `선택한 ${selected.size}곳 추가`;
    find('selection').textContent = selected.size ?
      `추가 순서: ${Array.from(selected.values()).join(' → ')}` : '선택한 장소가 없습니다.';
    controls();
  }
  function photo(place) {
    const frame = el('span', undefined, 'best-thumb');
    if (place.thumbnail && (/^https?:\/\//i.test(place.thumbnail) || /^\/uploads\//.test(place.thumbnail))) {
      const img = el('img');
      img.src = place.thumbnail; img.alt = ''; img.loading = 'lazy'; img.width = 56; img.height = 56;
      img.addEventListener('error', () => { frame.replaceChildren(el('span', '사진 없음')); }, {once: true});
      frame.append(img);
    } else frame.append(el('span', '사진 없음'));
    return frame;
  }
  function info(place, linked = false) {
    const body = el('div', undefined, 'best-place-info');
    const title = el(linked ? 'a' : 'strong', place.name);
    if (linked) title.href = `/admin/places/${place.id}/edit`;
    const meta = el('div', undefined, 'best-place-meta');
    meta.append(el('span', place.category, 'best-category'), el('span', place.region),
      el('span', place.unavailable ? '삭제·병합됨' : (place.is_public ? '공개' : '비공개'), 'best-visibility'));
    body.append(title, meta);
    if (place.summary) {
      const summary = el('p', place.summary, 'best-place-summary');
      summary.title = place.summary; body.append(summary);
    }
    if (!place.is_public) body.append(el('small', '방문자 BEST에는 노출되지 않습니다.', 'best-help'));
    return body;
  }
  function renderResults(items) {
    results.replaceChildren();
    for (const place of items) {
      const row = el('label', undefined, 'best-result');
      const input = el('input'); input.type = 'checkbox'; input.value = place.id;
      input.setAttribute('aria-label', `${place.name} 선택`);
      input.dataset.alreadyIncluded = String(place.included);
      input.checked = place.included || selected.has(place.id);
      input.addEventListener('change', () => {
        if (input.checked && selected.size >= 100) {
          input.checked = false; notice('한 번에 최대 100곳까지 선택할 수 있습니다.', true); return;
        }
        if (input.checked) selected.set(place.id, place.name); else selected.delete(place.id);
        selection();
      });
      row.append(input, photo(place), info(place));
      if (place.included) row.lastChild.append(el('small', '현재 모음에 포함됨', 'best-already'));
      results.append(row);
    }
    if (!items.length) results.append(el('p', '조건에 맞는 장소가 없습니다. 검색어나 필터를 바꿔보세요.', 'best-help'));
    controls();
  }
  function renderIncluded(focusId, focusAction) {
    const tabs = find('included-categories'); tabs.replaceChildren();
    const categories = new Map(options.categories.map(c => [String(c.id), c.name]));
    state.items.forEach(p => categories.set(String(p.category_id ?? ''), p.category));
    const entries = [['', '전체', state.items.length], ...Array.from(categories, ([id, name]) =>
      [id, name, state.items.filter(p => String(p.category_id ?? '') === id).length])];
    for (const [id, name, count] of entries) {
      const button = el('button', `${name} ${count}`, 'mini-btn'); button.type = 'button';
      button.dataset.includedCategory = id; button.setAttribute('aria-pressed', String(id === includedCategory));
      tabs.append(button);
    }
    list.replaceChildren();
    for (const [index, place] of state.items.entries()) {
      if (includedCategory && String(place.category_id) !== includedCategory) continue;
      const row = el('li', undefined, 'best-included-row'); row.dataset.placeId = place.id;
      const order = el('div', undefined, 'best-order');
      const handle = el('button', '☰', 'mini-btn best-drag'); handle.type = 'button';
      handle.dataset.rowAction = 'drag'; handle.setAttribute('aria-label', `${place.name} 순서 이동`);
      handle.title = '끌어서 순서 변경';
      order.append(handle, el('span', String(index + 1), 'best-position'));
      const actions = el('div', undefined, 'row-actions best-row-actions');
      for (const [action, text, label] of [['up', '↑', '위로'], ['down', '↓', '아래로'], ['remove', '모음에서 제외', '모음에서 제외']]) {
        const button = el('button', text, 'mini-btn'); button.type = 'button'; button.dataset.rowAction = action;
        button.setAttribute('aria-label', `${place.name} ${label}`); actions.append(button);
      }
      row.append(order, photo(place), info(place, true), actions); list.append(row);
    }
    find('included-count').textContent = `${state.items.length}곳`;
    find('empty').hidden = !!list.children.length;
    find('empty').textContent = includedCategory ? '이 카테고리에 포함된 장소가 없습니다.' : '포함된 장소가 없습니다. 위에서 장소를 찾아 추가해주세요.';
    find('order-help').textContent = includedCategory ? '순서 변경은 전체 보기에서 가능합니다. 카테고리 필터는 방문자 BEST의 순서를 바꾸지 않습니다.' :
      '전체 보기에서 손잡이를 드래그하거나 ↑↓ 버튼으로 순서를 바꿀 수 있습니다. 변경 즉시 저장됩니다.';
    controls();
    if (focusId) list.querySelector(`[data-place-id="${focusId}"] [data-row-action="${focusAction}"]`)?.focus({preventScroll: true});
  }
  function terms() {
    const parent = options.categories.find(c => String(c.id) === category);
    const choices = options.terms.filter(t => String(t.category_id) === category);
    const food = parent?.slug === 'food';
    find('major-wrap').hidden = !food;
    const fill = (select, values, label) => {
      select.replaceChildren(new Option(label, ''));
      values.forEach(t => select.add(new Option(t.name, t.id)));
    };
    if (!food) form.elements.major.value = '';
    if (food) {
      const value = form.elements.major.value;
      fill(form.elements.major, choices.filter(t => t.parent_id === parent.id), '전체 음식');
      form.elements.major.value = value;
    }
    const visible = food ? choices.filter(t => String(t.parent_id) === form.elements.major.value) : choices;
    fill(form.elements.term, visible, '전체');
    find('term-label').textContent = food ? '맛집 세부유형' : (parent?.slug === 'cafe' ? '카페 특징' : '세부유형');
    find('term-wrap').hidden = !visible.length;
  }
  async function search() {
    if (busy) return;
    clearTimeout(timer); controller?.abort();
    controller = new AbortController(); const currentController = controller, ticket = ++sequence;
    const timeout = setTimeout(() => currentController.abort(), 20000);
    const params = new URLSearchParams(new FormData(form)); params.set('category', category); params.set('page', page);
    loading = true; results.setAttribute('aria-busy', 'true');
    find('search-status').textContent = '장소를 찾고 있습니다…'; controls();
    try {
      const response = await fetch(`${root.dataset.url}/search?${params}`, {signal: controller.signal, headers: {'Accept': 'application/json'}});
      if (response.redirected || !response.headers.get('content-type')?.includes('application/json')) throw new Error('로그인이 만료되었습니다. 화면을 새로고침해주세요.');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '검색하지 못했습니다. 다시 시도해주세요.');
      if (ticket !== sequence) return;
      if (data.version !== state.version) {
        stale = true; notice('다른 작업에서 모음이 변경되었습니다. 최신 목록을 다시 불러와주세요.', true);
      }
      page = data.page; pages = data.pages;
      renderResults(data.items);
      find('search-status').textContent = `검색 결과 ${data.total}곳 · 한 페이지에 ${data.page_size}곳` +
        (data.items.length && data.items.every(p => p.included) ? ' · 이 페이지의 장소는 모두 현재 모음에 포함되어 있습니다.' : '');
      find('page').textContent = `${page} / ${pages}`;
    } catch (error) {
      if (ticket !== sequence) return;
      results.replaceChildren(); find('page').textContent = '';
      find('search-status').textContent = error.name === 'AbortError' ? '검색 시간이 초과되었습니다. 검색 버튼으로 다시 시도해주세요.' : error.message;
    } finally {
      clearTimeout(timeout);
      if (ticket === sequence) { loading = false; results.setAttribute('aria-busy', 'false'); controls(); }
    }
  }
  async function save(action, ids, focusId, focusAction) {
    if (busy || stale) return;
    const before = state;
    busy = true; clearTimeout(timer); controller?.abort(); ++sequence; loading = false;
    if (action === 'reorder') {
      const byId = new Map(state.items.map(p => [p.id, p]));
      state = {...state, items: ids.map(id => byId.get(id))}; renderIncluded();
    }
    controls(); notice('저장 중…');
    const abort = new AbortController(), timeout = setTimeout(() => abort.abort(), 20000);
    try {
      const response = await fetch(`${root.dataset.url}/manage`, {method: 'POST', signal: abort.signal,
        headers: {'Accept': 'application/json'}, body: new URLSearchParams({csrf_token: csrf, action, ids: JSON.stringify(ids), version: before.version})});
      if (response.redirected || !response.headers.get('content-type')?.includes('application/json')) throw new Error('로그인을 확인한 뒤 최신 목록을 다시 불러와주세요.');
      const data = await response.json();
      if (!response.ok) {
        stale = true;
        throw new Error(data.error || '저장하지 못했습니다. 최신 목록을 확인해주세요.');
      }
      state = data;
      if (action === 'add') selected.clear();
      notice(action === 'reorder' ? '순서를 변경했습니다.' : action === 'add' ? `${ids.length}곳을 추가했습니다.` : '모음에서 제외했습니다. 장소 원본은 유지됩니다.');
    } catch (error) {
      state = before; stale = true;
      notice(error.name === 'AbortError' ? '응답이 지연되어 기존 화면으로 돌렸습니다. 저장 여부는 최신 목록에서 확인해주세요.' : error.message, true);
    } finally {
      clearTimeout(timeout); busy = false; renderIncluded(focusId, focusAction); selection();
      if (!stale) search();
    }
  }
  function move(id, target, action = 'drag') {
    if (busy || stale || includedCategory) return;
    const ids = state.items.map(p => p.id), from = ids.indexOf(id);
    if (from < 0 || target < 0 || target >= ids.length || from === target) return;
    ids.splice(from, 1); ids.splice(target, 0, id); save('reorder', ids, id, action);
  }
  find('search-categories').addEventListener('click', event => {
    const button = event.target.closest('[data-category]'); if (!button || busy) return;
    category = button.dataset.category; page = 1; form.elements.major.value = ''; terms();
    find('search-categories').querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    search();
  });
  find('included-categories').addEventListener('click', event => {
    const button = event.target.closest('[data-included-category]'); if (!button || busy) return;
    includedCategory = button.dataset.includedCategory; renderIncluded();
  });
  form.addEventListener('submit', event => { event.preventDefault(); page = 1; search(); });
  form.elements.q.addEventListener('input', () => { clearTimeout(timer); page = 1; timer = setTimeout(search, 300); });
  form.addEventListener('change', event => {
    if (event.target === form.elements.q) return;
    page = 1; if (event.target === form.elements.major) terms(); search();
  });
  find('reset').addEventListener('click', () => {
    form.reset(); category = ''; page = 1;
    find('search-categories').querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.category === '')));
    terms(); search();
  });
  find('prev').addEventListener('click', () => { --page; search(); });
  find('next').addEventListener('click', () => { ++page; search(); });
  find('refresh').addEventListener('click', () => location.reload());
  find('clear').addEventListener('click', () => { selected.clear(); results.querySelectorAll('input:not(:disabled)').forEach(i => { i.checked = false; }); selection(); });
  find('add').addEventListener('click', () => save('add', [...selected.keys()]));
  list.addEventListener('click', event => {
    const button = event.target.closest('[data-row-action]'); if (!button || button.disabled) return;
    const id = Number(button.closest('[data-place-id]').dataset.placeId), action = button.dataset.rowAction;
    if (action === 'remove') save('remove', [id]);
    if (action === 'up' || action === 'down') move(id, state.items.findIndex(p => p.id === id) + (action === 'up' ? -1 : 1), action);
  });
  // Same pointer/handle and ↑↓ interaction pattern as admin-taxonomy-order.js.
  function clearDrag() {
    if (!drag) return;
    const previous = drag; drag = null;
    previous.row.classList.remove('is-dragging');
    list.querySelectorAll('.drop-before,.drop-after').forEach(n => n.classList.remove('drop-before', 'drop-after'));
    if (previous.handle.hasPointerCapture(previous.pointer)) previous.handle.releasePointerCapture(previous.pointer);
    return previous;
  }
  list.addEventListener('pointerdown', event => {
    const handle = event.target.closest('.best-drag');
    if (!handle || handle.disabled || event.button !== 0) return;
    const row = handle.closest('[data-place-id]');
    drag = {handle, row, pointer: event.pointerId, start: event.clientY, target: null, started: false};
    handle.setPointerCapture(event.pointerId); event.preventDefault();
  });
  list.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.pointer) return;
    if (!drag.started && Math.abs(event.clientY - drag.start) < 6) return;
    drag.started = true; drag.row.classList.add('is-dragging');
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-place-id]');
    list.querySelectorAll('.drop-before,.drop-after').forEach(n => n.classList.remove('drop-before', 'drop-after'));
    drag.target = null;
    if (target && list.contains(target) && target !== drag.row) {
      const rect = target.getBoundingClientRect(); drag.after = event.clientY > rect.top + rect.height / 2;
      drag.target = target; target.classList.add(drag.after ? 'drop-after' : 'drop-before');
    }
    if (event.clientY < 80) window.scrollBy(0, -18);
    if (event.clientY > window.innerHeight - 80) window.scrollBy(0, 18);
  });
  list.addEventListener('pointerup', () => {
    const previous = clearDrag(); if (!previous?.started || !previous.target) return;
    const id = Number(previous.row.dataset.placeId), targetId = Number(previous.target.dataset.placeId);
    const remaining = state.items.filter(p => p.id !== id);
    move(id, remaining.findIndex(p => p.id === targetId) + (previous.after ? 1 : 0));
  });
  list.addEventListener('pointercancel', clearDrag);
  list.addEventListener('lostpointercapture', clearDrag);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') clearDrag(); });
  terms(); renderIncluded(); search();
})();
