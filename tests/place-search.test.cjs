const test=require('node:test'), assert=require('node:assert/strict');
const filters=require('../static/place-search.js');
test('monthly PICK and NEW survive URLs without masquerading as a category',()=>{
  const state={...filters.defaultState(),pick:'current',new:'current'};
  assert.deepEqual(filters.fromParams(filters.params(state)),state);
  assert.equal(state.category,'');
  assert.deepEqual(filters.labels(state,{categories:[],terms:[]}),['이달의 JEJU_KNOW PICK','신상']);
});
test('base selection and optional multiple details toggle and reset on category switch',()=>{
  const state=filters.defaultState();assert.deepEqual(state.terms,[]);
  filters.select(state,'category','food');
  filters.select(state,'terms','food-korean');filters.select(state,'terms','food-western');
  assert.deepEqual(state.terms,['food-korean','food-western']);
  filters.select(state,'terms','food-korean');assert.deepEqual(state.terms,['food-western']);
  filters.select(state,'category','cafe');assert.deepEqual(state.terms,[]);
  filters.select(state,'terms','cafe-mood');filters.select(state,'terms','');assert.deepEqual(state.terms,[]);
});
test('companion exclusivity and AND selection survive a shared URL',()=>{
  const state=filters.defaultState();filters.select(state,'companions','부모님');filters.select(state,'companions','아이');
  assert.deepEqual(state.companions,['부모님','아이']);filters.select(state,'companions','혼자');assert.deepEqual(state.companions,['혼자']);
  filters.select(state,'companions','친구');assert.deepEqual(state.companions,['친구']);
  filters.select(state,'category','cafe');filters.select(state,'terms','cafe-view');state.region='제주시';
  assert.deepEqual(filters.fromParams(filters.params(state)),state);
  filters.select(state,'companions','');assert.deepEqual(state.companions,[]);
});
test('DB names drive detail choices and preserve IDs through renaming',()=>{
  const data={categories:[{id:1,slug:'cafe',name:'커피집'}],terms:[{parent_id:1,slug:'cafe-mood',name:'분위기'}]};
  assert.equal(filters.termsFor(data,'cafe').length,1);assert.equal(filters.termsFor(data,'attraction').length,0);
  const state={...filters.defaultState(),category:'cafe',terms:['cafe-mood']};assert.deepEqual(filters.labels(state,data),['커피집','분위기']);
});

test('FIND MAP and registration links retain every active filter and NEW context',()=>{
  const state={cuisine:'',focus:[],category:'food',region:'애월',companions:['부모님','아이'],terms:['food-western'],q:'바다 & 숲',condition:'rain',pick:'current',new:'current'};
  for(const path of ['/find','/map']){
    const url=new URL(filters.href(path,state),'https://example.test');
    assert.equal(url.pathname,path);assert.deepEqual(filters.fromParams(url.searchParams),state);
  }
  const register=new URL(filters.registrationHref(state),'https://example.test');
  assert.equal(register.pathname,'/map/register');assert.equal(register.searchParams.get('source'),'new');
  assert.deepEqual(filters.fromParams(register.searchParams),state);
  assert.equal(new URL(filters.registrationHref(filters.defaultState()),'https://example.test').searchParams.get('source'),'map');
});


test('orientation URL round trips separately from the ten cafe features',()=>{
 const state={...filters.defaultState(),category:'cafe',focus:['taste','space'],terms:['cafe-mood']};
 assert.deepEqual(filters.fromParams(filters.params(state)),state);
 filters.select(state,'category','food');assert.deepEqual(state.focus,[]);assert.deepEqual(state.terms,[]);
});
test('popup uses linked restaurant terms, shared cafe taxonomy and original companions',()=>{
 const taxonomy={cafe_focus:[{value:'taste',label:'맛 중심'},{value:'space',label:'공간 중심'},{value:'experience',label:'이색 경험'}]};
 const base={region:'서귀포시',companions:['연인','친구','부모님','아이']};
 const cafe=filters.discoveryAttributes({...base,category:'카페',category_slug:'cafe',cafe_focus:['taste','space'],classification_terms:[{type:'cafe_feature',name:'좋은 뷰'}]},taxonomy);
 assert.equal(cafe.region,'서귀포시내');assert.equal(cafe.what,'카페 · 좋은 뷰');assert.equal(cafe.companions,'연인 · 친구 · 부모님');assert.equal(cafe.moreCompanions,1);
 assert.equal(filters.discoveryAttributes({category:'카페',category_slug:'cafe'},taxonomy).what,'카페');
 assert.equal(filters.discoveryAttributes({category:'카페',category_slug:'cafe',classification_terms:[{type:'cafe_feature',name:'이색 경험'}]},taxonomy).what,'카페 · 이색 경험');
 assert.equal(filters.discoveryAttributes({...base,category:'맛집',category_slug:'food',classification_terms:[{type:'restaurant_subcategory',name:'베트남식'}]},taxonomy).what,'맛집 · 베트남식');
});

const foodTree={categories:[{id:1,slug:'food',name:'맛집'}],terms:[{id:2,parent_id:1,slug:'food-korean',name:'한식'},{id:3,parent_id:1,slug:'food-japanese',name:'일식'},{id:4,parent_id:2,slug:'food-noodles',name:'국수·면'},{id:5,parent_id:3,slug:'food-ramen',name:'라멘'},{id:6,parent_id:1,slug:'food-asian',name:'기타 아시아',is_legacy:1}]};
test('restaurant choices are hierarchical, legacy URLs infer parent and filters round trip',()=>{
 assert.deepEqual(filters.cuisinesFor(foodTree).map(t=>t.name),['한식','일식']);
 assert.deepEqual(filters.termsFor(foodTree,'food'),[]);
 assert.deepEqual(filters.termsFor(foodTree,'food','food-korean').map(t=>t.name),['국수·면']);
 const state=filters.fromParams(new URLSearchParams('category=food&detail=food-ramen'));
 assert.equal(filters.selectedCuisine(foodTree,state),'food-japanese');
 assert.equal(state.cuisine,''); // Compatibility display never silently changes an old query.
 filters.select(state,'cuisine','food-korean');assert.deepEqual(state.terms,[]);
 filters.select(state,'terms','food-noodles');
 assert.deepEqual(filters.fromParams(filters.params(state)),state);
 assert.deepEqual(filters.labels(state,foodTree),['맛집','한식','국수·면']);
 assert.equal(new URL(filters.registrationHref(state),'https://example.test').searchParams.get('cuisine'),'food-korean');
 filters.select(state,'category','cafe');assert.equal(state.cuisine,'');assert.deepEqual(state.terms,[]);
});
