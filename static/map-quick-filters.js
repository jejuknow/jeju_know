/* A mobile view of PlaceSearch state, using the same DB taxonomy as the sheet. */
(function(root){
  'use strict';
  const filters = typeof module !== 'undefined' && module.exports ? require('./place-search.js') : root.PlaceSearch;
  function model(state,taxonomy){
    const cuisine=filters.selectedCuisine(taxonomy,state);
    const major=filters.cuisinesFor(taxonomy).find(t=>t.slug===cuisine);
    const option=(key,value,label,selected,back=false)=>({key,value,label,selected,back});
    let options=[];
    if(state.category==='food' && !major){
      options=[option('cuisine','','전체 음식',!cuisine&&!state.terms.length),
        ...filters.cuisinesFor(taxonomy).map(t=>option('cuisine',t.slug,t.name,t.slug===cuisine))];
    }else{
      const terms=filters.termsFor(taxonomy,state.category,cuisine);
      if(major || terms.length){
        if(major) options.push(option('cuisine','','← '+major.name,false,true));
        options.push(option('terms','',state.category==='cafe'?'전체 카페':'전체',!state.terms.length));
        options.push(...terms.map(t=>option('terms',t.slug,t.name,state.terms.includes(t.slug))));
      }
    }
    return {level:state.category+':'+(major?.slug||''),options};
  }
  function mount(nav,onSelect){
    let level;
    nav.addEventListener('click',event=>{
      const button=event.target.closest('button[data-key]');
      if(button) onSelect(button.dataset.key,button.dataset.value);
    });
    return (state,taxonomy)=>{
      const next=model(state,taxonomy), changed=level!==next.level;
      const active=nav.contains(document.activeElement)?document.activeElement:null;
      const left=nav.scrollLeft;
      nav.replaceChildren();
      for(const item of next.options){
        const button=document.createElement('button');button.type='button';
        button.className='map-category'+(item.back?' map-quick-back':'')+(item.selected?' is-selected':'');
        button.dataset.key=item.key;button.dataset.value=item.value;button.textContent=item.label;
        if(item.back) button.setAttribute('aria-label',item.label.slice(2)+'에서 음식 대분류로 돌아가기');
        else button.setAttribute('aria-pressed',String(item.selected));
        nav.append(button);
      }
      nav.hidden=!next.options.length;
      if(active){
        const target=changed?nav.firstElementChild:Array.from(nav.children).find(b=>b.dataset.key===active.dataset.key&&b.dataset.value===active.dataset.value);
        target?.focus({preventScroll:true});
      }
      nav.scrollLeft=changed?0:left;
      level=next.level;
    };
  }
  const api={model,mount};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.MapQuickFilters=api;
})(typeof window==='undefined'?globalThis:window);
