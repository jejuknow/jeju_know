const search=window.PlaceSearch, cards=window.HomeCards;
const state=search.fromParams(new URLSearchParams(location.search));
let taxonomy, requestId=0, pending;
const $=id=>document.getElementById(id);
// Links must preserve the URL even while the initial API requests are pending.
$('finderMapLink').href=search.href('/map',state);
$('finderRegisterLink').href=search.registrationHref(state);
function buildFilters(){
  search.chips($('regionChips'),[{value:'',label:'전체'},...taxonomy.regions.map(v=>({value:v,label:taxonomy.region_labels?.[v]||search.regionLabel(v)}))],state.region);
  search.chips($('categoryChips'),[{value:'',label:'전체'},...taxonomy.categories.map(v=>({value:v.slug,label:v.name}))],state.category);
  search.chips($('companionChips'),[{value:'',label:'상관없음'},...taxonomy.companions.map(v=>({value:v,label:taxonomy.companion_labels?.[v]||v}))],state.companions);
  const cuisine=search.selectedCuisine(taxonomy,state),terms=search.termsFor(taxonomy,state.category,cuisine);
  $('categoryDetails').hidden=state.category!=='food'&&!terms.length;
  $('foodCuisineGroup').hidden=state.category!=='food';
  search.chips($('foodCuisineChips'),[{value:'',label:'전체'},...search.cuisinesFor(taxonomy).map(v=>({value:v.slug,label:v.name}))],cuisine);
  const more=$('categoryFeatureDetails');
  more.hidden=!terms.length;
  if(more.dataset.category!==state.category){more.open=state.category!=='cafe'||!matchMedia('(max-width: 640px)').matches;more.dataset.category=state.category;}
  $('categoryFeatureSummary').hidden=false;
  $('categoryFeatureSummary').textContent=state.category==='food'?'세부유형 (선택)':'카페 특징 더보기';
  $('categoryDetailTitle').textContent=state.category==='food'?'어떤 음식을 찾나요?':'어떤 카페를 찾나요?';
  const selected=$('selectedCafeFeatures');
  selected.hidden=state.category!=='cafe'||!state.terms.length;
  search.chips(selected,terms.filter(v=>state.terms.includes(v.slug)).map(v=>({value:v.slug,label:v.name+' ×'})),state.terms);
  $('categoryDetailCount').textContent=state.terms.length?`${state.terms.length}개 선택`:'';
  search.chips($('categoryDetailChips'),terms.map(v=>({value:v.slug,label:v.name})),state.terms);
}
async function renderResults(scroll=false,updateUrl=true){
  pending?.abort();pending=new AbortController();const request=++requestId;
  const query=search.params(state), labels=search.labels(state,taxonomy);
  const url=search.href(location.pathname,state)+location.hash;
  if(updateUrl&&url!==location.pathname+location.search+location.hash)history.pushState(null,'',url);
  $('finderMapLink').href=search.href('/map',state);
  $('finderRegisterLink').href=search.registrationHref(state);
  $('placeGrid').setAttribute('aria-busy','true');$('allResultsBtn').hidden=true;
  try{
    const response=await fetch('/api/places?'+query,{signal:pending.signal,headers:{Accept:'application/json'}});
    if(!response.ok)throw new Error('장소 검색 실패');
    const places=await response.json();if(request!==requestId)return;
    $('placeGrid').innerHTML=places.slice(0,5).map(p=>cards.placeCard(p,(p.region_label||search.regionLabel(p.region)))).join('');
    $('placeGrid').hidden=!places.length;$('emptyState').hidden=!!places.length;
    $('allResultsBtn').hidden=places.length<5;$('allResultsBtn').href='/places?'+query;
    $('allResultsBtn').textContent=`전체 결과 ${places.length}곳 보기 →`;
    $('resultSummary').textContent=labels.length?`${labels.join(' · ')} 조건에 맞는 장소 ${places.length}곳을 찾았어요.`:`현재 공개된 장소 ${places.length}곳을 보여드리고 있어요.`;
    if(scroll)$('places').scrollIntoView({behavior:'smooth',block:'start'});
  }catch(error){if(error.name!=='AbortError'&&request===requestId){$('placeGrid').replaceChildren();$('emptyState').hidden=true;$('resultSummary').textContent='장소를 불러오지 못했습니다. 검색 버튼을 다시 눌러 주세요.';}}
  finally{if(request===requestId)$('placeGrid').setAttribute('aria-busy','false');}
}
document.querySelector('.compact-finder').addEventListener('click',event=>{
  const button=event.target.closest('.chip');if(!button||!taxonomy)return;
  if(state.category==='food'&&button.closest('[data-filter]').dataset.filter==='terms'){
    state.cuisine=search.selectedCuisine(taxonomy,state);
    const allowed=search.termsFor(taxonomy,'food',state.cuisine).map(t=>t.slug);
    state.terms=state.terms.filter(t=>allowed.includes(t));
  }
  search.select(state,button.closest('[data-filter]').dataset.filter,button.dataset.value);buildFilters();
});
// Keep keywords received from MAP/shared URLs; FIND only edits the three filter groups.
$('findBtn').addEventListener('click',()=>renderResults(true));
addEventListener('popstate',()=>{if(taxonomy){Object.assign(state,search.fromParams(new URLSearchParams(location.search)));buildFilters();renderResults(false,false);}});
$('resetBtn').addEventListener('click',()=>{if(!taxonomy)return;Object.assign(state,search.defaultState());buildFilters();renderResults();});
document.querySelectorAll('[data-collection]').forEach(link=>link.addEventListener('click',event=>{
  if(!taxonomy)return;event.preventDefault();Object.assign(state,search.defaultState());
  if(link.dataset.collection==='parents')state.companions=['부모님'];
  if(link.dataset.collection==='soloCafe'){state.category='cafe';state.companions=['혼자'];}
  if(link.dataset.collection==='rain')state.condition='rain';
  buildFilters();renderResults(true);
}));
async function boot(){
  try{
    const [config,places]=await Promise.all([fetch('/api/place-taxonomy'),fetch('/api/places?pick=current')]);
    if(!config.ok||!places.ok)throw new Error('데이터 로딩 실패');
    taxonomy=await config.json();const all=await places.json();
    const picks=all.filter(p=>p.is_pick).slice(0,3);
    if($('pickGrid'))$('pickGrid').innerHTML=picks.length?picks.map(p=>cards.pickCard(p,(p.region_label||search.regionLabel(p.region)))).join(''):'<p>아직 공개된 JEJU_KNOW PICK이 없습니다.</p>';
    buildFilters();await renderResults(false,false);$('findBtn').disabled=false;
  }catch{$('resultSummary').textContent='장소 데이터를 불러오지 못했습니다. 새로고침해 주세요.';}
}
boot();
