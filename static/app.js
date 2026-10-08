let places = [];
let placeCategories = [];
const filters = window.PlaceFilters;
const state = filters.defaultState();
const cards = window.HomeCards;
let currentResults = [];
const resultsDialog = document.getElementById('allResultsDialog');
const placeGrid = document.getElementById('placeGrid');
const pickGrid = document.getElementById('pickGrid');
const emptyState = document.getElementById('emptyState');
const resultSummary = document.getElementById('resultSummary');

function escapeHtml(value=''){return String(value).replace(/[&<>'"]/g, ch=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));}
function chip(label,value,selected=false){return `<button type="button" class="chip ${selected?'is-selected':''}" data-value="${escapeHtml(value)}" aria-pressed="${selected}">${escapeHtml(label)}</button>`;}
function buildFilters(){
  document.getElementById('regionChips').innerHTML=chip('전체','전체',true)+filters.regions.map(v=>chip(v,v)).join('');
  document.getElementById('categoryChips').innerHTML=chip('전체','전체',true)+placeCategories.map(v=>chip(v.name,v.name)).join('');
  document.getElementById('companionChips').innerHTML=chip('상관없음','전체',true)+filters.companions.map(v=>chip(v,v)).join('');
  document.getElementById('preferenceChips').innerHTML=filters.preferences.map(v=>chip(v.label,v.id)).join('');
  document.getElementById('conditionChips').innerHTML=filters.conditions.map(v=>chip(v.label,v.id)).join('');
  document.getElementById('detailChips').innerHTML=filters.details.map(group=>`<fieldset class="filter-subgroup"><legend>${escapeHtml(group.label)}</legend><div class="chips" data-filter="details">${group.options.map(v=>chip(v.label,v.id)).join('')}</div></fieldset>`).join('');
  renderMenu();syncSelections();
}
function renderMenu(){
  const options=filters.menus[state.category]||[];
  document.getElementById('menuGroup').hidden=!options.length;
  document.getElementById('menuHint').hidden=!!options.length;
  document.getElementById('menuLabel').textContent=state.category==='가볼 곳'?'활동·장소 유형':'메뉴';
  document.getElementById('menuChips').innerHTML=options.map(v=>chip(v.label,v.id)).join('');
}
function syncSelections(){
  document.querySelectorAll('.chips[data-filter]').forEach(group=>{
    const value=state[group.dataset.filter];
    group.querySelectorAll('.chip').forEach(btn=>{
      const selected=Array.isArray(value)?(value.includes(btn.dataset.value)||(btn.dataset.value==='전체'&&!value.length)):value===btn.dataset.value;
      btn.classList.toggle('is-selected',selected);
      btn.setAttribute('aria-pressed',String(selected));
    });
  });
  document.getElementById('preferenceCount').textContent=`${state.preferences.length}/3 선택`;
  const count=state.menu.length+state.conditions.length+state.details.length;
  document.getElementById('detailCount').textContent=count?`${count}개 선택`:'';
}
function cardMarkup(place){return cards.placeCard(place,filters.regionLabel(place.region));}
function renderPick(){
  const picks=places.filter(p=>p.is_pick).slice(0,3);
  pickGrid.innerHTML=picks.length?picks.map(place=>cards.pickCard(place,filters.regionLabel(place.region))).join(''):'<div class="empty-state"><p>아직 공개된 JEJUNO PICK이 없습니다.</p></div>';
}
function renderResults(scroll=false){
  const {all:results,preview}=cards.searchResults(places,state,filters);
  currentResults=results;
  placeGrid.innerHTML=preview.map(cardMarkup).join('');
  const allButton=document.getElementById('allResultsBtn');
  allButton.hidden=results.length<=5;
  allButton.textContent=`전체 결과 ${results.length}곳 보기 →`;
  document.getElementById('allResultsGrid').replaceChildren();
  if(resultsDialog.open)resultsDialog.close();
  emptyState.hidden=results.length!==0;
  placeGrid.hidden=results.length===0;
  const selected=filters.selectedLabels(state);
  resultSummary.textContent=selected.length?`${selected.join(' · ')} 조건에 맞는 장소 ${results.length}곳을 찾았어요.`:`현재 공개된 장소 ${results.length}곳을 보여드리고 있어요.`;
  if(scroll) document.getElementById('places').scrollIntoView({behavior:'smooth',block:'start'});
}
document.querySelector('.compact-finder')?.addEventListener('click',e=>{
  const btn=e.target.closest('.chip');
  if(!btn)return;
  const key=btn.closest('[data-filter]').dataset.filter;
  const previousCategory=state.category;
  document.getElementById('filterFeedback').textContent=filters.select(state,key,btn.dataset.value);
  if(key==='category' && previousCategory!==state.category)renderMenu();
  syncSelections();
});
document.getElementById('findBtn')?.addEventListener('click',()=>renderResults(true));
function resetFilters(){
  Object.assign(state,filters.defaultState());
  document.getElementById('filterFeedback').textContent='';
  document.getElementById('moreFilters').open=false;
  document.querySelector('.taste-details').open=false;
  renderMenu();syncSelections();
}
document.getElementById('resetBtn')?.addEventListener('click',()=>{resetFilters();renderResults();});
document.querySelectorAll('[data-collection]').forEach(link=>link.addEventListener('click',e=>{
  e.preventDefault();resetFilters();
  const type=e.currentTarget.dataset.collection;
  if(type==='parents')state.companions=['부모님'];
  if(type==='soloCafe'){state.companions=['혼자'];state.category=placeCategories.find(c=>c.slug==='cafe')?.name||'카페';}
  if(type==='rain')state.conditions=['rain'];
  renderMenu();syncSelections();renderResults(true);
}));
document.getElementById('allResultsBtn')?.addEventListener('click',()=>{
  document.getElementById('allResultsGrid').innerHTML=currentResults.map(cardMarkup).join('');
  document.getElementById('allResultsSummary').textContent=resultSummary.textContent;
  resultsDialog.showModal();
});
resultsDialog?.addEventListener('click',event=>{if(event.target===resultsDialog)resultsDialog.close();});
async function boot(){
  try{
    const [response,categoryResponse]=await Promise.all([
      fetch('/api/places',{headers:{'Accept':'application/json'}}),
      fetch('/api/categories',{headers:{'Accept':'application/json'}})
    ]);
    if(!response.ok||!categoryResponse.ok) throw new Error('장소 데이터를 불러오지 못했습니다.');
    places=await response.json();
    placeCategories=await categoryResponse.json();
    const menuKinds={food:'맛집',cafe:'카페',attraction:'가볼 곳'};
    placeCategories.forEach(category=>{if(menuKinds[category.slug])filters.menus[category.name]=filters.menus[menuKinds[category.slug]];});
    buildFilters();renderPick();renderResults();
    document.getElementById('findBtn').disabled=false;
  }catch(error){
    console.error(error);
    resultSummary.textContent='장소 데이터를 불러오지 못했습니다. 새로고침해 주세요.';
    emptyState.hidden=false;placeGrid.hidden=true;
  }
}
boot();
