const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nearby = require('../static/nearby.js');

// Exercise the actual page controller with a deterministic Kakao/GPS boundary.
// No production records, browser permissions, or network requests are involved.
function node(textContent = '') {
  return {textContent, children: [], attrs: {}, dataset: {}, style: {setProperty(k,v){this[k]=v;}},
    classList: {toggle(){}}, append(n){this.children.push(n);}, focus(){},
    setAttribute(k,v){this.attrs[k]=v;}, getAttribute(k){return this.attrs[k];},
    removeAttribute(k){delete this.attrs[k];}, addEventListener(){},
    replaceChildren(...children){this.children=children;}};
}
function card(id, kind, text, color, lng, sponsor=false) {
  const c=node(); c.dataset={issueId:id,lat:'0',lng:String(lng),markerText:text,markerColor:color};
  const fields={'[data-category]':node(kind), h3:node(id), '[data-dates]':node('11.27 - 11.29'),
    '[data-badge]':node('진행중'), '[data-venue]':node('제주 공연장'), '[data-distance]':node(),
    a:{href:'https://example.test/news/'+id}};
  if(sponsor)fields['[data-disclosure]']=node('광고·협찬');
  c.querySelector=k=>fields[k] || null;
  return c;
}
function page(group,cards) {
  const fields=Object.fromEntries(['[data-near-empty]','.issue-empty','[data-results-title]','h2 span','[data-map-count]'].map(k=>[k,node()]));
  const results=node();results.children=cards;
  results.querySelectorAll=()=>results.children;
  results.querySelector=k=>fields[k];
  results.insertBefore=()=>{};
  const controls=node(), legend=node(group), highlights=node(group);
  const filters=node();filters.querySelector=()=>controls;
  const panel=node();panel.setAttribute('aria-label',group+' 지도');
  const canvas=node();canvas.setAttribute('aria-label',group+' 위치 지도');canvas.clientWidth=500;
  results.setAttribute('aria-label',group+' 목록');
  const nodes={'#issueResults':results,'.issue-filters':filters,'#eventHighlights':highlights,
    '.nearby-controls':controls,'#issueMapLegend':legend,'#issueMapPanel':panel,'#issueMap':canvas,
    'input[name="view"]':{value:'map'}};
  const root={dataset:{view:'map',group,kakaoKey:'test'},querySelector:k=>nodes[k],querySelectorAll:()=>[],
    events:{},addEventListener(k,fn){this.events[k]=fn;}};
  return {root,nodes,fields,results,controls,filters,legend,highlights,canvas};
}
test('GPS tab changes replace cards, markers, count, legend, popup and back state without leaking coordinates', async()=>{
  const events=[card('festival','행사·축제','행사','#3759c7',.01),card('concert','공연','공연','#bf3062',.02)];
  const benefits=[card('offer','혜택·할인','혜택','#b65308',.01),card('sponsor','혜택·할인','혜택','#b65308',.02,true),card('far-offer','혜택·할인','혜택','#b65308',.2)];
  const current=page('event',events), notices=node(), overlays=[], requests=[], pushes=[];
  const state={point:{latitude:0,longitude:0},radius:5,active:true};
  const url={href:'https://example.test/news?view=map',assign(){assert.fail('Unexpected full-page fallback');}};
  const transitions={};let changed;
  class Overlay {constructor(options){Object.assign(this,options);overlays.push(this);}setMap(map){this.map=map;}setZIndex(){}}
  class FakeMap {setCenter(){}setLevel(){}setBounds(){}panTo(){}relayout(){}getCenter(){return {};}}
  const sdk={Map:FakeMap,LatLng:class{},LatLngBounds:class{extend(){}},CustomOverlay:Overlay,event:{addListener(){}}};
  function documentFor(group) {
    const p=page(group,group==='event'?events:benefits);
    p.controls.replaceWith=control=>{assert.equal(control,current.controls);p.filters.querySelector=()=>control;};
    current.nodes['.issue-filters'].replaceWith=n=>{current.nodes['.issue-filters']=n;};
    current.nodes['#issueMapLegend'].replaceWith=n=>{current.nodes['#issueMapLegend']=n;};
    p.highlights.children=[node(group+' closing')];
    return {querySelector:k=>k==='.issue-discovery'?p.root:p.nodes[k]};
  }
  const context={document:{querySelector:()=>current.root,getElementById:id=>id==='issueMap'?current.canvas:notices,createElement:()=>node()},
    window:{JejuNearby:{...nearby,controls:(_,fn)=>{changed=fn;return state;},locationLayer:()=>({mark(){},frame(){}})},JejunoKakao:{load:async()=>sdk}},
    matchMedia:()=>({matches:false,addEventListener(){}}),ResizeObserver:class{observe(){}},
    location:url,history:{pushState(_,__,u){url.href=u;pushes.push(u);},replaceState(){}},
    fetch:async u=>{requests.push(u);return {ok:true,text:async()=>new URL(u).searchParams.get('group')==='benefit'?'benefit':'event'};},
    DOMParser:class{parseFromString(group){return documentFor(group);}},
    addEventListener(k,fn){transitions[k]=fn;},URL,Map,Set,AbortController};
  vm.runInNewContext(fs.readFileSync(require.resolve('../static/issue-discovery.js'),'utf8'),context);
  const settle=()=>new Promise(resolve=>setImmediate(resolve));
  await settle();
  assert.equal(current.fields['h2 span'].textContent,'2건');
  assert.deepEqual(overlays.map(o=>o.content.textContent),['행사','공연']);
  const old=overlays.slice();
  current.root.events.click({target:{closest:()=>({href:'https://example.test/news?group=benefit&view=map&period=weekend'})},button:0,preventDefault(){}});
  await settle();
  assert.equal(current.root.dataset.group,'benefit');
  assert.equal(current.nodes['#issueMapLegend'].textContent,'benefit');
  assert.equal(current.results.querySelector('[data-results-title]').textContent,'내 주변 혜택·할인');
  assert.equal(current.results.querySelector('h2 span').textContent,'2건');
  assert.equal(current.highlights.children[0].textContent,'benefit closing');
  assert.ok(old.every(o=>o.map===null));
  assert.equal(benefits[2].hidden,true);
  const pins=overlays.filter(o=>o.content.className==='issue-pin' && o.map);
  assert.equal(pins.length,2);
  assert.ok(pins.every(o=>o.content.textContent==='혜택' && o.content.style['--issue-marker']==='#b65308'));
  pins[0].content.onclick();
  let popup=overlays.at(-1).content;
  assert.ok(!popup.children.some(n=>n.textContent==='광고·협찬'));
  pins[1].content.onclick();
  popup=overlays.at(-1).content;
  assert.ok(popup.children.some(n=>n.textContent==='광고·협찬'));
  assert.ok(popup.children.some(n=>n.textContent==='제주 공연장'));
  assert.ok(requests.concat(pushes).every(u=>!/[?&](latitude|longitude|lat|lng|radius)=/.test(u)));
  assert.equal(state.radius,5);
  url.href='https://example.test/news?view=map';
  transitions.popstate();await settle();
  assert.equal(current.root.dataset.group,'event');
  assert.equal(current.nodes['#issueMapLegend'].textContent,'event');
  assert.equal(current.results.querySelector('[data-results-title]').textContent,'내 주변 EVENT');
  state.active=false;changed('toggle');
  assert.equal(current.results.querySelector('[data-results-title]').textContent,'전체 EVENT');
});
