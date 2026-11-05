const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nearby = require('../static/nearby.js');

test('ISSUE radius filtering retains schedule order and restores all cards when disabled', () => {
  // Server order differs deliberately from distance order. No live GPS or Kakao request.
  const cards = [0.007, 0.001, 0.018, null].map((lng, i) => ({
    id: i, dataset: {lat: lng === null ? '' : '0', lng: lng === null ? '' : String(lng)},
    distance: {}, querySelector() { return this.distance; }
  }));
  const nodes = Object.fromEntries(['[data-near-empty]', '.issue-empty', '[data-results-title]', 'h2 span', '[data-map-count]'].map(key => [key, {}]));
  let ordered = [...cards], changed;
  const state = {active:false, point:{latitude:0,longitude:0}, radius:1};
  const results = {
    querySelectorAll: () => cards, querySelector: key => nodes[key],
    insertBefore(card) { ordered = ordered.filter(c => c !== card).concat(card); }
  };
  const root = {dataset:{view:'list'}, addEventListener(){}, querySelectorAll:()=>[],
    querySelector:key => key === '#issueResults' ? results : {value:''}};
  const context = {document:{querySelector:()=>root, getElementById:()=>({})},
    window:{JejuNearby:{...nearby, controls:(_, callback) => {changed=callback;return state;}}},
    matchMedia:()=>({matches:true,addEventListener(){}}), addEventListener(){}, URL, Map, Set};
  vm.runInNewContext(fs.readFileSync(require.resolve('../static/issue-discovery.js'), 'utf8'), context);
  state.active = true;
  changed('radius');
  assert.deepEqual(ordered.filter(c => !c.hidden).map(c => c.id), [0,1]);
  assert.equal(nodes['[data-map-count]'].textContent, '1km · 일정 임박순');
  assert.equal(cards[0].distance.hidden, false);
  assert.ok(cards[0].distance.textContent.endsWith('m'));
  state.radius = 3;
  changed('radius');
  assert.deepEqual(ordered.filter(c => !c.hidden).map(c => c.id), [0,1,2]);
  state.active = false;
  changed('toggle');
  assert.deepEqual(ordered.map(c => c.id), [0,1,2,3]);
  assert.ok(cards.every(c => !c.hidden && c.distance.hidden));
  assert.equal(nodes['[data-map-count]'].textContent, '지도 위치 3곳');
});
