(function (root) {
  'use strict';
  const nearby = typeof module !== 'undefined' && module.exports ? require('./nearby.js') : root.JejuNearby;
  const {hasCoordinates} = nearby;
  // is_pick is supplied by the API's current monthly PICK lookup, not a category.
  // Only explicit editorial verification earns a blue marker, regardless of author.
  function placeStatus(place) {
    if(place.is_pick===true||place.is_pick===1)return {key:'pick',symbol:'★',label:'JEJU_KNOW PICK'};
    const label=place.status_label || '상태 확인 필요';
    if(place.verification_status==='jejuno_verified')return {key:'verified',symbol:'✓',label};
    return {key:'unverified',symbol:'●',label};
  }
  function makeStatusBadge(place,doc) {
    const status=placeStatus(place),badge=doc.createElement('span');
    badge.className=`map-status map-status--${status.key}`;
    badge.textContent=`${status.symbol} ${status.label}`;
    return badge;
  }
  function makeStatusPin(place,select,doc) {
    const status=placeStatus(place),pin=doc.createElement('button');
    pin.className=`map-pin map-status--${status.key}`;pin.type='button';
    const shape=doc.createElement('span');shape.className='map-pin-shape';
    shape.textContent=status.symbol;shape.setAttribute('aria-hidden','true');pin.append(shape);
    pin.setAttribute('aria-label',`${place.name} 지도 마커 · ${status.label}`);
    pin.setAttribute('title',status.label);pin.setAttribute('aria-pressed','false');
    pin.addEventListener('click',select);return pin;
  }
  function createMapView(maps, canvas, onSelect, makePin) {
    const map = new maps.Map(canvas, {center:new maps.LatLng(33.38,126.55), level:10});
    const pins = new Map();
    let visible = [];
    let locationState;
    const locator = nearby.locationLayer(maps, map);
    function fit() {
      if (locationState?.active) return locator.frame(locationState.point, visible);
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
      render(places, nextLocation, focusLocation = false) {
        locationState = nextLocation;
        locator.mark(locationState?.point);
        pins.forEach(pin => pin.overlay.setMap(null));
        pins.clear();
        visible = places.filter(hasCoordinates);
        visible.forEach(place => {
          const node = makePin(place,() => onSelect(place.id,true));
          const overlay = new maps.CustomOverlay({map,position:new maps.LatLng(place.latitude,place.longitude),content:node,yAnchor:1,zIndex:1,clickable:true});
          pins.set(place.id,{node,overlay});
        });
        // Only explicit filter/search changes and the fit button adjust bounds.
        if (focusLocation && locationState?.point && !locationState.active) {
          locator.frame(locationState.point);
        } else {
          fit();
        }
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
  if (typeof module !== 'undefined' && module.exports) { module.exports={hasCoordinates,placeStatus,makeStatusBadge,makeStatusPin,createMapView}; return; }

  const workspace=document.getElementById('mapWorkspace');
  if (!workspace) return;
  const cardStatus=root.PlaceCardStatus;
  const list=document.getElementById('mapResultsList');
  const query=document.getElementById('mapQuery');
  const notice=document.getElementById('mapNotice');
  const selected=document.getElementById('mapSelected');
  const categoryNav=document.querySelector('.map-categories');
  const count=document.getElementById('mapResultCount');
  const coordinateCount=document.getElementById('mapCoordinateCount');
  const fitButton=document.getElementById('mapFit');
  const taxonomy=JSON.parse(document.getElementById('mapCategoriesData').textContent);
  const categories=taxonomy.categories, filters=root.PlaceSearch;
  const detailNav=document.getElementById('mapDetails');
  const cuisineNav=document.getElementById('mapCuisines');
  const parameters=new URLSearchParams(location.search);
  const state={...filters.fromParams(parameters),places:[],selected:null};
  let focusedSlug=parameters.get('place')||'';
  let view=null, pending=null, sequence=0, debounce, searchFailed=false;
  query.value=state.q;
  const element=(tag,className,text)=>{
    const node=document.createElement(tag);
    if(className) node.className=className;
    if(text!==undefined) node.textContent=text;
    return node;
  };
  let centerOnLocation = false;
  const locationState = nearby.controls(workspace.querySelector('.nearby-controls'), reason => {
    mobile.updateLocation(locationState,reason);
    focusedSlug = '';
    centerOnLocation = reason === 'location';
    if (searchFailed) {
      view?.render([], locationState, centerOnLocation);
    } else {
      render();
    }
  });
  const mobile = root.mobileMapDiscovery(workspace);
  const quickFilters = root.MapQuickFilters.mount(document.getElementById('mapQuickFilters'),selectFilter);
  const regionLabel=value=>({'제주시':'제주시내','서귀포시':'서귀포시내'}[value]||value);
  function thumbnail(place) {
    const frame=element('div','map-thumb');
    frame.textContent=place.emoji||'📍';
    if (/^https?:\/\//i.test(place.image_url||'') || /^\/uploads\/places\/[0-9a-f]{32}\.webp$/.test(place.image_url||'')) {
      const image=element('img');image.src=place.image_url;image.alt='';image.loading='lazy';
      image.addEventListener('error',()=>image.remove());frame.append(image);
    }
    return frame;
  }
  function categoryName() { return filters.labels(state,taxonomy).join(' · ')||'전체'; }
  function renderDetails(){
    const active=document.activeElement;
    const focusGroup=active?.closest('#mapFilterSheet [id]');
    filters.chips(document.getElementById('mapRegions'),[{value:'',label:'전체'},...taxonomy.regions.map(v=>({value:v,label:taxonomy.region_labels?.[v]||v}))],state.region);
    filters.chips(document.getElementById('mapCompanions'),[{value:'',label:'상관없음'},...taxonomy.companions.map(v=>({value:v,label:taxonomy.companion_labels?.[v]||v}))],state.companions);
    const cuisine=filters.selectedCuisine(taxonomy,state);
    cuisineNav.hidden=state.category!=='food';
    filters.chips(cuisineNav,[{value:'',label:'전체 음식'},...filters.cuisinesFor(taxonomy).map(t=>({value:t.slug,label:t.name}))],cuisine,'map-category');
    const terms=filters.termsFor(taxonomy,state.category,cuisine);detailNav.hidden=!terms.length;
    if(terms.length) filters.chips(detailNav,[{value:'',label:'전체'},...terms.map(t=>({value:t.slug,label:t.name}))],state.terms,'map-category');
    else detailNav.replaceChildren();
    const focusSlot=document.getElementById('mapFocusSlot');
    focusSlot.hidden=state.category!=='cafe'||!taxonomy.cafe_focus?.length;
    if(!focusSlot.hidden) filters.chips(document.getElementById('mapFocus'),taxonomy.cafe_focus,state.focus);
    mobile.sections(state);
    quickFilters(state,taxonomy);
    if(focusGroup && active.dataset.value!==undefined) {
      Array.from(focusGroup.querySelectorAll('[data-value]')).find(b=>b.dataset.value===active.dataset.value)?.focus({preventScroll:true});
    }
  }
  function updateNotice() {
    if(!view||searchFailed) return;
    const mapped=state.places.filter(hasCoordinates).length;
    notice.hidden=mapped>0 || !!locationState.point || (!state.places.length && mobile.media.matches);
    notice.textContent=state.places.length?'이 조건의 장소는 아직 지도 위치를 준비 중이에요. 목록에서 상세 정보를 확인하세요.':'현재 조건에 맞는 장소가 없습니다.';
  }
  function selectPlace(id,fromMarker=false) {
    const place=state.places.find(p=>p.id===id);
    if(!place) return;
    state.selected=id;
    mobile.closeMenus();
    mobile.collapse();
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
    const content=element('div','map-selected-content');
    const header=element('div','map-selected-header'),intro=element('div','map-selected-intro');
    intro.append(element('h3','',place.name),element('p','map-meta',`${(place.region_label||regionLabel(place.region))} · ${place.category}`));
    header.append(thumbnail(place),intro);
    cardStatus.append(content,place);
    content.append(header,element('p','map-recommendation',place.one_line||'상세 페이지에서 장소 정보를 확인하세요.'));
    const attributes=filters.discoveryAttributes(place,taxonomy),discovery=element('dl','map-discovery');
    for(const [label,value] of [['① 어디',attributes.region],['② 무엇',attributes.what],['③ 누구랑',attributes.companions]]){
      const row=element('div'),description=element('dd','',value);
      if(label==='③ 누구랑'&&attributes.moreCompanions){
        const more=element('a','map-companions-more',` +${attributes.moreCompanions}`);more.href=`/place/${encodeURIComponent(place.slug)}`;more.setAttribute('aria-label',`동행 ${attributes.moreCompanions}개 더 보기`);description.append(more);
      }
      row.append(element('dt','',label),description);discovery.append(row);
    }
    content.append(discovery);
    if(place.category_slug==='food'&&place.signature_menu){
      const menu=element('p','map-signature-menu','대표메뉴 · '+place.signature_menu.trim().split('\n').filter(Boolean).join(' · '));
      content.append(menu);
    }
    if(!hasCoordinates(place)) content.append(element('p','map-location-missing','지도 위치 미등록'));
    const link=element('a','detail-link','상세보기 →');link.href=`/place/${encodeURIComponent(place.slug)}`;
    const correction=element('a','detail-link','정보 바로잡기');correction.href=`/places/${encodeURIComponent(place.id)}/suggest`;
    const actions=element('div','map-selected-actions');actions.append(link,correction);
    body.append(content,actions);selected.append(close,body);selected.hidden=false;
    if(mobile.media.matches) close.focus({preventScroll:true});
  }
  function renderRecommendations(places) {
    const section = element('section', 'nearby-recs');
    section.append(element('h3', '', 'JEJU_KNOW 주변 추천'));
    const picks = nearby.recommendations(places);
    for (const place of picks) {
      const button = element('button', '', place.name + ' · ' + nearby.formatDistance(place.distance));
      button.type = 'button';
      button.append(' ', makeStatusBadge(place, document));
      button.onclick = () => selectPlace(place.id);
      section.append(button);
    }
    if (!picks.length) section.append(element('p', '', '현재 반경에는 JEJU_KNOW 추천 장소가 없습니다.'));
    list.append(section, element('h3', 'nearby-list-heading', '내 주변 장소 · 거리순'));
  }
  function render() {
    updateFilterSummary();
    const name=categoryName();
    const places = locationState.active ? nearby.within(state.places, locationState.point, locationState.radius) : state.places;
    count.textContent = (locationState.active ? '내 주변 ' + locationState.radius + 'km · ' : '') + `${name} · 총 ${places.length}곳`;
    if (!places.length && mobile.media.matches) count.textContent='조건에 맞는 장소가 없습니다.';
    mobile.finished(places.length);
    const mapped=places.filter(hasCoordinates).length;
    coordinateCount.textContent=locationState.active ? '현재 위치 기준 직선거리' : `지도 위치 등록 ${mapped}곳`+(mapped<state.places.length?` · 위치 미등록 ${state.places.length-mapped}곳`:'');
    categoryNav.querySelectorAll('button').forEach(button=>{
      const active=button.dataset.pick?state.pick==='current':button.dataset.category===state.category;
      button.classList.toggle('is-selected',active);button.setAttribute('aria-pressed',String(active));
    });
    list.replaceChildren();selected.hidden=true;state.selected=null;
    if (locationState.active) renderRecommendations(places);
    if(!places.length) {
      const empty=element('div','map-empty');
      if (locationState.active) empty.append(element('strong','','반경 안에 장소가 없습니다. 반경을 넓히거나 내 주변을 해제해 보세요.'));
      else empty.append(element('strong','',state.q?'검색 조건에 맞는 장소가 없습니다.':state.category?`현재 등록된 ${name} 장소가 없습니다.`:'현재 등록된 장소가 없습니다.'),element('p','','알고 있는 장소가 있나요? 지도 위의 + 장소 알려주기 버튼을 이용해주세요.'));
      list.append(empty);
    }
    places.forEach(place=>{
      const card=element('article','map-place-card');card.dataset.placeId=place.id;
      const button=element('button','map-place-select');button.type='button';button.setAttribute('aria-pressed','false');button.setAttribute('aria-label',`${place.name} 위치 보기`);
      const body=element('div','map-card-body');
      cardStatus.append(body,place);
      body.append(element('p','map-meta',`${(place.region_label||regionLabel(place.region))} · ${place.category}`),element('h3','',place.name),element('p','map-recommendation',(state.pick?place.pick_note||place.reason:'')||place.one_line||'상세 페이지에서 장소 정보를 확인하세요.'));
      if (locationState.active) body.append(element('span', 'nearby-distance', nearby.formatDistance(place.distance)));
      if(!hasCoordinates(place)) body.append(element('span','map-location-missing','지도 위치 미등록'));
      button.append(thumbnail(place),body);button.addEventListener('click',()=>selectPlace(place.id));
      const link=element('a','map-card-detail','상세보기 →');link.href=`/place/${encodeURIComponent(place.slug)}`;
      card.append(button,link);list.append(card);
    });
    if(view) view.render(places, locationState, centerOnLocation);
    const focused=places.find(p=>p.slug===focusedSlug);if(focused)selectPlace(focused.id);
    fitButton.disabled=!view||!mapped;
    updateNotice();
  }
  async function search(historyMode='push') {
    centerOnLocation = false;
    clearTimeout(debounce);pending?.abort();pending=new AbortController();const request=++sequence;
    list.setAttribute('aria-busy','true');
    mobile.searching();
    count.textContent='장소 정보를 불러오고 있어요.';
    const params=filters.params(state);
    const url=filters.href(location.pathname,state);
    if(historyMode&&url!==location.pathname+location.search)history[historyMode+'State'](null,'',url);
    document.getElementById('mapFindLink').href=filters.href('/find',state);
    document.getElementById('mapRegisterLink').href=filters.registrationHref(state);
    filters.activeChips(document.getElementById('mapActiveFilters'),state,taxonomy,item=>{filters.removeFilter(state,item.key,item.value);query.value=state.q;renderDetails();search();});
    updateFilterSummary();
    try {
      const response=await fetch('/api/places?'+params,{signal:pending.signal,headers:{Accept:'application/json'}});
      if(!response.ok) throw new Error('장소를 불러오지 못했습니다. 다시 검색해 주세요.');
      const places=await response.json();if(request!==sequence)return;
      searchFailed=false;state.places=places;render();
    } catch(error) {
      if(error.name==='AbortError'||request!==sequence)return;
      searchFailed=true;state.places=[];render();count.textContent='장소를 불러오지 못했습니다.';
      notice.hidden=false;notice.textContent='장소 정보를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.';
      const retry=element('button','secondary-btn','다시 시도');retry.type='button';retry.addEventListener('click',()=>search(false));
      list.replaceChildren(element('p','map-empty','연결을 확인한 후 다시 시도해주세요.'),retry);
      mobile.finished(0,true);
    } finally { if(request===sequence)list.setAttribute('aria-busy','false'); }
  }
  categoryNav.addEventListener('click',event=>{const button=event.target.closest('[data-category],[data-pick]');if(!button)return;if(button.dataset.pick)state.pick=state.pick?'':'current';else filters.select(state,'category',button.dataset.category||'');renderDetails();state.q=query.value.trim();search();});
  function updateFilterSummary(){
    const selected=filters.selectedFilters(state,taxonomy);
    const extraCount=selected.filter(item=>!['category','cuisine','terms','q','pick'].includes(item.key)).length;
    document.getElementById('mapOpenFilters').textContent='상세필터'+(extraCount?' · '+extraCount:'');
    document.getElementById('mapReset').classList.toggle('is-empty',!selected.length&&!locationState.active);
  }
  function selectFilter(key,value){
    if(key==='terms' && state.category==='food'){
      state.cuisine=filters.selectedCuisine(taxonomy,state);
      const allowed=filters.termsFor(taxonomy,'food',state.cuisine).map(t=>t.slug);
      state.terms=state.terms.filter(t=>allowed.includes(t));
    }
    filters.select(state,key,value);renderDetails();search();
  }
  for(const [node,key] of [[document.getElementById('mapRegions'),'region'],[document.getElementById('mapCompanions'),'companions'],[document.getElementById('mapFocus'),'focus'],[cuisineNav,'cuisine'],[detailNav,'terms']])node.addEventListener('click',event=>{
    const button=event.target.closest('[data-value]');if(button)selectFilter(key,button.dataset.value);
  });
  document.getElementById('mapSearch').addEventListener('submit',event=>{event.preventDefault();state.q=query.value.trim();search();});
  query.addEventListener('input',()=>{clearTimeout(debounce);debounce=setTimeout(()=>{state.q=query.value.trim();search('replace');},250);});
  document.getElementById('mapReset').addEventListener('click',()=>{locationState.resetRadius();mobile.updateLocation(locationState);Object.assign(state,filters.defaultState());query.value='';renderDetails();search();});
  addEventListener('popstate',()=>{const params=new URLSearchParams(location.search);focusedSlug=params.get('place')||'';Object.assign(state,filters.fromParams(params));query.value=state.q;renderDetails();search(false);});
  fitButton.addEventListener('click',()=>view?.fit());
  mobile.media.addEventListener('change',()=>{updateNotice();});
  document.getElementById('mapFilterApply').addEventListener('click',()=>{if(searchFailed)search(false);});
  renderDetails();search(false);
  root.JejunoKakao.load(workspace.dataset.kakaoKey).then(maps=>{
    view=createMapView(maps,document.getElementById('kakaoMap'),selectPlace,(place,select)=>makeStatusPin(place,select,document));
    const initialSelection = state.selected;
    if (!searchFailed) render();
    else view.render([], locationState, centerOnLocation);
    if (initialSelection !== null && state.selected === null) selectPlace(initialSelection);
    new ResizeObserver(()=>view.resize()).observe(document.getElementById('kakaoMap'));
  }).catch(error=>{notice.hidden=false;notice.textContent=error.message;});
})(typeof window==='undefined'?globalThis:window);
