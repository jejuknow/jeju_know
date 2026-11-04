const test=require('node:test'),assert=require('node:assert/strict');
const status=require('../static/place-card-status.js'),cards=require('../static/home-cards.js');
const verified={status_label:'JEJU_KNOW 확인',status_tone:'brand',name:'장소',slug:'test',category:'카페'};
test('status combinations follow only server decisions; dates never recalculate NEW',()=>{
  for(const [p,labels] of [
    [{...verified,is_new:true},['JEJU_KNOW 확인','NEW']],
    [verified,['JEJU_KNOW 확인']],
    [{is_new:true},['NEW']],
    [{...verified,is_new:false,opening_label:'2025년 8월 오픈'},['JEJU_KNOW 확인']],
    [{...verified,is_new:true,is_pick:true},['JEJU_KNOW PICK','JEJU_KNOW 확인','NEW']],
    [{is_new:false,opened_year:2099,opened_month:1,verification_status:'jejuno_verified'},[]]
  ])assert.deepEqual(status.entries(p).map(i=>i.label),labels);
});
test('opening metadata is separate, kept after NEW expires, and omitted when absent',()=>{
  const html=status.html({...verified,is_new:false,opening_label:'2025년 8월 오픈'});
  assert.match(html,/<\/div><p class="place-card-opening">2025년 8월 오픈<\/p>/);
  assert.doesNotMatch(html,/badge-new/);
  assert.doesNotMatch(status.html(verified),/place-card-opening/);
  assert.equal(status.html({}),'');
});
test('HTML and DOM card renderers preserve identical status decisions and escape text',()=>{
  const p={...verified,is_new:true,is_pick:true,status_tone:'brand" onclick="bad',status_label:'<script>bad</script>',opening_label:'<img onerror="bad">'};
  const doc={createElement:tag=>({tag,children:[],append(node){this.children.push(node)}})};
  const root=doc.createElement('div');status.append(root,p,doc);
  assert.deepEqual(root.children[0].children.map(n=>n.textContent),status.entries(p).map(i=>i.label));
  assert.equal(root.children[1].textContent,p.opening_label);
  const html=status.html(p);assert.doesNotMatch(html,/<script>|<img|onclick=/);assert.match(html,/&lt;script&gt;/);
  for(const render of [cards.placeCard,cards.pickCard]){
    const card=render(p,'조천');assert.ok(card.includes(html));assert.ok(card.indexOf('place-card-badges')<card.indexOf('place-card-opening'));
  }
});
