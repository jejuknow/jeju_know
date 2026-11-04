/* Run with PLAYWRIGHT_MODULE pointing to the installed Playwright package.
   MAP_TEST_URL defaults to the isolated fixture. No data is written.
   Real-device Safari / Instagram checks are deliberately not claimed here. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const geo = require('../static/nearby.js');
const base = process.env.MAP_TEST_URL || 'http://127.0.0.1:8934';
const local = base.includes('127.0.0.1');
const sdk = `window.JejunoKakao={load:async()=>({Map:class{constructor(el){this.el=el;window.mapInstances=(window.mapInstances||0)+1}setCenter(p){this.center=p}getCenter(){return this.center}setLevel(n){this.level=n}getLevel(){return this.level||5}panTo(p){this.setCenter(p)}setBounds(){}relayout(){window.relayouts=(window.relayouts||0)+1}},LatLng:class{constructor(lat,lng){this.lat=lat;this.lng=lng}},LatLngBounds:class{extend(){}},CustomOverlay:class{constructor(o){this.content=o.content;this.setMap(o.map)}setMap(m){if(m)m.el.appendChild(this.content);else this.content.remove()}setPosition(){}setZIndex(){}},event:{addListener(){}}})}`;
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 try {
  const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,permissions:['geolocation'],geolocation:{latitude:33.41,longitude:126.5}});
  await ctx.route('**/api/analytics/events',r=>r.fulfill({status:204}));
  if(local)await ctx.route('**/static/kakao-loader.js*',r=>r.fulfill({contentType:'application/javascript',body:sdk}));
  const p=await ctx.newPage();const errors=[];p.on('pageerror',e=>errors.push(e.message));p.setDefaultTimeout(12000);
  async function ready(){await p.locator('#mapResultsList[aria-busy=false]').waitFor({state:'attached'})}
  async function open(){await p.locator('#mapOpenFilters').click();await p.locator('#mapFilterSheet[open]').waitFor()}
  async function apply(){await ready();await p.locator('#mapFilterApply:not([disabled])').click();assert.equal(await p.locator('#mapFilterSheet[open]').count(),0)}
  async function filterClick(group,text){await p.locator(group).getByRole('button',{name:text,exact:true}).click();await ready()}
  async function verifyApi(){
    await ready();
    const params=new URL(p.url()).search;
    const rows=await (await ctx.request.get(base+'/api/places'+params)).json();
    assert.equal(await p.locator('#mapResultsList article').count(),rows.length);
    assert.match(await p.locator('#mapFilterApply').textContent(),new RegExp('^'+rows.length+'곳 보기$'));
  }
  await p.goto(base+'/map',{waitUntil:'domcontentloaded'});await ready();
  await p.locator('#mapFit:not([disabled])').waitFor({state:'attached'});
  const beforeMap=await p.locator('#kakaoMap').elementHandle();
  for(const [w,h] of [[320,568],[360,740],[375,812],[390,844],[430,932]]){
    await p.setViewportSize({width:w,height:h});
    const rect=await p.locator('#kakaoMap').boundingBox();
    const layout=await p.evaluate(()=>({width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight}));
    assert.equal(layout.width,w);
    assert.ok(rect.height>=h*.5-1,JSON.stringify({w,h,rect}));
    assert.ok(rect.y+rect.height<=h+1,'map must be actually visible');
    assert.ok(layout.height<=h+1,'initial page must fit');
    assert.equal(await p.locator('#mapResultsList').isVisible(),false);
    console.log('mobile',w,h,'map',rect.height,'px',Math.round(rect.height/h*100)+'%');
  }
  const taxonomy=await(await ctx.request.get(base+'/api/place-taxonomy')).json();
  const filters=require('../static/place-search.js');
  const majors=filters.cuisinesFor(taxonomy),korean=majors.find(t=>t.name==='한식');
  const dishes=filters.termsFor(taxonomy,'food',korean.slug),meal=dishes.find(t=>t.name==='백반·정식');
  const cafes=filters.termsFor(taxonomy,'cafe');
  const quick=p.locator('#mapQuickFilters');
  let requests=0;
  p.on('request',r=>{if(new URL(r.url()).pathname==='/api/places')requests++});
  async function quickClick(text){
    const before=requests;await filterClick('#mapQuickFilters',text);await verifyApi();
    assert.equal(requests-before,1,'one quick action must trigger one search');
  }
  for(const [w,h] of [[320,568],[360,740],[375,812],[390,844],[430,932]]){
    await p.setViewportSize({width:w,height:h});
    const initial=await p.locator('#kakaoMap').boundingBox();
    assert.equal(await quick.isVisible(),false);assert.equal(await p.locator('#mapReset').isVisible(),false);
    await filterClick('.map-categories','맛집');
    assert.deepEqual(await quick.locator('button').allTextContents(),['전체 음식',...majors.map(t=>t.name)]);
    await quickClick('한식');
    assert.deepEqual(await quick.locator('button').allTextContents(),['← 한식','전체',...dishes.map(t=>t.name)]);
    assert.equal(await quick.evaluate(n=>n.scrollLeft),0);
    await quickClick('백반·정식');
    assert.equal(await quick.getByRole('button',{name:'백반·정식',exact:true}).getAttribute('aria-pressed'),'true');
    assert.equal(await p.evaluate(()=>document.activeElement.textContent),'백반·정식');
    await open();
    assert.equal(await p.locator('#mapCuisines').getByRole('button',{name:'한식',exact:true}).getAttribute('aria-pressed'),'true');
    assert.equal(await p.locator('#mapDetails').getByRole('button',{name:'백반·정식',exact:true}).getAttribute('aria-pressed'),'true');
    await filterClick('#mapDetails','전체');await apply();
    assert.equal(await quick.getByRole('button',{name:'전체',exact:true}).getAttribute('aria-pressed'),'true');
    await quickClick('한식에서 음식 대분류로 돌아가기');
    assert.equal(new URL(p.url()).searchParams.get('category'),'food');
    assert.equal(new URL(p.url()).searchParams.has('cuisine'),false);
    assert.equal(await quick.evaluate(n=>n.scrollLeft),0);
    await filterClick('.map-categories','카페');
    assert.deepEqual(await quick.locator('button').allTextContents(),['전체 카페',...cafes.map(t=>t.name)]);
    await quickClick(cafes[0].name);await quickClick(cafes[1].name);
    assert.equal(await quick.locator('[aria-pressed=true]').count(),2);
    assert.equal(new URL(p.url()).searchParams.has('cuisine'),false);
    await open();assert.equal(await p.locator('#mapDetails [aria-pressed=true]').count(),2);await apply();
    const layout=await quick.evaluate(n=>({height:n.getBoundingClientRect().height,ys:Array.from(n.children).map(b=>b.getBoundingClientRect().y),scroll:n.scrollWidth>n.clientWidth}));
    assert.equal(layout.height,44);assert.equal(new Set(layout.ys).size,1);assert.equal(layout.scroll,true);
    assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),w);
    const after=await p.locator('#kakaoMap').boundingBox();
    assert.equal(after.height,initial.height);assert.equal(after.y,initial.y);
    assert.equal(await p.locator('#mapActiveFilters').isVisible(),false);
    await p.locator('#mapReset').click();await ready();
    console.log('quick',w,'food drill/back, cafe multi, sheet sync, single row, map unchanged');
  }
  await p.setViewportSize({width:390,height:844});
  await filterClick('.map-categories','가볼 곳');
  assert.equal(await quick.isVisible(),filters.termsFor(taxonomy,'attraction').length>0);
  await p.locator('#mapReset').click();await ready();
  await filterClick('.map-categories','JEJU_KNOW PICK');assert.equal(await quick.isVisible(),false);
  await p.locator('#mapReset').click();await ready();
  await filterClick('.map-categories','맛집');await quickClick('한식');await quickClick('백반·정식');
  await p.goBack();await ready();assert.equal(await quick.getByRole('button',{name:'전체',exact:true}).getAttribute('aria-pressed'),'true');
  await p.goForward();await ready();assert.equal(await quick.getByRole('button',{name:'백반·정식',exact:true}).getAttribute('aria-pressed'),'true');
  const foodRows=await(await ctx.request.get(base+'/api/places'+new URL(p.url()).search)).json();
  const foodAnchor=foodRows.find(geo.hasCoordinates)||(await(await ctx.request.get(base+'/api/places?category=food')).json()).find(geo.hasCoordinates);
  if(foodAnchor){
    const point={latitude:foodAnchor.latitude,longitude:foodAnchor.longitude};await ctx.setGeolocation(point);
    await p.locator('[data-locate]').click();await p.waitForFunction(()=>!document.querySelector('[data-radius]').disabled);
    await p.locator('#mapRadiusMenu summary').click();await p.locator('[data-radius]').selectOption('5');
    assert.deepEqual(await p.locator('#mapResultsList article').evaluateAll(ns=>ns.map(n=>+n.dataset.placeId)),geo.within(foodRows,point,5).map(x=>x.id));
    await p.keyboard.press('Escape');
    console.log('food + korean + meal + nearby 5km',foodRows.length,'before radius');
  }else console.log('No public geocoded food result; radius covered by isolated fixture');
  if(process.env.MAP_EVIDENCE_DIR){
    await p.screenshot({path:path.join(process.env.MAP_EVIDENCE_DIR,'quick-drill-'+(local?'local':'live')+'.png')});
    await p.locator('#mapReset').click();await ready();await filterClick('.map-categories','맛집');
    await p.screenshot({path:path.join(process.env.MAP_EVIDENCE_DIR,'quick-major-'+(local?'local':'live')+'.png')});
  }
  await p.locator('#mapReset').click();await ready();
  await p.setViewportSize({width:390,height:844});
  await filterClick('.map-categories','맛집');
  assert.equal(await p.locator('#mapCuisines').isVisible(),false);
  const mapHeight=(await p.locator('#kakaoMap').boundingBox()).height;
  await open();await filterClick('#mapCuisines','한식');
  await verifyApi();await filterClick('#mapDetails','백반·정식');await verifyApi();
  await filterClick('#mapRegions','애월');await filterClick('#mapCompanions','부모님');
  assert.ok((await p.locator('#mapFilterSheet').boundingBox()).height<=844*.89);
  const findURL=await p.locator('#mapFindLink').getAttribute('href');
  assert.ok(findURL.startsWith('/find?') && findURL.includes('region='));
  assert.equal(await p.locator('#mapFilterSheet').evaluate(n=>n.contains(document.activeElement)),true);
  for(let i=0;i<30;i++){await p.keyboard.press('Tab');assert.equal(await p.locator('#mapFilterSheet').evaluate(n=>n.contains(document.activeElement)),true)}
  await apply();
  assert.equal((await p.locator('#kakaoMap').boundingBox()).height,mapHeight);
  for(const [w,h] of [[320,568],[360,740],[375,812],[390,844],[430,932]]){
    await p.setViewportSize({width:w,height:h});
    assert.ok((await p.locator('#kakaoMap').boundingBox()).height>=h*.5-1);
    assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),w);
    assert.ok(await p.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1));
  }
  await p.setViewportSize({width:390,height:844});
  assert.equal(await p.locator('#mapActiveFilters').isVisible(),false);
  await filterClick('#mapQuickFilters','백반·정식');await verifyApi();
  await open();await p.keyboard.press('Escape');assert.equal(await p.locator('#mapFilterSheet[open]').count(),0);
  await open();await p.mouse.click(3,3);assert.equal(await p.locator('#mapFilterSheet[open]').count(),0);
  await p.locator('#mapReset').click();await ready();
  const grip=await p.locator('#mapSheetToggle').boundingBox();
  await p.mouse.move(grip.x+grip.width/2,grip.y+grip.height/2);await p.mouse.down();await p.mouse.move(grip.x+grip.width/2,grip.y-80,{steps:6});await p.mouse.up();
  assert.equal(await p.locator('#mapWorkspace').getAttribute('data-list-level'),'1');
  await p.locator('#mapSheetClose').click();
  for(const expected of ['1','2','0']){
    await p.locator('#mapSheetToggle').click();
    assert.equal(await p.locator('#mapWorkspace').getAttribute('data-list-level'),expected);
    const sheet=await p.locator('.map-results').boundingBox();
    assert.ok(expected==='0'?sheet.height<=120:sheet.height>=(expected==='1'?400:650),JSON.stringify(sheet));
    assert.equal(await p.locator('.map-place-select').first().isVisible(),expected!=='0');
    assert.equal((await p.locator('#kakaoMap').boundingBox()).height,mapHeight);
  }
  await p.locator('#mapSheetToggle').click();
  await p.locator('.map-place-select').first().click();
  assert.equal(await p.locator('#mapWorkspace').getAttribute('data-list-level'),'0');
  await p.locator('#mapSelected:not([hidden])').waitFor();
  assert.equal(await p.locator('#mapSelected .detail-link').count(),2);
  await p.locator('#mapSelected .map-close').click();
  if(local)await p.locator('.map-pin').first().dispatchEvent('click');
  else {
    await p.locator('#mapFit').click();
    const visiblePin=()=>Array.from(document.querySelectorAll('.map-pin')).findIndex(n=>{
      const r=n.getBoundingClientRect();const x=r.x+r.width/2,y=r.y+r.height/2;
      return n.contains(document.elementFromPoint(x,y));
    });
    await p.waitForFunction(()=>Array.from(document.querySelectorAll('.map-pin')).some(n=>{
      const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
    }));
    const index=await p.evaluate(visiblePin);
    await p.locator('.map-pin').nth(index).click();
  }
  await p.locator('#mapSelected:not([hidden])').waitFor();
  await p.locator('#mapSelected .map-close').click();
  await filterClick('.map-categories','카페');
  await open();assert.equal(await p.locator('#mapDetails').isVisible(),true);assert.equal(await p.locator('#mapCuisines').isVisible(),false);
  await p.locator('#mapDetails button').nth(1).click();await verifyApi();
  await p.locator('#mapSheetReset').click();await ready();await apply();
  await p.locator('#mapQuery').fill('존재하지않는장소_모바일검증');await p.locator('#mapSearch button').click();await ready();
  assert.equal(await p.locator('#mapEditEmpty').isVisible(),true);
  assert.equal(await p.locator('#mapWorkspace').getAttribute('data-list-level'),'0');
  await p.locator('#mapEditEmpty').click();await p.locator('#mapSheetReset').click();await ready();await apply();
  const places=await(await ctx.request.get(base+'/api/places')).json();
  const anchor=places.find(geo.hasCoordinates);const point={latitude:anchor.latitude,longitude:anchor.longitude};
  await ctx.setGeolocation(point);
  await p.locator('[data-locate]').click();
  await p.waitForFunction(()=>!document.querySelector('[data-radius]').disabled);
  for(const r of [1,3,5,10]){
    if(!await p.locator('#mapRadiusMenu').evaluate(n=>n.open))await p.locator('#mapRadiusMenu summary').click();
    await p.locator('[data-radius]').selectOption(String(r));
    const expected=geo.within(places,point,r);
    assert.deepEqual(await p.locator('#mapResultsList article').evaluateAll(ns=>ns.map(n=>+n.dataset.placeId)),expected.map(x=>x.id));
    assert.deepEqual(await p.locator('.nearby-distance').allTextContents(),expected.map(x=>geo.formatDistance(x.distance)));
  }
  await p.keyboard.press('Escape');
  await p.locator('#mapFit').click();
  await p.locator('#mapLegend summary').click();assert.equal(await p.locator('#mapLegend ul').isVisible(),true);
  await p.keyboard.press('Escape');
  assert.ok((await p.locator('#mapRegisterLink').getAttribute('href')).startsWith('/map/register'));
  if(process.env.MAP_EVIDENCE_DIR)await p.screenshot({path:path.join(process.env.MAP_EVIDENCE_DIR,'mobile-discovery-'+(local?'local':'live')+'.png')});
  await p.locator('#mapReset').click();await ready();
  for(const [w,h] of [[768,1024],[1280,900],[1440,900],[1920,1080],[844,390],[390,844]]){
    await p.setViewportSize({width:w,height:h});await p.waitForTimeout(50);
    assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),w);
    const mobile=w===390||h===390;
    assert.equal(await p.locator('#mapOpenFilters').isVisible(),mobile);
    if(!mobile){
      assert.equal(await p.locator('.map-tools .map-extra-filters').isVisible(),true);assert.equal(await p.locator('.map-filter-panel .nearby-controls').count(),1);assert.equal(await p.locator('#mapResultsList').isVisible(),true);
      await filterClick('.map-categories','맛집');assert.equal(await quick.isVisible(),false);
      await filterClick('#mapCuisines','한식');assert.equal(await p.locator('#mapDetails').isVisible(),true);
      await filterClick('#mapDetails','백반·정식');await verifyApi();
      await p.locator('#mapReset').click();await ready();
    }
    if(h===390){await p.locator('#kakaoMap').scrollIntoViewIfNeeded();assert.ok((await p.locator('#kakaoMap').boundingBox()).height>=h*.5)}
    console.log('resize',w,h,'OK');
  }
  assert.equal(await beforeMap.evaluate(n=>n===document.querySelector('#kakaoMap')),true);
  if(local){assert.equal(await p.evaluate(()=>window.mapInstances),1);assert.ok(await p.evaluate(()=>window.relayouts>0))}
  const linked=await ctx.newPage();linked.on('pageerror',e=>errors.push(e.message));
  await linked.goto(base+'/find?category=food&cuisine='+korean.slug+'&detail='+meal.slug);
  await linked.locator('#finderMapLink').click();
  await linked.locator('#mapResultsList[aria-busy=false]').waitFor({state:'attached'});
  assert.equal(await linked.locator('.map-categories [data-category=food]').getAttribute('aria-pressed'),'true');
  assert.equal(await linked.locator('#mapQuickFilters').getByRole('button',{name:'백반·정식',exact:true}).getAttribute('aria-pressed'),'true');
  assert.equal(await linked.locator('#mapQuickFilters button').first().textContent(),'← 한식');
  assert.equal(new URL(linked.url()).searchParams.get('cuisine'),korean.slug);
  await linked.close();
  // A failed request must not leave the filter CTA loading forever.
  await ctx.route('**/api/places?**',r=>r.fulfill({status:503,body:'unavailable'}));
  await p.locator('#mapSearch button').click();await ready();await open();
  assert.equal(await p.locator('#mapFilterApply').textContent(),'다시 시도');
  await ctx.unroute('**/api/places?**');
  await p.locator('#mapFilterApply').click();await ready();await apply();
  const denied=await browser.newContext({viewport:{width:390,height:844}});
  await denied.route('**/api/analytics/events',r=>r.fulfill({status:204}));
  if(local)await denied.route('**/static/kakao-loader.js*',r=>r.fulfill({contentType:'application/javascript',body:sdk}));
  const d=await denied.newPage();await d.addInitScript(()=>{navigator.geolocation.getCurrentPosition=(ok,fail)=>fail({code:1})});
  await d.goto(base+'/map');await d.locator('[data-locate]').click();await d.getByText(/위치 권한을 허용하거나/).waitFor();
  assert.equal(await d.locator('#mapRadiusMenu').evaluate(n=>n.open),true);
  assert.equal(await d.locator('[data-radius]').isDisabled(),true);await denied.close();
  assert.deepEqual(errors,[]);
  console.log('PASS: API/counts, food/cafe/region/companion, chips, focus, close, sheets, marker, GPS/radii/distances, retries, denied permission, single map');
 } finally {await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
