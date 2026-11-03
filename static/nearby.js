(function (root) {
  'use strict';
  function hasCoordinates(point) {
    return point && Number.isFinite(point.latitude) && Number.isFinite(point.longitude) &&
      Math.abs(point.latitude) <= 90 && Math.abs(point.longitude) <= 180;
  }

  function distanceKm(a, b) {
    const rad = Math.PI / 180;
    const halfLat = (b.latitude - a.latitude) * rad / 2;
    const halfLng = (b.longitude - a.longitude) * rad / 2;
    const h = Math.sin(halfLat) ** 2 +
      Math.cos(a.latitude * rad) * Math.cos(b.latitude * rad) * Math.sin(halfLng) ** 2;
    return 12742 * Math.asin(Math.sqrt(Math.min(1, h)));
  }

  function within(items, point, radius) {
    return items.filter(hasCoordinates)
      .map(item => ({...item, distance: distanceKm(point, item)}))
      .filter(item => item.distance <= radius)
      .sort((a, b) => a.distance - b.distance);
  }

  function formatDistance(km) {
    return km < 1 ? Math.min(999, Math.round(km * 1000)) + 'm' : km.toFixed(1) + 'km';
  }

  // The caller supplies the already filtered, distance-sorted public places.
  function recommendations(places) {
    const isPick = p => p.is_pick === true || p.is_pick === 1;
    const picks = places.filter(isPick);
    const verified = places.filter(p => !isPick(p) && p.verification_status === 'jejuno_verified');
    return picks.concat(verified).slice(0, 3);
  }

  function controls(container, onChange, getGeolocation = () => navigator.geolocation) {
    const locate = container.querySelector('[data-locate]');
    const toggle = container.querySelector('[data-near-toggle]');
    const radius = container.querySelector('[data-radius]');
    const message = container.querySelector('[data-location-message]');
    const state = {point: null, active: false, radius: Number(radius.value)};
    function update(reason) {
      toggle.disabled = radius.disabled = !state.point;
      toggle.setAttribute('aria-pressed', String(state.active));
      onChange(reason);
    }
    function failed(code) {
      locate.disabled = false;
      state.point = null;
      state.active = false;
      message.textContent = code === 1 ? '현재 위치를 확인할 수 없습니다. 위치 권한을 허용하거나 기존 지도와 필터를 이용해주세요.' :
        code === 'unsupported' ? '현재 브라우저에서는 내 위치 기능을 사용할 수 없습니다.' :
        '현재 위치를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.';
      update('error');
    }
    locate.onclick = () => {
      if (locate.disabled) return;
      locate.disabled = true;
      message.textContent = '위치 확인 중…';
      try {
        const geolocation = getGeolocation();
        if (!geolocation) return failed('unsupported');
        geolocation.getCurrentPosition(({coords}) => {
          if (!hasCoordinates(coords)) return failed();
          state.point = {latitude: coords.latitude, longitude: coords.longitude};
          locate.disabled = false;
          message.textContent = '현재 위치를 확인했습니다. 거리는 직선거리 기준입니다.';
          update('location');
        }, error => failed(error.code), {enableHighAccuracy: true, timeout: 12000, maximumAge: 0});
      } catch { failed(); }
    };
    toggle.onclick = () => { state.active = !state.active; update('filter'); };
    radius.onchange = () => { state.radius = Number(radius.value); state.active = true; update('filter'); };
    state.resetRadius = () => {
      state.active = false;
      state.radius = 5;
      radius.value = '5';
      toggle.setAttribute('aria-pressed', 'false');
    };
    return state;
  }

  function locationLayer(sdk, map) {
    let dot;
    const latLng = p => new sdk.LatLng(p.latitude, p.longitude);
    return {
      mark(point) {
        if (!point) { dot?.setMap(null); dot = null; return; }
        const position = latLng(point);
        if (dot) { dot.setPosition(position); return; }
        const node = document.createElement('span');
        node.className = 'nearby-dot';
        node.title = '현재 위치';
        node.setAttribute('role', 'img');
        node.setAttribute('aria-label', '현재 위치');
        dot = new sdk.CustomOverlay({map, position, content: node, xAnchor: .5, yAnchor: .5, zIndex: 4});
      },
      frame(point, results = []) {
        if (!results.length) {
          map.setLevel(5);
          map.setCenter(latLng(point));
          return;
        }
        const bounds = new sdk.LatLngBounds();
        bounds.extend(latLng(point));
        results.forEach(item => bounds.extend(latLng(item)));
        map.setBounds(bounds, 48, 32, 38, 32);
        if (map.getLevel() < 3) map.setLevel(3);
      }
    };
  }

  const api = {hasCoordinates, distanceKm, within, formatDistance, recommendations, controls, locationLayer};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.JejuNearby = api;
})(typeof window === 'undefined' ? globalThis : window);
