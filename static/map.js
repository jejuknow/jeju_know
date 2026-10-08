(function (root) {
  'use strict';
  function hasCoordinates(place) {
    return typeof place.latitude === 'number' && typeof place.longitude === 'number' &&
      Number.isFinite(place.latitude) && Number.isFinite(place.longitude) &&
      Math.abs(place.latitude) <= 90 && Math.abs(place.longitude) <= 180;
  }
  function createMapView(maps, canvas, onSelect, makePin) {
    const map = new maps.Map(canvas, {center:new maps.LatLng(33.38,126.55), level:10});
    const pins = new Map();
    let visible = [];
    function fit() {
      if (!visible.length) return;
      if (visible.length === 1) {
        map.setCenter(new maps.LatLng(visible[0].latitude, visible[0].longitude));
        map.setLevel(4);
      } else {
        const bounds = new maps.LatLngBounds();
        visible.forEach(p => bounds.extend(new maps.LatLng(p.latitude,p.longitude)));
        map.setBounds(bounds,100,50,70,50);
      }
    }
    return {
      render(places) {
        pins.forEach(pin => pin.overlay.setMap(null));
        pins.clear();
        visible = places.filter(hasCoordinates);
        visible.forEach((place, index) => {
          const node = makePin(place,index+1,() => onSelect(place.id,true));
          const overlay = new maps.CustomOverlay({map,position:new maps.LatLng(place.latitude,place.longitude),content:node,yAnchor:1,zIndex:1,clickable:true});
          pins.set(place.id,{node,overlay});
        });
        // Only explicit filter/search changes and the fit button adjust bounds.
        fit();
      },
      select(id) {
        pins.forEach((pin,key) => {
          pin.node.setAttribute('aria-pressed',String(key===id));
          pin.overlay.setZIndex(key===id?10:1);
        });
        const place = visible.find(p => p.id===id);
        if (place) map.panTo(new maps.LatLng(place.latitude,place.longitude));
      },
      resize() { const center=map.getCenter(); map.relayout(); map.setCenter(center); },
      fit
    };
  }
  if (typeof module !== 'undefined' && module.exports) { module.exports={hasCoordinates,createMapView}; return; }

  const workspace=document.getElementById('mapWorkspace');
  if (!workspace) return;
  const list=document.getElementById('mapResultsList');
  const query=document.getElementById('mapQuery');
  const notice=document.getElementById('mapNotice');
  const selected=document.getElementById('mapSelected');
  const categoryNav=document.querySelector('.map-categories');
  const count=document.getElementById('mapResultCount');
  const coordinateCount=document.getElementById('mapCoordinateCount');
  const fitButton=document.getElementById('mapFit');
  const sheetToggle=document.getElementById('mapSheetToggle');
  const categories=JSON.parse(document.getElementById('mapCategoriesData').textContent);
  const parameters=new URLSearchParams(location.search);
  const state={category:parameters.get('category')||'', q:parameters.get('q')||'', places:[], selected:null};
  let view=null, pending=null, sequence=0, debounce;
  query.value=state.q;
  const element=(tag,className,text)=>{
    const node=document.createElement(tag);
    if(className) node.className=className;
    if(text!==undefined) node.textContent=text;
    return node;
  };
  const regionLabel=value=>({'제주시':'제주시내','서귀포시':'서귀포시내'}[value]||value);
  function thumbnail(place) {
    const frame=element('div','map-thumb');
    frame.textContent=place.emoji||'📍';
    if (/^https?:\/\//i.test(place.image_url||'')) {
      const image=element('img');image.src=place.image_url;image.alt='';image.loading='lazy';
      image.addEventListener('error',()=>image.remove());frame.append(image);
    }
    return frame;
  }
  function categoryName() { return state.category ? categories.find(c=>c.slug===state.category)?.name||'선택한 카테고리' : '전체'; }
  function updateNotice() {
    if(!view) return;
    const mapped=state.places.filter(hasCoordinates).length;
    notice.hidden=mapped>0;
    notice.textContent=state.places.length?'이 조건의 장소는 아직 지도 위치를 준비 중이에요. 목록에서 상세 정보를 확인하세요.':'현재 조건에 맞는 장소가 없습니다.';
  }
  function setListExpanded(expanded) {
    workspace.classList.toggle('is-list-expanded',expanded);
    sheetToggle.setAttribute('aria-expanded',String(expanded));
    sheetToggle.textContent=expanded?'목록 줄이기 ⌄':'목록 펼치기 ⌃';
  }
  function selectPlace(id,fromMarker=false) {
    const place=state.places.find(p=>p.id===id);
    if(!place) return;
    state.selected=id;
    // Make room for the selected place before panning on a resized mobile map.
    if(workspace.classList.contains('is-list-expanded')) {
      setListExpanded(false);
      view?.resize();
    }
    list.querySelectorAll('.map-place-card').forEach(card=>{
      const active=Number(card.dataset.placeId)===id;
      card.classList.toggle('is-selected',active);
      card.querySelector('.map-place-select').setAttribute('aria-pressed',String(active));
      if(active&&fromMarker) list.scrollTo({top:Math.max(0,list.scrollTop+card.getBoundingClientRect().top-list.getBoundingClientRect().top-8),behavior:'smooth'});
    });
    if(view) view.select(id);
    selected.replaceChildren();
    const close=element('button','map-close','×');close.type='button';close.setAttribute('aria-label','선택한 장소 닫기');
    close.addEventListener('click',()=>{
      selected.hidden=true;state.selected=null;if(view)view.select(null);
      list.querySelectorAll('.map-place-card').forEach(card=>{card.classList.remove('is-selected');card.querySelector('button').setAttribute('aria-pressed','false');});
    });
    const body=element('div','map-selected-body');
    body.append(element('p','map-meta',`${place.category} · ${regionLabel(place.region)}`),element('h3','',place.name),element('p','map-recommendation',place.one_line||'제주노가 기록한 장소입니다.'));
    if(!hasCoordinates(place)) body.append(element('p','map-location-missing','지도 위치 미등록'));
    const link=element('a','detail-link','상세보기 →');link.href=`/place/${encodeURIComponent(place.slug)}`;
    body.append(link);selected.append(close,thumbnail(place),body);selected.hidden=false;
  }
  function render() {
    const name=categoryName();
    count.textContent=`${name} · 총 ${state.places.length}곳`;
    const mapped=state.places.filter(hasCoordinates).length;
    coordinateCount.textContent=`지도 위치 등록 ${mapped}곳`+(mapped<state.places.length?` · 위치 미등록 ${state.places.length-mapped}곳`:'');
    categoryNav.querySelectorAll('button').forEach(button=>{
      const active=button.dataset.category===state.category;
      button.classList.toggle('is-selected',active);button.setAttribute('aria-pressed',String(active));
    });
    list.replaceChildren();selected.hidden=true;state.selected=null;
    if(!state.places.length) {
      const empty=element('div','map-empty');
      empty.append(element('strong','',state.q?'검색 조건에 맞는 장소가 없습니다.':state.category?`현재 등록된 ${name}가 없습니다.`:'현재 등록된 장소가 없습니다.'),element('p','','다른 검색어나 카테고리를 직접 선택해 주세요.'));
      list.append(empty);
    }
    state.places.forEach(place=>{
      const card=element('article','map-place-card');card.dataset.placeId=place.id;
      const button=element('button','map-place-select');button.type='button';button.setAttribute('aria-pressed','false');button.setAttribute('aria-label',`${place.name} 위치 보기`);
      const body=element('div','map-card-body');
      body.append(element('p','map-meta',`${place.category} · ${regionLabel(place.region)}`),element('h3','',place.name),element('p','map-recommendation',place.one_line||'제주노가 기록한 장소입니다.'));
      if(place.is_pick) body.append(element('span','badge pick','JEJUNO PICK'));
      if(!hasCoordinates(place)) body.append(element('span','map-location-missing','지도 위치 미등록'));
      button.append(thumbnail(place),body);button.addEventListener('click',()=>selectPlace(place.id));
      const link=element('a','map-card-detail','상세보기 →');link.href=`/place/${encodeURIComponent(place.slug)}`;
      card.append(button,link);list.append(card);
    });
    if(view) view.render(state.places);
    fitButton.disabled=!view||!mapped;
    updateNotice();
  }
  async function search() {
    clearTimeout(debounce);pending?.abort();pending=new AbortController();const request=++sequence;
    list.setAttribute('aria-busy','true');
    const params=new URLSearchParams();if(state.category)params.set('category',state.category);if(state.q)params.set('q',state.q);
    history.replaceState(null,'',location.pathname+(params.size?'?'+params.toString():''));
    try {
      const response=await fetch('/api/places?'+params,{signal:pending.signal,headers:{Accept:'application/json'}});
      if(!response.ok) throw new Error('장소를 불러오지 못했습니다. 다시 검색해 주세요.');
      const places=await response.json();if(request!==sequence)return;
      state.places=places;render();
    } catch(error) {
      if(error.name==='AbortError'||request!==sequence)return;
      state.places=[];render();count.textContent='장소를 불러오지 못했습니다.';
      list.replaceChildren(element('p','map-empty','연결을 확인하고 검색 버튼을 다시 눌러 주세요.'));
    } finally { if(request===sequence)list.setAttribute('aria-busy','false'); }
  }
  categoryNav.addEventListener('click',event=>{const button=event.target.closest('[data-category]');if(!button)return;state.category=button.dataset.category;state.q=query.value.trim();search();});
  document.getElementById('mapSearch').addEventListener('submit',event=>{event.preventDefault();state.q=query.value.trim();search();});
  query.addEventListener('input',()=>{clearTimeout(debounce);debounce=setTimeout(()=>{state.q=query.value.trim();search();},250);});
  fitButton.addEventListener('click',()=>view?.fit());
  sheetToggle.addEventListener('click',()=>setListExpanded(!workspace.classList.contains('is-list-expanded')));
  search();
  root.JejunoKakao.load(workspace.dataset.kakaoKey).then(maps=>{
    view=createMapView(maps,document.getElementById('kakaoMap'),selectPlace,(place,index,select)=>{
      const pin=element('button','map-pin',String(index));pin.type='button';pin.setAttribute('aria-label',`${place.name} 지도 마커`);pin.setAttribute('aria-pressed','false');pin.addEventListener('click',select);return pin;
    });
    view.render(state.places);fitButton.disabled=!state.places.some(hasCoordinates);updateNotice();
    new ResizeObserver(()=>view.resize()).observe(document.getElementById('kakaoMap'));
    if(state.selected!==null) view.select(state.selected);
  }).catch(error=>{notice.hidden=false;notice.textContent=error.message;});
})(typeof window==='undefined'?globalThis:window);
