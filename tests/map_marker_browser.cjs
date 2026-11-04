/* Read-only browser QA. Local fixture data covers the provenance combinations;
   production runs never create places, change visibility, or write analytics. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),path=require('node:path');
const {placeStatus,legendStatuses,hasCoordinates}=require('../static/map.js');
const base=process.env.MAP_TEST_URL||'http://127.0.0.1:8937',local=base.includes('127.0.0.1');
const sdk=`window.JejunoKakao={load:async()=>({Map:class{constructor(el){this.el=el}setCenter(p){this.center=p}getCenter(){return this.center}setLevel(){}getLevel(){return 5}panTo(){}setBounds(){}relayout(){}},LatLng:class{},LatLngBounds:class{extend(){}},CustomOverlay:class{constructor(o){this.content=o.content;this.setMap(o.map)}setMap(m){if(m)m.el.append(this.content);else this.content.remove()}setPosition(){}setZIndex(){}}})}`;
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 try{
  const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await ctx.route('**/api/analytics/events',r=>r.fulfill({status:204}));
  if(local)await ctx.route('**/static/kakao-loader.js*',r=>r.fulfill({contentType:'application/javascript',body:sdk}));
  const places=await(await ctx.request.get(base+'/api/places')).json();
  assert.ok(places.every(p=>p.published&&!p.submitted_by_user_id));
  if(local)assert.ok(!places.some(p=>p.slug==='marker-private'));
  const p=await ctx.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));p.setDefaultTimeout(12000);
  async function ready(){await p.locator('#mapResultsList[aria-busy=false]').waitFor({state:'attached'})}
  await p.goto(base+'/map');await ready();await p.locator('#mapFit:not([disabled])').waitFor({state:'attached'});
  assert.equal(await p.locator('.map-pin').count(),places.filter(hasCoordinates).length);
  for(const key of new Set(places.filter(hasCoordinates).map(x=>placeStatus(x).key))){
   assert.equal(await p.locator('.map-pin[data-marker-type='+key+']').count(),places.filter(x=>hasCoordinates(x)&&placeStatus(x).key===key).length);
  }
  for(const place of places){
   const badges=await p.locator('[data-place-id="'+place.id+'"] .place-card-badges .user-badge').allTextContents();
   const state=placeStatus(place);
   assert.deepEqual(badges,[...(state.key==='default'?[]:[state.label]),...(place.is_new?['NEW']:[])]);
   if(place.source_type!=='user')assert.ok(!badges.includes('사용자 등록'));
   assert.ok(!JSON.stringify(place).includes('JEJU_KNOW 등록'));
  }
  const summary=p.locator('#mapLegend summary'),legend=p.locator('#mapLegendItems');
  assert.equal(await summary.getAttribute('aria-label'),'마커 안내');
  assert.equal(await summary.getAttribute('title'),'마커 안내');
  assert.equal(await summary.locator('.map-mobile').textContent(),'ⓘ');
  for(const [width,height]of [[320,568],[360,740],[375,812],[390,844],[430,932]]){
   await p.setViewportSize({width,height});
   const map=await p.locator('#kakaoMap').boundingBox();
   if(!await p.locator('#mapLegend').evaluate(n=>n.open))await summary.click();
   assert.deepEqual(await legend.locator('li>span:last-child').allTextContents(),legendStatuses(places).map(s=>s.label));
   const box=await legend.boundingBox(),button=await summary.boundingBox(),sheet=await p.locator('.map-results').boundingBox();
   assert.equal(button.width,44);assert.equal(button.height,44);
   assert.ok(box.x>=0&&box.x+box.width<=width);
   assert.ok(box.y>=map.y&&box.y+box.height<=Math.min(map.y+map.height,sheet.y)+1);
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),width);
   await summary.click();assert.equal(await legend.isVisible(),false);
   await summary.click();await p.mouse.click(map.x+map.width/2,map.y+map.height-15);assert.equal(await legend.isVisible(),false);
   assert.equal((await p.locator('#kakaoMap').boundingBox()).height,map.height);
   console.log('legend',width,'bounds/dismiss/44px/unchanged map OK');
  }
  await p.setViewportSize({width:390,height:844});
  // Inspect each available semantic state through its list selection and popup.
  const samples=local?places.filter(x=>x.slug.startsWith('marker-')):
   [...new Set(places.map(x=>placeStatus(x).key))].map(k=>places.find(x=>placeStatus(x).key===k));
  for(const place of samples){
   if(await p.locator('#mapWorkspace').getAttribute('data-list-level')==='0')await p.locator('#mapSheetToggle').click();
   await p.locator('[data-place-id="'+place.id+'"] .map-place-select').click();
   const panel=p.locator('#mapSelected');await panel.waitFor();
   const state=placeStatus(place);
   assert.deepEqual(await panel.locator('.user-badge').allTextContents(),[...(state.key==='default'?[]:[state.label]),...(place.is_new?['NEW']:[])]);
   assert.equal(await panel.locator('.detail-link').first().getAttribute('href'),'/place/'+encodeURIComponent(place.slug));
   const detail=await ctx.request.get(base+'/place/'+encodeURIComponent(place.slug));assert.equal(detail.status(),200);
   const detailHtml=await detail.text();
   assert.ok(!detailHtml.includes('JEJU_KNOW 등록'));
   if(place.source_type==='user')assert.ok(detailHtml.includes('<dt>등록</dt><dd>사용자 등록</dd>'));
   await panel.locator('.map-close').click();
  }
  if(local){
   // Defensive API fallback for a legacy record with no source; no DB mutation.
   const legacy={...places.find(x=>x.slug==='marker-legacy')};delete legacy.source_type;
   await ctx.route('**/api/places?**',r=>r.fulfill({contentType:'application/json',body:JSON.stringify([legacy])}));
   await p.locator('#mapSearch button').click();await ready();
   assert.equal(await p.locator('.map-pin').getAttribute('data-marker-type'),'default');
   assert.equal(await p.locator('.map-place-card .user-badge').count(),0);
   await summary.click();assert.deepEqual(await legend.locator('li>span:last-child').allTextContents(),['사용자 등록','JEJU_KNOW 확인','JEJU_KNOW PICK']);await summary.click();
   await ctx.unroute('**/api/places?**');await p.locator('#mapSearch button').click();await ready();
  }
  for(const width of [1280,1440,1920]){
   await p.setViewportSize({width,height:900});
   assert.equal(await p.locator('#mapLegend').evaluate(n=>n.open),true);
   assert.equal(await summary.locator('.map-wide').isVisible(),true);
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),width);
  }
  await p.setViewportSize({width:390,height:844});await summary.click();
  if(process.env.MAP_EVIDENCE_DIR)await p.screenshot({path:path.join(process.env.MAP_EVIDENCE_DIR,'public-marker-legend-'+(local?'local':'live')+'.png')});
  assert.deepEqual(errors,[]);
  console.log('PASS marker/card/popup/legend priority, provenance, privacy, desktop; page errors none');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
