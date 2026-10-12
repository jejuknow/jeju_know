/* Shared selection, URL and chip behavior. Labels come from the DB. */
(function(root){
  'use strict';
  const defaultState=()=>({category:'',region:'',companions:[],terms:[],q:'',condition:'',pick:'',new:''});
  const toggle=(list,value)=>list.includes(value)?list.filter(v=>v!==value):[...list,value];
  const regionLabel=v=>({'제주시':'제주시내','서귀포시':'서귀포시내'}[v]||v);
  function select(state,key,value){
    if(key==='category'){if(state.category!==value)state.terms=[];state.category=value;}
    else if(key==='companions'){
      if(!value)state.companions=[];
      else if(value==='혼자')state.companions=state.companions.includes(value)?[]:[value];
      else state.companions=toggle(state.companions.filter(v=>v!=='혼자'),value);
    }else if(key==='terms')state.terms=value?toggle(state.terms,value):[];
    else state[key]=value;
  }
  function params(state){
    const query=new URLSearchParams();
    for(const [key,value] of Object.entries({category:state.category,region:state.region,companion:state.companions.join(','),detail:state.terms.join(','),q:state.q,condition:state.condition,pick:state.pick,new:state.new}))if(value)query.set(key,value);
    return query;
  }
  function fromParams(query){
    const state=defaultState();
    for(const key of ['category','region','q','condition','pick','new'])state[key]=query.get(key)||'';
    state.companions=(query.get('companion')||'').split(',').filter(Boolean);
    state.terms=(query.get('detail')||'').split(',').filter(Boolean);
    return state;
  }
  function termsFor(taxonomy,category){
    const parent=taxonomy.categories.find(c=>c.slug===category);
    return parent?taxonomy.terms.filter(t=>t.parent_id===parent.id):[];
  }
  function labels(state,taxonomy){
    return [state.pick==='current'?'이달의 JEJUNO PICK':'',state.new==='current'?'신상':'',regionLabel(state.region),taxonomy.categories.find(c=>c.slug===state.category)?.name||state.category,...state.companions,
      ...state.terms.map(slug=>taxonomy.terms.find(t=>t.slug===slug)?.name||slug),state.q,state.condition==='rain'?'비 오는 날':state.condition].filter(Boolean);
  }
  function chips(container,options,selected,css='chip'){
    container.replaceChildren();
    options.forEach(({value,label})=>{
      const button=document.createElement('button');button.type='button';button.className=css;button.dataset.value=value;button.textContent=label;
      const active=Array.isArray(selected)?(value?selected.includes(value):!selected.length):value===selected;
      button.classList.toggle('is-selected',active);button.setAttribute('aria-pressed',String(active));container.append(button);
    });
  }
  const api={defaultState,select,params,fromParams,termsFor,labels,chips,regionLabel};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.PlaceSearch=api;
})(typeof window==='undefined'?globalThis:window);
