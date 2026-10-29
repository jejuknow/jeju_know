const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {createTracker}=require('../static/analytics.js');
test('one page view per document despite visibility/load callbacks',()=>{
  const events=[],t=createTracker({send:e=>events.push(e),id:()=> 'page'});
  t.page();t.page();t.page();assert.equal(events.length,1);assert.equal(events[0].type,'page_view');
});
test('search dedupes double click and multi-select values without leaking keyword',()=>{
  let at=100;const events=[],t=createTracker({send:e=>events.push(e),now:()=>at,id:()=>String(at)});
  const state={region:'구좌',category:'cafe',terms:['cafe-view','cafe-bakery','cafe-view'],companions:['연인','연인'],q:'private search'};
  t.search(state);t.search(state);assert.equal(events.length,1);
  assert.deepEqual(events[0].filters.terms,['cafe-bakery','cafe-view']);assert.deepEqual(events[0].filters.companions,['연인']);
  assert.ok(!JSON.stringify(events).includes('private'));
  at+=2001;t.search(state);assert.equal(events.length,2);
});
test('FIND instrumentation is on successful results only, not on initial/history render',()=>{
  const source=fs.readFileSync(require.resolve('../static/app.js'),'utf8');
  assert.ok(source.indexOf('if(track)window.JejuAnalytics?.search')>source.indexOf('const places=await response.json()'));
  assert.ok(source.includes('await renderResults(false,false,false)'));
  assert.ok(source.includes('buildFilters();renderResults(false,false,false)'));
});
