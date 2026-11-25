/* Isolated headless Chromium. NEW_TEST_URL may point to production for read-only checks.
   No user browser/profile, admin session or production content is modified. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const base=process.env.NEW_TEST_URL||'http://127.0.0.1:8956';
const local=base.includes('127.0.0.1');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 try{
  const ctx=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});
  await ctx.route('**/api/analytics/events',r=>r.fulfill({status:204}));
  const p=await ctx.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
  async function ready(){await p.waitForFunction(()=>document.querySelector('#allPlaceGrid+button')?.textContent.startsWith('더 보기 ('));await p.evaluate(()=>document.fonts.ready)}
  async function open(query=''){await p.goto(base+'/new'+query);await ready()}
  const bounds=selector=>p.locator(selector).first().boundingBox();
  await open();
  for(const width of [320,360,375,390,430,768,1280,1440,1920]){
   await p.setViewportSize({width,height:900});
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),width,`overflow ${width}`);
   const mobile=width<=760,card=await bounds('.compact-place-card'),visual=await bounds('.compact-place-visual'),body=await bounds('.compact-place-body');
   assert.ok(card&&visual&&body,'fixture or production must have NEW places');
   assert.equal(await p.locator('.compact-place-card').first().evaluate(n=>getComputedStyle(n).flexDirection),'column');
   if(mobile){
    assert.ok(Math.abs(visual.width/visual.height-16/9)<.01,`16:9 ${width}`);
    assert.ok(body.y>=visual.y+visual.height-1);
    const summary=await bounds('#listSummary'),map=await bounds('#listMapLink');
    assert.ok(summary.y<map.y+map.height&&map.y<summary.y+summary.height,'one result row');
    assert.ok(summary.x+summary.width<=map.x,'result actions do not overlap');
    const submit=await bounds('.new-submit'),copy=await bounds('.new-policy');
    assert.ok(submit.y>=copy.y+copy.height+16,'CTA follows copy');
    assert.ok(Math.abs(submit.width-card.width)<2,'full width CTA');
    const chips=await p.locator('.new-tabs button').evaluateAll(ns=>ns.map(n=>n.getBoundingClientRect().y));
    assert.equal(new Set(chips).size,1,'three filters on one line');
    assert.deepEqual(await p.locator('.new-tabs button').allInnerTexts(),['전체','카페','맛집']);
    assert.equal(await p.locator('.compact-place-reason').first().evaluate(n=>getComputedStyle(n).webkitLineClamp),'2');
    assert.ok(await p.locator('.compact-place-card').first().locator('.tag').count()<=3);
   }else{
    assert.equal(Math.round(visual.height),128,'desktop photo height unchanged');
    const columns=await p.locator('#allPlaceGrid').evaluate(n=>getComputedStyle(n).gridTemplateColumns.split(' ').length);
    assert.equal(columns,width===768?3:5,'desktop grid unchanged');
   }
   console.log('layout',width,'pass');
   if(process.env.NEW_SCREENSHOT_DIR&&[390,1440].includes(width)){
    await p.screenshot({path:path.join(process.env.NEW_SCREENSHOT_DIR,`new-${local?'local':'production'}-${width}.png`),fullPage:false});
   }
  }
  await p.setViewportSize({width:390,height:844});
  for(const [value,label] of [['cafe','카페'],['food','맛집'],['','전체']]){
   await p.locator(`.new-tabs button[value="${value}"]`).click();await ready();
   assert.equal(new URL(p.url()).searchParams.get('category'),value);
   assert.equal(await p.locator(`.new-tabs button[value="${value}"]`).getAttribute('aria-pressed'),'true');
   const r=await ctx.request.get(base+'/api/places?new=current&limit=50&category='+value),rows=await r.json();
   const total=Number(r.headers()['x-total-count']);
   assert.equal(await p.locator('#listSummary').innerText(),`${label} · ${total}곳`);
   assert.equal(await p.locator('#allPlaceGrid article').count(),rows.length);
   if(value)assert.ok(rows.every(row=>row.category_slug===value));
   assert.equal(new URL(await p.locator('#listMapLink').getAttribute('href'),base).searchParams.get('new'),'current');
   await p.reload();await ready();assert.equal(await p.locator('#listSummary').innerText(),`${label} · ${total}곳`);
  }
  // Real hit tests, including the stretched-link overlay and independent MAP link.
  for(const selector of ['.compact-place-visual','h3','.compact-place-body']){
   await open();const first=p.locator('.compact-place-card').first();
   const href=await first.locator('.card-actions a').first().getAttribute('href');
   await first.locator(selector).scrollIntoViewIfNeeded();
   const box=await first.locator(selector).boundingBox();
   await p.mouse.click(box.x+8,box.y+8);await p.waitForURL(base+href);
  }
  await open();const mapHref=await p.locator('.card-actions a').nth(1).getAttribute('href');
  await p.locator('.card-actions a').nth(1).click();await p.waitForURL(base+mapHref);
  await open();await p.locator('.card-actions a').first().focus();await p.keyboard.press('Enter');await p.waitForURL('**/place/**');
  await open('?category=cafe&q=__new_layout_empty_result__');
  assert.ok(await p.locator('#listEmpty').isVisible());assert.equal(await p.locator('#allPlaceGrid article').count(),0);
  assert.match(await p.locator('#listEmpty').innerText(),/신상 카페가 없습니다/);
  await p.locator('#listEmpty a').click();await ready();assert.ok(await p.locator('#allPlaceGrid article').count()>0);
  if(local){
   await open();await p.locator('#allPlaceGrid+button').click();
   await p.waitForFunction(()=>document.querySelectorAll('#allPlaceGrid article').length===54);
   assert.equal(await p.locator('#allPlaceGrid+button').isVisible(),false);
   const rich=p.locator('.compact-place-card').filter({hasText:'바다를 바라보며 오래 머무르고 싶은 제주 카페'});
   assert.equal(await rich.locator('.tag').count(),3,'five fixture features are limited to three');
   assert.ok(await rich.locator('.badge-pick').isVisible());
   assert.ok(await rich.locator('img').isVisible());
   assert.equal(await p.locator('.compact-place-card:has(h3:text-is("새 장소 1")) .card-menu').isVisible(),true);
   assert.equal(await p.locator('.compact-place-card:has(h3:text-is("새 장소 1")) .compact-place-reason').isVisible(),false);
   assert.ok(await p.locator('.compact-place-visual:not(:has(img))').count()>0,'missing image fallback');
   await p.goto(base+'/places');await ready();
   assert.equal(await p.locator('.compact-place-card').first().evaluate(n=>getComputedStyle(n).flexDirection),'row','other mobile cards unchanged');
   assert.equal(await p.locator('.compact-place-card a').first().evaluate(n=>getComputedStyle(n,'::after').content),'none','no stretched link outside NEW');
  }
  // Server-rendered filters, categories and empty-state remain usable without JS.
  const nojs=await browser.newContext({javaScriptEnabled:false,viewport:{width:390,height:844}}),s=await nojs.newPage();
  await s.goto(base+'/new?category=cafe');
  assert.ok(await s.locator('.compact-place-card').count()>0);
  assert.equal(await s.locator('.new-tabs button[value=cafe]').getAttribute('aria-pressed'),'true');
  await s.locator('.new-tabs button[value=food]').click();assert.equal(new URL(s.url()).searchParams.get('category'),'food');
  await s.goto(base+'/new?category=cafe&q=__new_layout_empty_result__');assert.ok(await s.locator('#listEmpty').isVisible());
  assert.deepEqual(errors,[],'JavaScript runtime errors');
  console.log('filters / reload / detail hit areas / MAP / keyboard / empty / SSR passed');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
