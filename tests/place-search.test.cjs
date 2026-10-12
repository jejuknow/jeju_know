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
  const state={category:'food',region:'애월',companions:['부모님','아이'],terms:['food-western'],q:'바다 & 숲',condition:'rain',pick:'current',new:'current'};
  for(const path of ['/find','/map']){
    const url=new URL(filters.href(path,state),'https://example.test');
    assert.equal(url.pathname,path);assert.deepEqual(filters.fromParams(url.searchParams),state);
  }
  const register=new URL(filters.registrationHref(state),'https://example.test');
  assert.equal(register.pathname,'/map/register');assert.equal(register.searchParams.get('source'),'new');
  assert.deepEqual(filters.fromParams(register.searchParams),state);
  assert.equal(new URL(filters.registrationHref(filters.defaultState()),'https://example.test').searchParams.get('source'),'map');
});
