/* Shared cards for the five-place preview and its complete filtered result dialog. */
(function(root){
  'use strict';
  function escapeHtml(value=''){return String(value).replace(/[&<>'"]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));}
  function imageUrl(value){
    if (/^\/uploads\/(places|issues)\/[0-9a-f]{32}\.webp$/.test(value||'')) return value;
    try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:'';}catch{return '';}
  }
  function visual(place,className){
    const url=imageUrl(place.image_url);
    return `<div class="${className}">${url?`<img src="${escapeHtml(url)}" alt="${escapeHtml(place.name)}" loading="lazy" decoding="async" width="480" height="320">`:`<span aria-hidden="true">${escapeHtml(place.emoji||'📍')}</span>`}${place.is_example?'<span class="badge example card-example">예시</span>':''}</div>`;
  }
  function placeCard(place,region){
    const tags=(place.detail_features||[]).slice(0,3);
    const highlights=tags.length?`<div class="tag-row">${tags.map(tag=>`<span class="tag">${escapeHtml(tag)}</span>`).join('')}</div>`:'';
    const menu=place.signature_menu?`<p class="card-menu">${escapeHtml(place.signature_menu.split('\n')[0])}</p>`:'';
    return `<article class="compact-place-card">${visual(place,'compact-place-visual')}<div class="compact-place-body"><span class="place-provenance">${escapeHtml(place.status_label||'')}</span>${place.is_pick?'<span class="badge pick">JEJU_KNOW PICK</span>':''}${place.is_new?'<span class="badge">NEW</span>':''}${place.opening_label?`<p class="place-opening">${escapeHtml(place.opening_label)}</p>`:''}<p class="compact-place-meta">${escapeHtml(region)} · ${escapeHtml(place.category)}</p><h3>${escapeHtml(place.name)}</h3>${highlights}${menu}<p class="compact-place-reason">${escapeHtml(place.one_line||'')}</p><div class="card-actions"><a class="detail-link" href="/place/${encodeURIComponent(place.slug)}">상세보기 →</a><a class="detail-link" href="/map?place=${encodeURIComponent(place.slug)}">지도에서 보기</a></div></div></article>`;
  }
  function pickCard(place,region){
    const tags=[...new Set([...(place.features_public||place.features||[]),...(place.companions||[])])].slice(0,4);
    return `<article class="pick-card">${visual(place,'pick-visual')}<div class="pick-body"><span class="pick-label">JEJU_KNOW PICK</span><h3>${escapeHtml(place.name)}</h3><p class="pick-meta">${escapeHtml(region)} · ${escapeHtml(place.category)}</p><div class="pick-recommendation"><strong>JEJU_KNOW 추천 이유</strong><p>${escapeHtml(place.pick_note||place.reason||place.one_line||'상세 페이지에서 장소 정보를 확인해보세요.')}</p></div><div class="tag-row">${tags.map(tag=>`<span class="tag">#${escapeHtml(tag)}</span>`).join('')}</div><a class="pick-detail-link" href="/place/${encodeURIComponent(place.slug)}">상세보기 <span aria-hidden="true">→</span></a></div></article>`;
  }
  function searchResults(places,state,filters){
    const all=places.filter(place=>filters.matches(place,state));
    return {all,preview:all.slice(0,5)};
  }
  const api={placeCard,pickCard,searchResults};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.HomeCards=api;
})(typeof window!=='undefined'?window:this);
