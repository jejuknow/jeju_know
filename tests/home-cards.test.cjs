const test=require('node:test');
const assert=require('node:assert/strict');
const cards=require('../static/home-cards.js');
const filters=require('../static/place-filters.js');

const places=Array.from({length:8},(_,i)=>({slug:`place-${i}`,name:`장소 ${i}`,category:i<6?'카페':'맛집',region:'애월',companions:[],features:i<3?['뷰']:[],one_line:'짧은 설명',reason:'긴 추천 이유',is_pick:i===0}));
test('five-place preview and complete results reuse the current filter without dropping matches',()=>{
  const snapshot=JSON.stringify(places);
  const state=filters.defaultState();
  let result=cards.searchResults(places,state,filters);
  assert.equal(result.preview.length,5);assert.equal(result.all.length,8);
  filters.select(state,'category','카페');
  result=cards.searchResults(places,state,filters);
  assert.equal(result.preview.length,5);assert.equal(result.all.length,6);
  filters.select(state,'preferences','view');
  result=cards.searchResults(places,state,filters);
  assert.equal(result.preview.length,3);assert.equal(result.all.length,3);
  filters.select(state,'preferences','view');
  assert.equal(cards.searchResults(places,state,filters).all.length,6);
  assert.equal(JSON.stringify(places),snapshot);
});
test('PICK uses the recommendation reason while ordinary cards stay concise and detail links match',()=>{
  const compact=cards.placeCard(places[0],'애월');
  const pick=cards.pickCard(places[0],'애월');
  assert.ok(compact.includes('짧은 설명'));assert.ok(!compact.includes('긴 추천 이유'));
  assert.ok(pick.includes('제주노 추천 이유'));assert.ok(pick.includes('긴 추천 이유'));
  assert.ok(compact.includes('/place/place-0'));assert.ok(pick.includes('/place/place-0'));
  assert.ok(pick.includes('pick-visual'));assert.ok(compact.includes('compact-place-visual'));
});
test('card content and image attributes are escaped; non-http images are ignored',()=>{
  const hostile={...places[0],name:'<script>alert(1)</script>',reason:'<img onerror=alert(1)>',image_url:'javascript:alert(1)'};
  for(const render of [cards.placeCard,cards.pickCard]){
    const html=render(hostile,'<b>지역</b>');
    assert.ok(!html.includes('<script>'));assert.ok(!html.includes('src="javascript:'));
    assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('&lt;b&gt;'));
  }
  assert.ok(cards.pickCard({...places[0],image_url:'https://example.com/photo.jpg'},'애월').includes('<img src="https://example.com/photo.jpg"'));
});
