const test=require('node:test'), assert=require('node:assert/strict');
const filters=require('../static/place-search.js');
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
