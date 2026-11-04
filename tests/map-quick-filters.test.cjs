const test=require('node:test');
const assert=require('node:assert/strict');
const filters=require('../static/place-search.js');
const {model}=require('../static/map-quick-filters.js');
const taxonomy={categories:[{id:1,slug:'food'},{id:2,slug:'cafe'},{id:3,slug:'attraction'}],terms:[
  {id:10,parent_id:1,slug:'second',name:'관리자가 정한 첫 분류'},
  {id:11,parent_id:1,slug:'first',name:'다음 분류'},
  {id:12,parent_id:1,slug:'retired',name:'과거 분류',is_legacy:1},
  {id:20,parent_id:10,slug:'dish',name:'새 세부유형'},
  {id:21,parent_id:10,slug:'old-dish',name:'숨긴 세부유형',is_legacy:1},
  {id:30,parent_id:2,slug:'feature-b',name:'새 카페 특징'},
  {id:31,parent_id:2,slug:'feature-a',name:'다른 카페 특징'}]};
test('quick choices preserve DB ordering and names; empty categories add no fake taxonomy',()=>{
  const state=filters.defaultState();
  assert.deepEqual(model(state,taxonomy).options,[]);
  state.pick='current';assert.deepEqual(model(state,taxonomy).options,[]);
  filters.select(state,'category','attraction');assert.deepEqual(model(state,taxonomy).options,[]);
  filters.select(state,'category','food');
  assert.deepEqual(model(state,taxonomy).options.map(x=>x.label),['전체 음식','관리자가 정한 첫 분류','다음 분류']);
  const withAttraction={...taxonomy,terms:[...taxonomy.terms,{id:40,parent_id:3,slug:'walk',name:'산책'}]};
  filters.select(state,'category','attraction');
  assert.deepEqual(model(state,withAttraction).options.map(x=>x.label),['전체','산책']);
});
test('food drill-down, multi-selection, back and legacy URL use the existing filter state',()=>{
  const state=filters.fromParams(new URLSearchParams('category=food&detail=dish'));
  const before=JSON.stringify(state),quick=model(state,taxonomy);
  assert.equal(JSON.stringify(state),before,'rendering must not rewrite incoming URL state');
  assert.deepEqual(quick.options.map(x=>x.label),['← 관리자가 정한 첫 분류','전체','새 세부유형']);
  assert.equal(quick.options[2].selected,true);
  const back=quick.options[0];filters.select(state,back.key,back.value);
  assert.equal(state.category,'food');assert.deepEqual(state.terms,[]);
  assert.equal(model(state,taxonomy).options[0].label,'전체 음식');
});
test('cafe multi-select and category changes stay in sync with URL/sheet selections',()=>{
  const state=filters.fromParams(new URLSearchParams('category=cafe&detail=feature-b,feature-a&region=애월'));
  assert.deepEqual(model(state,taxonomy).options.filter(x=>x.selected).map(x=>x.value),['feature-b','feature-a']);
  filters.select(state,'terms','feature-b');
  assert.equal(model(state,taxonomy).options[1].selected,false);
  assert.equal(filters.params(state).get('detail'),'feature-a');
  filters.select(state,'category','food');filters.select(state,'cuisine','second');filters.select(state,'terms','dish');
  filters.select(state,'category','cafe');
  assert.equal(state.cuisine,'');assert.deepEqual(state.terms,[]);assert.equal(state.region,'애월');
  assert.equal(model(state,taxonomy).options[0].selected,true);
});
