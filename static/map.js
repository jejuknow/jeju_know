(function (root) {
  'use strict';
  function hasCoordinates(place) {
    return typeof place.latitude === 'number' && typeof place.longitude === 'number' &&
      Number.isFinite(place.latitude) && Number.isFinite(place.longitude) &&
      Math.abs(place.latitude) <= 90 && Math.abs(place.longitude) <= 180;
  }
  // is_pick is supplied by the API's current monthly PICK lookup, not a category.
  // Only explicit editorial verification earns a blue marker, regardless of author.
  function placeStatus(place) {
    if(place.is_pick===true||place.is_pick===1)return {key:'pick',symbol:'★',label:'JEJU_KNOW PICK'};
    if(place.verification_status==='jejuno_verified')return {key:'verified',symbol:'✓',label:'JEJU_KNOW 확인'};
    const source=place.source_type==='user'?'사용자 등록':'JEJU_KNOW 등록';
    const verification='미확인';
    return {key:'unverified',symbol:'●',label:`${source} · ${verification}`};
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
        visible.forEach(place => {
          const node = makePin(place,() => onSelect(place.id,true));
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
  if (typeof module !== 'undefined' && module.exports) { module.exports={hasCoordinates,placeStatus,makeStatusBadge,makeStatusPin,createMapView}; return; }

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
  const legend=document.getElementById('mapLegend');
  const mobileLegend=root.matchMedia('(max-width:700px)');
  legend.open=!mobileLegend.matches;
  mobileLegend.addEventListener('change',event=>{legend.open=!event.matches;});
  const taxonomy=JSON.parse(document.getElementById('mapCategoriesData').textContent);
  const categories=taxonomy.categories, filters=root.PlaceSearch;
  const detailNav=document.getElementById('mapDetails');
  const cuisineNav=document.getElementById('mapCuisines');
  const parameters=new URLSearchParams(location.search);
  const state={...filters.fromParams(parameters),places:[],selected:null};
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
    if (/^https?:\/\//i.test(place.image_url||'') || /^\/uploads\/places\/[0-9a-f]{32}\.webp$/.test(place.image_url||'')) {
      const image=element('img');image.src=place.image_url;image.alt='';image.loading='lazy';
      image.addEventListener('error',()=>image.remove());frame.append(image);
    }
    return frame;
  }
  function categoryName() { return filters.labels(state,taxonomy).join(' · ')||'전체'; }
  function renderDetails(){
    const cuisine=filters.selectedCuisine(taxonomy,state);
    cuisineNav.hidden=state.category!=='food';
    filters.chips(cuisineNav,[{value:'',label:'전체 음식'},...filters.cuisinesFor(taxonomy).map(t=>({value:t.slug,label:t.name}))],cuisine,'map-category');
    const terms=filters.termsFor(taxonomy,state.category,cuisine);detailNav.hidden=!terms.length;
    if(!terms.length){detailNav.replaceChildren();return;}
    filters.chips(detailNav,[{value:'',label:'전체'},...terms.map(t=>({value:t.slug,label:t.name}))],state.terms,'map-category');
  }
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
    if(mobileLegend.matches)legend.open=false;
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
    const content=element('div','map-selected-content');
    const header=element('div','map-selected-header'),intro=element('div','map-selected-intro');
    intro.append(element('h3','',place.name),element('p','map-meta',`${(place.region_label||regionLabel(place.region))} · ${place.category}`));
    header.append(thumbnail(place),intro);
    content.append(header,element('p','map-recommendation',place.one_line||'상세 페이지에서 장소 정보를 확인하세요.'));
    const statuses=element('div','map-selected-statuses');
    statuses.append(makeStatusBadge(place,document));
    if(place.is_pick)statuses.append(makeStatusBadge({...place,is_pick:false},document));
    content.append(statuses);
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
  }
  function render() {
    const name=categoryName();
    count.textContent=`${name} · 총 ${state.places.length}곳`;
    const mapped=state.places.filter(hasCoordinates).length;
    coordinateCount.textContent=`지도 위치 등록 ${mapped}곳`+(mapped<state.places.length?` · 위치 미등록 ${state.places.length-mapped}곳`:'');
    categoryNav.querySelectorAll('button').forEach(button=>{
      const active=button.dataset.pick?state.pick==='current':button.dataset.category===state.category;
      button.classList.toggle('is-selected',active);button.setAttribute('aria-pressed',String(active));
    });
    list.replaceChildren();selected.hidden=true;state.selected=null;
    if(!state.places.length) {
      const empty=element('div','map-empty');
      empty.append(element('strong','',state.q?'검색 조건에 맞는 장소가 없습니다.':state.category?`현재 등록된 ${name} 장소가 없습니다.`:'현재 등록된 장소가 없습니다.'),element('p','','알고 있는 장소가 있나요? 지도 위의 + 장소 알려주기 버튼을 이용해주세요.'));
      list.append(empty);
    }
    state.places.forEach(place=>{
      const card=element('article','map-place-card');card.dataset.placeId=place.id;
      const button=element('button','map-place-select');button.type='button';button.setAttribute('aria-pressed','false');button.setAttribute('aria-label',`${place.name} 위치 보기`);
      const body=element('div','map-card-body');
      body.append(element('p','map-meta',`${place.category} · ${(place.region_label||regionLabel(place.region))}`),element('h3','',place.name),element('p','map-recommendation',(state.pick?place.pick_note||place.reason:'')||place.one_line||'상세 페이지에서 장소 정보를 확인하세요.'));
      body.append(makeStatusBadge(place,document));
      if(!hasCoordinates(place)) body.append(element('span','map-location-missing','지도 위치 미등록'));
      button.append(thumbnail(place),body);button.addEventListener('click',()=>selectPlace(place.id));
      const link=element('a','map-card-detail','상세보기 →');link.href=`/place/${encodeURIComponent(place.slug)}`;
      card.append(button,link);list.append(card);
    });
    if(view) view.render(state.places);
    fitButton.disabled=!view||!mapped;
    updateNotice();
  }
  async function search(historyMode='push') {
    clearTimeout(debounce);pending?.abort();pending=new AbortController();const request=++sequence;
    list.setAttribute('aria-busy','true');
    const params=filters.params(state);
    const url=filters.href(location.pathname,state);
    if(historyMode&&url!==location.pathname+location.search)history[historyMode+'State'](null,'',url);
    document.getElementById('mapFindLink').href=filters.href('/find',state);
    document.getElementById('mapRegisterLink').href=filters.registrationHref(state);
    document.getElementById('mapActiveFilters').textContent=filters.labels(state,taxonomy).join(' · ');
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
  categoryNav.addEventListener('click',event=>{const button=event.target.closest('[data-category],[data-pick]');if(!button)return;if(button.dataset.pick)state.pick=state.pick?'':'current';else filters.select(state,'category',button.dataset.category||'');renderDetails();state.q=query.value.trim();search();});
  cuisineNav.addEventListener('click',event=>{const button=event.target.closest('[data-value]');if(!button)return;filters.select(state,'cuisine',button.dataset.value);renderDetails();search();});
  detailNav.addEventListener('click',event=>{const button=event.target.closest('[data-value]');if(!button)return;
    if(state.category==='food'){
      state.cuisine=filters.selectedCuisine(taxonomy,state);
      const allowed=filters.termsFor(taxonomy,'food',state.cuisine).map(t=>t.slug);
      state.terms=state.terms.filter(t=>allowed.includes(t));
    }
    filters.select(state,'terms',button.dataset.value);renderDetails();search();});
  document.getElementById('mapSearch').addEventListener('submit',event=>{event.preventDefault();state.q=query.value.trim();search();});
  query.addEventListener('input',()=>{clearTimeout(debounce);debounce=setTimeout(()=>{state.q=query.value.trim();search('replace');},250);});
  document.getElementById('mapReset').addEventListener('click',()=>{Object.assign(state,filters.defaultState());query.value='';renderDetails();search();});
  addEventListener('popstate',()=>{Object.assign(state,filters.fromParams(new URLSearchParams(location.search)));query.value=state.q;renderDetails();search(false);});
  fitButton.addEventListener('click',()=>view?.fit());
  sheetToggle.addEventListener('click',()=>setListExpanded(!workspace.classList.contains('is-list-expanded')));
  renderDetails();search(false);
  root.JejunoKakao.load(workspace.dataset.kakaoKey).then(maps=>{
    view=createMapView(maps,document.getElementById('kakaoMap'),selectPlace,(place,select)=>makeStatusPin(place,select,document));
    view.render(state.places);fitButton.disabled=!state.places.some(hasCoordinates);updateNotice();
    new ResizeObserver(()=>view.resize()).observe(document.getElementById('kakaoMap'));
    if(state.selected!==null) view.select(state.selected);
  }).catch(error=>{notice.hidden=false;notice.textContent=error.message;});
})(typeof window==='undefined'?globalThis:window);
