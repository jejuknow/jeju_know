(async()=>{
  const search=window.PlaceSearch, state=search.fromParams(new URLSearchParams(location.search));
  if(document.getElementById('allPlaceGrid').dataset.new==='current')state.new='current';
  const query=search.params(state), grid=document.getElementById('allPlaceGrid'), summary=document.getElementById('listSummary');
  document.getElementById('listMapLink').href='/map?'+query;
  try{
    const [config,response]=await Promise.all([fetch('/api/place-taxonomy'),fetch('/api/places?'+query)]);
    if(!config.ok||!response.ok)throw new Error('검색 실패');
    const taxonomy=await config.json(), places=await response.json(), labels=search.labels(state,taxonomy);
    summary.textContent=`${labels.length?labels.join(' · ')+' · ':''}총 ${places.length}곳`;
    grid.innerHTML=places.map(p=>window.HomeCards.placeCard(p,search.regionLabel(p.region))).join('');
    document.getElementById('listEmpty').hidden=!!places.length;
  }catch{summary.textContent='장소를 불러오지 못했습니다. 새로고침해 주세요.';}
})();
