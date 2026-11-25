(async()=>{
  const search=window.PlaceSearch, state=search.fromParams(new URLSearchParams(location.search));
  if(document.getElementById('allPlaceGrid').dataset.new==='current')state.new='current';
  const query=search.params(state), grid=document.getElementById('allPlaceGrid'), summary=document.getElementById('listSummary');
  const mapLink=document.getElementById('listMapLink');
  if(mapLink)mapLink.href='/map?'+query;
  const more=document.createElement('button');more.type='button';more.className='secondary-btn';
  more.textContent='더 보기';more.hidden=true;grid.after(more);
  let page=0,shown=0,labels=[];
  async function load(){
    more.disabled=true;
    try{
      const response=await fetch('/api/places?'+query+'&limit=50&page='+(page+1));
      if(!response.ok)throw new Error('검색 실패');
      const places=await response.json(),total=Number(response.headers.get('X-Total-Count')||places.length);
      const html=places.map(p=>window.HomeCards.placeCard(p,p.region_label||search.regionLabel(p.region))).join('');
      if(page===0)grid.innerHTML=html;else grid.insertAdjacentHTML('beforeend',html);
      page++;shown+=places.length;
      summary.textContent=summary.dataset.categoryLabel?`${summary.dataset.categoryLabel} · ${total}곳`:`${labels.length?labels.join(' · ')+' · ':''}총 ${total}곳`;
      more.hidden=shown>=total;more.textContent=`더 보기 (${shown}/${total})`;
      document.getElementById('listEmpty').hidden=total>0;
    }catch{summary.textContent='장소를 불러오지 못했습니다. 다시 시도해 주세요.';more.hidden=false;more.textContent='다시 시도';}
    finally{more.disabled=false;}
  }
  more.addEventListener('click',load);
  try{const config=await fetch('/api/place-taxonomy');if(config.ok)labels=search.labels(state,await config.json());}catch{}
  await load();
})();
