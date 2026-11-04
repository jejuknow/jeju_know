/* Presentation only. NEW and verification are supplied by the server. */
(function(root){
  'use strict';
  const escape=value=>String(value).replace(/[&<>'"]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  function entries(place){
    const items=[];
    if(place.is_pick)items.push({label:'JEJU_KNOW PICK',css:'badge-pick'});
    if(place.status_label){
      const tone=['neutral','positive','brand','attention'].includes(place.status_tone)?place.status_tone:'neutral';
      items.push({label:place.status_label,css:'tone-'+tone});
    }
    if(place.is_new)items.push({label:'NEW',css:'badge-new'});
    return items;
  }
  function html(place){
    const items=entries(place);
    const row=items.length?'<div class="place-card-badges">'+items.map(item=>'<span class="user-badge '+item.css+'">'+escape(item.label)+'</span>').join('')+'</div>':'';
    return row+(place.opening_label?'<p class="place-card-opening">'+escape(place.opening_label)+'</p>':'');
  }
  function append(container,place,doc=container.ownerDocument){
    const items=entries(place);
    if(items.length){
      const row=doc.createElement('div');row.className='place-card-badges';
      for(const item of items){const badge=doc.createElement('span');badge.className='user-badge '+item.css;badge.textContent=item.label;row.append(badge);}
      container.append(row);
    }
    if(place.opening_label){const opening=doc.createElement('p');opening.className='place-card-opening';opening.textContent=place.opening_label;container.append(opening);}
  }
  const api={entries,html,append};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.PlaceCardStatus=api;
})(typeof window==='undefined'?globalThis:window);
