const {test}=require('node:test');
const assert=require('node:assert/strict');
const F=require('../static/place-filters.js');
const place=(overrides={})=>({region:'제주시',category:'카페',companions:['부모님','아이'],features:['View 추천','감성','커피'],parking_status:'가능',...overrides});

test('companions support groups but alone and any are exclusive',()=>{
  const s=F.defaultState();
  F.select(s,'companions','부모님');F.select(s,'companions','아이');
  assert.deepEqual(s.companions,['부모님','아이']);
  F.select(s,'companions','혼자');assert.deepEqual(s.companions,['혼자']);
  F.select(s,'companions','친구');assert.deepEqual(s.companions,['친구']);
  F.select(s,'companions','친구');assert.deepEqual(s.companions,[]);
  F.select(s,'companions','아이');F.select(s,'companions','전체');assert.deepEqual(s.companions,[]);
});
test('fourth preference is rejected without silently deleting any choice',()=>{
  const s=F.defaultState();['view','mood','local'].forEach(v=>F.select(s,'preferences',v));
  assert.match(F.select(s,'preferences','space'),/최대 3개/);
  assert.deepEqual(s.preferences,['view','mood','local']);
  F.select(s,'preferences','view');F.select(s,'preferences','space');
  assert.deepEqual(s.preferences,['mood','local','space']);
});
test('category is single select and clears only incompatible menu selections',()=>{
  const s=F.defaultState();F.select(s,'category','카페');F.select(s,'menu','커피');F.select(s,'conditions','parking');
  F.select(s,'category','카페');assert.deepEqual(s.menu,['커피']);
  F.select(s,'category','맛집');assert.deepEqual(s.menu,[]);assert.deepEqual(s.conditions,['parking']);
  F.select(s,'category','전체');assert.equal(s.category,'전체');assert.deepEqual(F.menus['전체'],[]);
});
test('legacy tags map without mutating source; ambiguous animal is not a pet claim',()=>{
  const p=place({features:['View 추천','전통 추천','신상 추천','숨은 추천','사진 추천','공부·책 추천','비올때 추천','꽃 명소 추천','동물 추천','선물 추천'],category:'가볼 곳'});
  const original=JSON.stringify(p);const m=F.mapPlace(p);
  ['view','local','popular'].forEach(v=>assert.ok(m.preferences.has(v)));
  ['work','rain','takeout'].forEach(v=>assert.ok(m.conditions.has(v)));
  assert.ok(m.menu.has('정원·꽃'));assert.ok(m.menu.has('사진 명소'));assert.ok(m.details.has('숨은'));
  assert.ok(!m.conditions.has('pet'));assert.equal(JSON.stringify(p),original);
});
test('explicit unavailable/limited statuses win over legacy positive tags',()=>{
  const m=F.mapPlace(place({features:['주차','반려동물','아이 동반'],parking_status:'불가',pet_status:'제한',child_status:'불가'}));
  ['parking','pet','child'].forEach(v=>assert.ok(!m.conditions.has(v)));
  assert.ok(!F.mapPlace(place({features:[],companions:[],parking_status:'미확인'})).conditions.has('parking'));
});
test('search combines all companions/conditions with any preference/menu',()=>{
  const s=F.defaultState();Object.assign(s,{region:'제주시내',category:'카페',companions:['부모님','아이'],preferences:['view','premium'],menu:['커피','디저트'],conditions:['parking']});
  assert.ok(F.matches(place(),s));
  assert.ok(!F.matches(place({companions:['부모님']}),s));
  assert.ok(!F.matches(place({parking_status:'불가'}),s));
  assert.ok(!F.matches(place({category:'맛집'}),s));
  assert.ok(!F.matches(place({region:'애월'}),s));
  assert.ok(!F.matches(place({features:['감성']}),s));
});
test('detailed view tags imply parent taste and restaurant menus never leak into cafes',()=>{
  const s=F.defaultState();s.preferences=['view'];
  assert.ok(F.matches(place({features:['오션뷰']}),s));
  assert.ok(!F.mapPlace(place({features:['흑돼지']})).menu.has('흑돼지'));
  assert.ok(F.mapPlace(place({category:'맛집',features:['흑돼지']})).menu.has('흑돼지'));
  assert.equal(F.regionLabel('서귀포시'),'서귀포시내');
});
test('new filter state is isolated and empty filters show every public place',()=>{
  const a=F.defaultState(),b=F.defaultState();F.select(a,'preferences','view');
  assert.deepEqual(b.preferences,[]);assert.ok(F.matches(place({features:[]}),b));
  a.category='카페';a.menu=['커피'];a.conditions=['rain'];
  assert.deepEqual(F.selectedLabels(a),['카페','좋은 뷰','커피','비 오는 날']);
});
