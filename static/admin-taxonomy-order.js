/* Shared taxonomy controls; guarded server writes are authoritative. */
(() => {
  'use strict';
  const root = document.querySelector('[data-taxonomy-admin]');
  if (!root) return;
  const states = JSON.parse(document.getElementById('taxonomyOrderStates').textContent);
  const searching = root.dataset.searching === 'true';
  const status = root.querySelector('[data-taxonomy-status]');
  const csrf = root.querySelector('input[name=csrf_token]').value;
  const groups = [...root.querySelectorAll('[data-order-group]')];
  let busy = false, stale = false, drag = null, noticeTimer;
  const items = group => [...group.querySelectorAll('[data-order-item]')]
    .filter(item => item.closest('[data-order-group]') === group);
  const own = (item, selector) => [...item.querySelectorAll(selector)]
    .filter(node => node.closest('[data-order-item]') === item);
  function notify(message, error = false) {
    clearTimeout(noticeTimer);
    status.textContent = message;
    status.hidden = false;
    status.classList.toggle('is-error', error);
    status.setAttribute('role', error ? 'alert' : 'status');
    if (stale) {
      const link = document.createElement('a');
      link.href = location.href; link.textContent = ' 최신 목록 새로고침';
      link.addEventListener('click', event => { event.preventDefault(); location.reload(); });
      status.append(link);
    }
    if (!error) noticeTimer = setTimeout(() => { status.hidden = true; }, 4000);
  }
  function controls() {
    groups.forEach(group => {
      const rows = items(group);
      const complete = rows.length === states[group.dataset.orderGroup]?.items.length;
      rows.forEach((row, index) => {
        own(row, '[data-order-step]').forEach(button => {
          button.disabled = busy || stale || searching || !complete ||
            (button.dataset.orderStep === '-1' ? index === 0 : index === rows.length - 1);
        });
        own(row, '[data-order-drag]').forEach(button => { button.disabled = busy || stale || searching || !complete; });
        own(row, '[data-taxonomy-active]').forEach(button => {
          button.disabled = busy || stale || Boolean(states[group.dataset.orderGroup]?.items.find(r => String(r.id) === row.dataset.orderItem)?.activation_locked);
        });
      });
    });
    root.setAttribute('aria-busy', String(busy));
  }
  function renderState(group) {
    const current = states[group.dataset.orderGroup];
    if (group === groups[0]) {
      const nav = root.querySelector('.taxonomy-jumps');
      current.items.forEach(record => {
        const link = nav?.querySelector('[data-taxonomy-jump="' + record.id + '"]');
        if (link) nav.append(link);
      });
    }
    items(group).forEach(row => {
      const record = current.items.find(item => String(item.id) === row.dataset.orderItem);
      if (!record) return;
      own(row, '[data-order-number]').forEach(node => { node.textContent = record.sort_order; });
      own(row, '[data-taxonomy-active]').forEach(button => {
        const enabled = Boolean(record.is_active);
        button.hidden = Boolean(record.activation_locked);
        button.setAttribute('aria-checked', String(enabled));
        button.setAttribute('aria-label', button.dataset.itemName + (enabled ? ' 비활성화' : ' 활성화'));
        button.querySelector('b').textContent = enabled ? 'ON' : 'OFF';
      });
      own(row, '[data-activation-lock]').forEach(label => {
        label.hidden = !record.activation_locked;
        label.title = record.activation_reason || '';
        label.querySelector('b').textContent = record.activation_label || '';
      });
      if (row.matches('[data-taxonomy-group]')) {
        const badge = row.querySelector(':scope > summary .admin-badge');
        if (badge) {
          badge.textContent = record.is_active ? 'ON' : 'OFF';
          badge.classList.toggle('badge-success', Boolean(record.is_active));
          badge.classList.toggle('badge-neutral', !record.is_active);
        }
      }
      row.querySelectorAll('#edit-taxonomy-' + record.id + ' input[name=sort_order]').forEach(input => {
        if (input.value === input.defaultValue) input.value = record.sort_order;
        input.defaultValue = record.sort_order;
      });
      row.querySelectorAll('#edit-taxonomy-' + record.id + ' input[name=is_active]').forEach(input => {
        if (input.checked === input.defaultChecked) input.checked = Boolean(record.is_active);
        input.defaultChecked = Boolean(record.is_active);
      });
    });
  }
  async function persist(group, path, fields, rollback) {
    const focus = document.activeElement;
    busy = true; controls();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, {method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams({csrf_token: csrf, version: states[group.dataset.orderGroup].version, ...fields})});
      if (response.redirected || !response.headers.get('content-type')?.includes('application/json')) {
        stale = true;
        throw new Error('로그인 또는 저장 상태를 확인한 뒤 최신 목록을 다시 불러와주세요.');
      }
      const result = await response.json();
      if (!response.ok) {
        if (response.status === 409 || response.status === 400) stale = true;
        throw new Error(result.error || '저장하지 못했습니다. 다시 시도해주세요.');
      }
      states[group.dataset.orderGroup] = result;
      renderState(group); notify(result.message);
    } catch (error) {
      const connectionError = error instanceof TypeError || error.name === 'AbortError';
      if (connectionError) stale = true;
      rollback();
      notify(connectionError ? '서버 응답을 확인하지 못했습니다. 최신 목록에서 저장 상태를 확인해주세요.' : error.message, true);
    } finally {
      clearTimeout(timeout);
      busy = false; controls();
      if (document.activeElement === document.body && focus instanceof HTMLElement && focus.isConnected && !focus.disabled) {
        focus.focus({preventScroll: true});
      }
    }
  }
  function move(group, row, targetIndex) {
    if (busy || stale || searching) return;
    const original = items(group), next = [...original];
    const oldIndex = next.indexOf(row);
    if (oldIndex < 0 || targetIndex === oldIndex || targetIndex < 0 || targetIndex >= next.length) return;
    next.splice(oldIndex, 1); next.splice(targetIndex, 0, row);
    const focus = document.activeElement;
    next.forEach(item => group.append(item));
    next.forEach((item, i) => own(item, '[data-order-number]').forEach(node => { node.textContent = i + 1; }));
    if (focus instanceof HTMLElement) focus.focus({preventScroll: true});
    persist(group, root.dataset.reorderUrl || '/admin/categories/reorder', {
      parent_id: group.dataset.orderGroup, ordered_ids: JSON.stringify(next.map(row =>
        states[group.dataset.orderGroup].items.find(item => String(item.id) === row.dataset.orderItem).id))
    }, () => { original.forEach(item => group.append(item)); renderState(group); });
  }
  root.addEventListener('click', event => {
    const step = event.target.closest('[data-order-step]');
    if (step && !step.disabled) {
      const row = step.closest('[data-order-item]'), group = row.closest('[data-order-group]');
      move(group, row, items(group).indexOf(row) + Number(step.dataset.orderStep));
    }
    const toggle = event.target.closest('[data-taxonomy-active]');
    if (toggle && !toggle.disabled) {
      const row = toggle.closest('[data-order-item]'), group = row.closest('[data-order-group]');
      const active = toggle.getAttribute('aria-checked') !== 'true';
      toggle.setAttribute('aria-checked', String(active)); toggle.querySelector('b').textContent = active ? 'ON' : 'OFF';
      const path = root.dataset.activeUrl?.replace('{id}', encodeURIComponent(toggle.dataset.itemId)) || '/admin/categories/' + toggle.dataset.itemId + '/active';
      persist(group, path, {is_active: active ? '1' : '0'}, () => renderState(group));
    }
  });
  function clearDrag() {
    root.querySelectorAll('.is-dragging,.drop-before,.drop-after').forEach(node => node.classList.remove('is-dragging', 'drop-before', 'drop-after'));
    drag = null;
  }
  root.addEventListener('pointerdown', event => {
    const handle = event.target.closest('[data-order-drag]');
    if (!handle || handle.disabled || event.button !== 0) return;
    const row = handle.closest('[data-order-item]');
    drag = {row, group: row.closest('[data-order-group]'), handle, pointer: event.pointerId,
      x: event.clientX, y: event.clientY, moved: false, target: null};
    handle.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  root.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.pointer) return;
    if (!drag.moved && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 6) return;
    drag.moved = true; drag.row.classList.add('is-dragging');
    root.querySelectorAll('.drop-before,.drop-after').forEach(node => node.classList.remove('drop-before', 'drop-after'));
    let target = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-order-item]');
    // A major may be dropped over its sibling's content, but a subtype cannot leave its tbody.
    if (drag.row.matches('[data-taxonomy-group]')) target = target?.closest('[data-taxonomy-group]');
    drag.target = target && target !== drag.row && target.closest('[data-order-group]') === drag.group ? target : null;
    if (drag.target) {
      const rect = drag.target.getBoundingClientRect();
      drag.after = event.clientY > rect.top + rect.height / 2;
      drag.target.classList.add(drag.after ? 'drop-after' : 'drop-before');
    }
  });
  root.addEventListener('pointerup', event => {
    if (!drag || event.pointerId !== drag.pointer) return;
    const current = drag;
    clearDrag();
    current.handle.releasePointerCapture(event.pointerId);
    if (!current.moved) return;
    if (!current.target) { notify('같은 그룹 안에서만 순서를 변경할 수 있습니다.', true); return; }
    const remaining = items(current.group).filter(row => row !== current.row);
    move(current.group, current.row, remaining.indexOf(current.target) + (current.after ? 1 : 0));
  });
  root.addEventListener('pointercancel', clearDrag);
  root.addEventListener('lostpointercapture', clearDrag);
  root.addEventListener('keydown', event => { if (event.key === 'Escape') clearDrag(); });
  root.querySelectorAll('[data-order-new]').forEach(input => {
    const parent = input.form.elements.namedItem('parent_id');
    parent?.addEventListener('change', () => { input.value = (states[parent.value]?.items.length || 0) + 1; });
  });
  groups.forEach(renderState);
  controls();
})();
