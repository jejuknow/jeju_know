let places = [];
const state = { region:'전체', category:'전체', withWhom:'전체', feature:'전체' };
const placeGrid = document.getElementById('placeGrid');
const pickGrid = document.getElementById('pickGrid');
const emptyState = document.getElementById('emptyState');
const resultSummary = document.getElementById('resultSummary');

const defaults = {
  regions:['제주시','애월','한림','한경','구좌','조천','성산','표선','남원','서귀포시','대정','안덕'],
  categories:['맛집','카페','가볼 곳'],
  companions:['혼자','연인','친구','부모님','아이'],
  features:['주차','감성','뷰','비오는날','사진','힐링','로컬','전통','가성비']
};

function uniq(list){ return [...new Set(list.filter(Boolean))]; }
function chip(label, value, selected=false){return `<button class="chip ${selected?'is-selected':''}" data-value="${escapeHtml(value)}">${escapeHtml(label)}</button>`;}
function escapeHtml(value=''){return String(value).replace(/[&<>'"]/g, ch=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));}

function buildFilters(){
  const regions=uniq([...defaults.regions,...places.map(p=>p.region)]);
  const categories=uniq([...defaults.categories,...places.map(p=>p.category)]);
  const companions=uniq([...defaults.companions,...places.flatMap(p=>p.companions||[])]);
  const features=uniq([...defaults.features,...places.flatMap(p=>p.features_public||p.features||[])]);
  document.getElementById('regionChips').innerHTML=chip('전체','전체',true)+regions.map(v=>chip(v,v)).join('');
  document.getElementById('categoryChips').innerHTML=chip('전체','전체',true)+categories.map(v=>chip(v,v)).join('');
  document.getElementById('companionChips').innerHTML=chip('상관없음','전체',true)+companions.map(v=>chip(v,v)).join('');
  document.getElementById('featureChips').innerHTML=features.map(v=>chip(v,v)).join('');
  bindChipEvents();
}

function cardMarkup(place){
  const featureList=place.features_public||place.features||[];
  const tags=[...(place.companions||[]).slice(0,2),...featureList.slice(0,2)].slice(0,4);
  const visual=place.image_url
    ? `<div class="place-thumb has-image" style="background-image:url('${escapeHtml(place.image_url)}')"></div>`
    : `<div class="place-thumb">${escapeHtml(place.emoji||'📍')}</div>`;
  return `<article class="place-card">${visual}<div class="place-body"><div class="meta-row"><span class="badge primary">${escapeHtml(place.region)}</span><span class="badge">${escapeHtml(place.category)}</span>${place.is_pick?'<span class="badge pick">JEJUNO PICK</span>':''}${place.is_example?'<span class="badge example">예시</span>':''}</div><h3>${escapeHtml(place.name)}</h3><p class="location">📍 ${escapeHtml(place.region)}</p><p class="one-line">${escapeHtml(place.one_line||'')}</p><div class="tag-row">${tags.map(t=>`<span class="tag">#${escapeHtml(t)}</span>`).join('')}</div><div class="card-bottom"><span class="recommend">제주노 추천 기록</span><a class="detail-link" href="/place/${encodeURIComponent(place.slug)}">상세보기 →</a></div></div></article>`;
}

function renderPick(){
  const picks=places.filter(p=>p.is_pick).slice(0,3);
  pickGrid.innerHTML=picks.length?picks.map(cardMarkup).join(''):'<div class="empty-state"><p>아직 공개된 JEJUNO PICK이 없습니다.</p></div>';
}
function filteredPlaces(){return places.filter(p=>{
  const regionOk=state.region==='전체'||p.region===state.region;
  const categoryOk=state.category==='전체'||p.category===state.category;
  const withOk=state.withWhom==='전체'||(p.companions||[]).includes(state.withWhom);
  const features=p.features_public||p.features||[];
  const featureOk=state.feature==='전체'||features.includes(state.feature);
  return regionOk&&categoryOk&&withOk&&featureOk;
});}
function renderResults(scroll=false){
  const results=filteredPlaces();
  placeGrid.innerHTML=results.map(cardMarkup).join('');
  emptyState.hidden=results.length!==0;
  placeGrid.hidden=results.length===0;
  const selected=Object.values(state).filter(v=>v!=='전체');
  resultSummary.textContent=selected.length?`${selected.join(' · ')} 조건에 맞는 장소 ${results.length}곳을 찾았어요.`:`현재 공개된 장소 ${results.length}곳을 보여드리고 있어요.`;
  if(scroll) document.getElementById('places').scrollIntoView({behavior:'smooth',block:'start'});
}
function setGroupSelection(group,value){if(!group)return;group.querySelectorAll('.chip').forEach(btn=>btn.classList.toggle('is-selected',btn.dataset.value===value));}
function bindChipEvents(){
  document.querySelectorAll('.chips[data-filter]').forEach(group=>{group.addEventListener('click',e=>{const btn=e.target.closest('.chip');if(!btn)return;const key=group.dataset.filter;if(key==='feature'){const was=btn.classList.contains('is-selected');group.querySelectorAll('.chip').forEach(b=>b.classList.remove('is-selected'));if(was)state.feature='전체';else{btn.classList.add('is-selected');state.feature=btn.dataset.value;}return;}state[key]=btn.dataset.value;setGroupSelection(group,btn.dataset.value);});});
}

document.getElementById('findBtn')?.addEventListener('click',()=>renderResults(true));
document.getElementById('resetBtn')?.addEventListener('click',()=>{state.region=state.category=state.withWhom=state.feature='전체';document.querySelectorAll('.chips[data-filter]').forEach(group=>{if(group.dataset.filter==='feature')group.querySelectorAll('.chip').forEach(b=>b.classList.remove('is-selected'));else setGroupSelection(group,'전체');});renderResults();});
document.querySelectorAll('.quick-card').forEach(btn=>btn.addEventListener('click',()=>{const value=btn.dataset.quick;if(['카페','맛집'].includes(value)){state.category=value;setGroupSelection(document.querySelector('.chips[data-filter="category"]'),value);}else if(value==='부모님'){state.withWhom='부모님';setGroupSelection(document.querySelector('.chips[data-filter="withWhom"]'),'부모님');}else{state.feature=value;const group=document.querySelector('.chips[data-filter="feature"]');group?.querySelectorAll('.chip').forEach(b=>b.classList.toggle('is-selected',b.dataset.value===value));}renderResults(true);}));
document.querySelectorAll('[data-collection]').forEach(link=>link.addEventListener('click',e=>{const type=e.currentTarget.dataset.collection;if(type==='parents')state.withWhom='부모님';if(type==='soloCafe'){state.withWhom='혼자';state.category='카페';}if(type==='rain')state.feature='비오는날';setGroupSelection(document.querySelector('.chips[data-filter="withWhom"]'),state.withWhom);setGroupSelection(document.querySelector('.chips[data-filter="category"]'),state.category);const featureGroup=document.querySelector('.chips[data-filter="feature"]');featureGroup?.querySelectorAll('.chip').forEach(b=>b.classList.toggle('is-selected',b.dataset.value===state.feature));setTimeout(()=>renderResults(true),120);}));
document.getElementById('navToggle')?.addEventListener('click',()=>document.querySelector('.nav')?.classList.toggle('is-open'));

async function boot(){
  try{
    const response=await fetch('/api/places',{headers:{'Accept':'application/json'}});
    if(!response.ok) throw new Error(`HTTP ${response.status}`);
    places=await response.json();
    buildFilters();renderPick();renderResults();
  }catch(error){
    console.error(error);
    resultSummary.textContent='장소 데이터를 불러오지 못했습니다.';
    emptyState.hidden=false;placeGrid.hidden=true;
  }
}
boot();
