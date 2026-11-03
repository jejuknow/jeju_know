const test = require('node:test');
const assert = require('node:assert/strict');
const {distanceKm, within, formatDistance, recommendations, controls} = require('../static/nearby.js');

test('distances are geodesic, inclusive and ordered without changing the input list', () => {
  const point = {latitude: 0, longitude: 0};
  const places = [0.075, 0.036, 0.018, 0.004].map(longitude => ({latitude:0, longitude}));
  places.push({latitude:null, longitude:0}, {latitude:91, longitude:0});
  assert.ok(Math.abs(distanceKm(point, {latitude:0, longitude:1}) - 111.195) < 0.001);
  for (const [i, radius] of [1, 3, 5, 10].entries()) {
    const results = within(places, point, radius);
    assert.equal(results.length, i+1);
    assert.deepEqual(results.map(p=>p.distance), results.map(p=>p.distance).sort((a,b)=>a-b));
  }
  assert.equal(within([places[0]], point, distanceKm(point, places[0])).length, 1);
  assert.equal(places[0].distance, undefined);
  assert.equal(formatDistance(.45), '450m');
  assert.equal(formatDistance(1.19), '1.2km');
});

test('recommendations consider only current PICKs and verified places within the filtered radius', () => {
  const items = [
    {id:1}, {id:2, verification_status:'jejuno_verified'}, {id:3, is_pick:1},
    {id:4, is_pick:true, verification_status:'jejuno_verified'}, {id:5, is_pick:0}
  ];
  assert.deepEqual(recommendations(items).map(p=>p.id), [3,4,2]);
  assert.deepEqual(recommendations([{verification_status:'community_confirmed'}, {is_pick:0}]), []);
});

test('GPS is requested only by a click; errors discard stale fixes and allow a retry', () => {
  const locate = {}, toggle = {setAttribute(){}}, radius = {value:'5'}, message = {};
  const nodes = {'[data-locate]':locate, '[data-near-toggle]':toggle, '[data-radius]':radius, '[data-location-message]':message};
  let calls = 0, provider, success, failure;
  const state = controls({querySelector:s=>nodes[s]}, ()=>{}, ()=>{calls++;return provider;});
  assert.equal(calls, 0);
  locate.onclick();
  assert.match(message.textContent, /현재 브라우저/);
  provider = {getCurrentPosition(ok, fail) {success=ok; failure=fail;}};
  locate.onclick(); locate.onclick();
  assert.equal(calls, 2);
  success({coords:{latitude:33, longitude:126}});
  toggle.onclick();
  assert.equal(state.active,true);
  locate.onclick(); failure({code:3});
  assert.match(message.textContent,/다시 시도/);
  assert.equal(state.point,null);
  assert.equal(state.active,false);
  assert.equal(toggle.disabled,true);
  locate.onclick(); failure({code:1});
  assert.match(message.textContent,/위치 권한/);
  assert.equal(locate.disabled,false);
});
