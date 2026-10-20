/* Shared selection, URL and chip behavior. Labels come from the DB. */
(function(root){
  'use strict';
  const defaultState=()=>({category:'',cuisine:'',region:'',companions:[],terms:[],focus:[],q:'',condition:'',pick:'',new:''});
  const toggle=(list,value)=>list.includes(value)?list.filter(v=>v!==value):[...list,value];
  const regionLabel=v=>({'제주시':'제주시내','서귀포시':'서귀포시내'}[v]||v);
  function select(state,key,value){
    if(key==='category'){if(state.category!==value){state.cuisine='';state.terms=[];state.focus=[];}state.category=value;}
    else if(key==='cuisine'){state.cuisine=value;state.terms=[];}
    else if(key==='companions'){
      if(!value)state.companions=[];
      else if(value==='혼자')state.companions=state.companions.includes(value)?[]:[value];
      else state.companions=toggle(state.companions.filter(v=>v!=='혼자'),value);
    }else if(key==='terms'||key==='focus')state[key]=value?toggle(state[key]||[],value):[];
    else state[key]=value;
  }
  function params(state){
    const query=new URLSearchParams();
    for(const [key,value] of Object.entries({category:state.category,cuisine:state.cuisine,region:state.region,companion:state.companions.join(','),detail:state.terms.join(','),focus:(state.focus||[]).join(','),q:state.q,condition:state.condition,pick:state.pick,new:state.new}))if(value)query.set(key,value);
    return query;
  }
  function fromParams(query){
    const state=defaultState();
    for(const key of ['category','cuisine','region','q','condition','pick','new'])state[key]=query.get(key)||'';
    state.companions=(query.get('companion')||'').split(',').filter(Boolean);
    state.terms=(query.get('detail')||'').split(',').filter(Boolean);
    state.focus=(query.get('focus')||'').split(',').filter(Boolean);
    return state;
  }
  function href(path,state){const query=params(state);return path+(query.size?'?'+query:'');}
  function registrationHref(state,source='map'){
    const query=params(state);query.set('source',source==='new'||state.new==='current'?'new':'map');
    return '/map/register?'+query;
  }
  function cuisinesFor(taxonomy){
    const food=taxonomy.categories.find(c=>c.slug==='food');
    return food?taxonomy.terms.filter(t=>t.parent_id===food.id&&!t.is_legacy):[];
  }
  function selectedCuisine(taxonomy,state){
    if(state.cuisine)return state.cuisine;
    // Display the parent of old detail-only URLs without rewriting their meaning.
    const majors=cuisinesFor(taxonomy),parents=state.terms.map(slug=>{
      const term=taxonomy.terms.find(t=>t.slug===slug);
      return majors.find(m=>m.id===term?.id||m.id===term?.parent_id)?.slug;
    });
    return parents.length&&parents.every(v=>v&&v===parents[0])?parents[0]:'';
  }
  function termsFor(taxonomy,category,cuisine=''){
    if(category==='food'){
      const main=cuisinesFor(taxonomy).find(t=>t.slug===cuisine);
      return main?taxonomy.terms.filter(t=>t.parent_id===main.id&&!t.is_legacy):[];
    }
    const parent=taxonomy.categories.find(c=>c.slug===category);
    return parent?taxonomy.terms.filter(t=>t.parent_id===parent.id):[];
  }
  function labels(state,taxonomy){
    return [state.pick==='current'?'이달의 JEJU_KNOW PICK':'',state.new==='current'?'신상':'',(taxonomy.region_labels?.[state.region]||regionLabel(state.region)),taxonomy.categories.find(c=>c.slug===state.category)?.name||state.category,...state.companions.map(v=>taxonomy.companion_labels?.[v]||v),
      taxonomy.terms.find(t=>t.slug===state.cuisine)?.name||state.cuisine,...(state.focus||[]).map(value=>(taxonomy.cafe_focus||[]).find(t=>t.value===value)?.label||value),...state.terms.map(slug=>taxonomy.terms.find(t=>t.slug===slug)?.name||slug),state.q,state.condition==='rain'?'비 오는 날':state.condition].filter(Boolean);
  }
  function discoveryAttributes(place,taxonomy){
    const details=place.category_slug==='food'?(place.classification_terms||[]).filter(t=>t.type==='restaurant_subcategory').map(t=>t.name):
      place.category_slug==='cafe'?(place.classification_terms||[]).filter(t=>t.type==='cafe_feature').map(t=>t.name):[];
    const companions=Array.isArray(place.companions)?place.companions:[];
    return {region:taxonomy.region_labels?.[place.region]||regionLabel(place.region)||'미확인',what:[place.category,...details].filter(Boolean).join(' · '),
      companions:companions.slice(0,3).map(v=>taxonomy.companion_labels?.[v]||v).join(' · ')||'미확인',moreCompanions:Math.max(0,companions.length-3)};
  }
  function chips(container,options,selected,css='chip'){
    container.replaceChildren();
    options.forEach(({value,label})=>{
      const button=document.createElement('button');button.type='button';button.className=css;button.dataset.value=value;button.textContent=label;
      const active=Array.isArray(selected)?(value?selected.includes(value):!selected.length):value===selected;
      button.classList.toggle('is-selected',active);button.setAttribute('aria-pressed',String(active));container.append(button);
    });
  }
  const api={defaultState,select,params,fromParams,termsFor,cuisinesFor,selectedCuisine,labels,chips,regionLabel,href,registrationHref,discoveryAttributes};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.PlaceSearch=api;
})(typeof window==='undefined'?globalThis:window);
