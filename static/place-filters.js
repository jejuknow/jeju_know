/* Shared taxonomy and compatibility layer. Stored place data and API stay unchanged. */
(function (root) {
  'use strict';
  const option = (id, label, aliases = []) => ({ id, label, aliases });
  const regions = ['제주시내','애월','한림','한경','구좌','조천','성산','표선','남원','서귀포시내','대정','안덕'];
  const categories = ['맛집','카페','가볼 곳'];
  const companions = ['혼자','연인','친구','부모님','아이'];
  const preferences = [
    option('taste','맛 중심',['맛','맛집 추천']),
    option('view','좋은 뷰',['뷰','View','뷰 좋은 곳']),
    option('mood','감성 공간',['감성']),
    option('healing','힐링',['조용한 힐링']),
    option('local','제주다움',['로컬','전통','제주다운 곳']),
    option('popular','인기 핫플',['핫플','트렌디한','트렌디']),
    option('value','가성비',['가성비 좋은 곳']),
    option('space','넓은 공간',['대형','넓고 편안한 곳']),
    option('premium','고급 분위기',['고급','고급스러운 곳']),
    option('unique','이색 경험',['이색'])
  ];
  const menus = {
    '전체': [],
    '카페': ['커피','베이커리','디저트','브런치','차·말차'].map(label => option(label,label,label==='차·말차'?['차','말차']:[])),
    '맛집': ['제주 향토음식','흑돼지','해산물','생선요리','국수','한식','양식','일식'].map(label => option(label,label)),
    '가볼 곳': [
      option('자연·산책','자연·산책',['자연','산책']), option('바다','바다'),
      option('숲·오름','숲·오름',['숲','오름']), option('전시·관람','전시·관람',['전시','관람']),
      option('체험','체험',['동물 체험']), option('아이와 함께','아이와 함께'),
      option('정원·꽃','정원·꽃',['정원','꽃','꽃 명소']), option('사진 명소','사진 명소',['사진','사진 찍기 좋은 곳'])
    ]
  };
  const conditions = [
    option('parking','주차 가능',['주차']), option('pet','반려동물 동반',['반려동물','애견동반']),
    option('child','아이와 이용하기 좋은 곳',['아이 동반']), option('indoor','실내 이용',['실내']),
    option('outdoor','야외 좌석'), option('rain','비 오는 날',['비오는날','비올때']),
    option('solo','혼자 가기 좋은 곳'), option('work','작업·공부하기 좋은 곳',['공부·책','공부','작업']),
    option('takeout','포장·선물 가능',['포장','선물'])
  ];
  const details = [
    { label:'감성 공간', parent:'mood', options:['모던','빈티지','앤티크','한옥/제주 전통','미니멀'].map(v=>option(v,v)) },
    { label:'좋은 뷰', parent:'view', options:['오션뷰','숲뷰','정원뷰','노을뷰','오름뷰'].map(v=>option(v,v)) },
    { label:'제주다움', parent:'local', options:['로컬','전통','제주 식재료','제주 건축/돌담'].map(v=>option(v,v)) },
    { label:'인기 핫플', parent:'popular', options:['신상','트렌디','SNS 인기'].map(v=>option(v,v)) },
    { label:'이색 경험', parent:'unique', options:['음악','전시','체험','독특한 공간','동물 체험'].map(v=>option(v,v)) },
    { label:'그 밖의 취향', options:[option('숨은','숨은 곳',['숨은']),option('사진','사진 찍기 좋은 곳',['사진','사진 명소'])] }
  ];
  const normalize = value => String(value).trim().replace(/\s*추천$/, '').replace(/\s+/g,'').toLowerCase();
  const regionLabel = value => ({'제주시':'제주시내','서귀포시':'서귀포시내'}[value] || value);
  const hasOption = (tags, item) => [item.label,...item.aliases].some(v=>tags.has(normalize(v)));
  const defaultState = () => ({region:'전체',category:'전체',companions:[],preferences:[],menu:[],conditions:[],details:[]});
  function toggle(list, value) { return list.includes(value) ? list.filter(v=>v!==value) : [...list,value]; }
  function select(state, key, value) {
    if (key==='region') state.region=value;
    else if (key==='category') { if (state.category!==value) state.menu=[]; state.category=value; }
    else if (key==='companions') {
      if (value==='전체') state.companions=[];
      else if (value==='혼자') state.companions=state.companions.includes(value)?[]:[value];
      else state.companions=toggle(state.companions.filter(v=>v!=='혼자'),value);
    } else if (['preferences','menu','conditions','details'].includes(key)) {
      if (key==='preferences' && !state.preferences.includes(value) && state.preferences.length===3) return '취향은 최대 3개까지 선택할 수 있어요.';
      state[key]=toggle(state[key],value);
    }
    return '';
  }
  function mapPlace(place) {
    const tags = new Set([...(place.features||[]),...(place.features_public||[])].map(normalize));
    const mappedDetails = details.flatMap(group=>group.options.filter(item=>hasOption(tags,item)).map(item=>item.id));
    const mappedPreferences = preferences.filter(item=>hasOption(tags,item)).map(item=>item.id);
    details.forEach(group=>{ if(group.parent && group.options.some(item=>mappedDetails.includes(item.id))) mappedPreferences.push(group.parent); });
    const mappedConditions = new Set(conditions.filter(item=>hasOption(tags,item)).map(item=>item.id));
    // Explicit availability overrides old tags; unknown/limited does not imply availability.
    [['parking_status','parking'],['pet_status','pet'],['child_status','child']].forEach(([field,id])=>{
      if(place[field]==='가능') mappedConditions.add(id);
      if(['불가','제한'].includes(place[field])) mappedConditions.delete(id);
    });
    if((place.companions||[]).includes('혼자')) mappedConditions.add('solo');
    if((place.companions||[]).includes('아이') && !['불가','제한'].includes(place.child_status)) mappedConditions.add('child');
    if((place.companions||[]).includes('반려동물') && !['불가','제한'].includes(place.pet_status)) mappedConditions.add('pet');
    const menu = (menus[place.category]||[]).filter(item=>hasOption(tags,item)).map(item=>item.id);
    if(place.category==='가볼 곳' && mappedConditions.has('child')) menu.push('아이와 함께');
    return {region:regionLabel(place.region),preferences:new Set(mappedPreferences),menu:new Set(menu),conditions:mappedConditions,details:new Set(mappedDetails)};
  }
  function matches(place, state) {
    const mapped=mapPlace(place);
    const any = (selected, available) => !selected.length || selected.some(v=>available.has(v));
    return (state.region==='전체'||mapped.region===state.region)
      && (state.category==='전체'||place.category===state.category)
      && state.companions.every(v=>(place.companions||[]).includes(v))
      && any(state.preferences,mapped.preferences) && any(state.menu,mapped.menu)
      && any(state.details,mapped.details) && state.conditions.every(v=>mapped.conditions.has(v));
  }
  function selectedLabels(state) {
    const choices={preferences,menu:menus[state.category]||[],conditions,details:details.flatMap(g=>g.options)};
    return [state.region==='전체'?'':state.region,state.category==='전체'?'':state.category,...state.companions,
      ...Object.entries(choices).flatMap(([key,options])=>state[key].map(id=>options.find(o=>o.id===id)?.label||id))].filter(Boolean);
  }
  const api={regions,categories,companions,preferences,menus,conditions,details,regionLabel,defaultState,select,mapPlace,matches,selectedLabels};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  else root.PlaceFilters=api;
})(typeof window!=='undefined'?window:this);
