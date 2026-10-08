/* Shared cards for the five-place preview and its complete filtered result dialog. */
(function(root){
  'use strict';
  function escapeHtml(value=''){return String(value).replace(/[&<>'"]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));}
  function imageUrl(value){
    try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:'';}catch{return '';}
  }
  function visual(place,className){
    const url=imageUrl(place.image_url);
    return `<div class="${className}">${url?`<img src="${escapeHtml(url)}" alt="${escapeHtml(place.name)}" loading="lazy" decoding="async">`:`<span aria-hidden="true">${escapeHtml(place.emoji||'📍')}</span>`}${place.is_example?'<span class="badge example card-example">예시</span>':''}</div>`;
  }
  function placeCard(place,region){
    return `<article class="compact-place-card">${visual(place,'compact-place-visual')}<div class="compact-place-body"><p class="compact-place-meta">${escapeHtml(region)} · ${escapeHtml(place.category)}</p><h3>${escapeHtml(place.name)}</h3><p class="compact-place-reason">${escapeHtml(place.one_line||'')}</p><a class="detail-link" href="/place/${encodeURIComponent(place.slug)}">상세보기 →</a></div></article>`;
  }
  function pickCard(place,region){
    const tags=[...new Set([...(place.features_public||place.features||[]),...(place.companions||[])])].slice(0,4);
    return `<article class="pick-card">${visual(place,'pick-visual')}<div class="pick-body"><span class="pick-label">JEJUNO PICK</span><h3>${escapeHtml(place.name)}</h3><p class="pick-meta">${escapeHtml(region)} · ${escapeHtml(place.category)}</p><div class="pick-recommendation"><strong>제주노 추천 이유</strong><p>${escapeHtml(place.reason||place.one_line||'상세 페이지에서 장소 정보를 확인해보세요.')}</p></div><div class="tag-row">${tags.map(tag=>`<span class="tag">#${escapeHtml(tag)}</span>`).join('')}</div><a class="pick-detail-link" href="/place/${encodeURIComponent(place.slug)}">상세보기 <span aria-hidden="true">→</span></a></div></article>`;
  }
  function searchResults(places,state,filters){
    const all=places.filter(place=>filters.matches(place,state));
    return {all,preview:all.slice(0,5)};
  }
  const api={placeCard,pickCard,searchResults};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.HomeCards=api;
})(typeof window!=='undefined'?window:this);
