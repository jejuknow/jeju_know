/* Runs only against the isolated auto-publish fixture, never production writes. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),path=require('node:path');
const base=process.env.AUTO_TEST_URL||'http://127.0.0.1:8941';
assert.ok(base.startsWith('http://127.0.0.1:'),'Isolated local fixture required');
const sdk=`window.JejunoKakao={geocode:async()=>({lat:33.45,lng:126.56}),load:async()=>({LatLng:class{constructor(a,b){this.a=a;this.b=b}getLat(){return this.a}getLng(){return this.b}},Map:class{constructor(el,o){this.center=o.center}getCenter(){return this.center}setCenter(p){this.center=p}setLevel(){}relayout(){}},Marker:class{setMap(){}setPosition(){}},event:{addListener(){}}})}`;
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 try{
  const ctx=await browser.newContext({viewport:{width:390,height:844}}),p=await ctx.newPage(),errors=[];
  p.on('pageerror',e=>errors.push(e.message));
  await ctx.route('**/static/kakao-loader.js*',r=>r.fulfill({contentType:'application/javascript',body:sdk}));
  await ctx.route('**/api/analytics/events',r=>r.fulfill({status:204}));
  async function login(url,name){await p.goto(base+url);await p.locator('[name=username]').fill(name);await p.locator('[name=password]').fill('local-auto-test-only');await p.locator('button[type=submit]').click();await p.waitForURL(u=>!u.pathname.endsWith('/login'))}
  await login('/login','auto-member');
  const suffix=require('node:crypto').randomUUID();
  async function form(name){await p.goto(base+'/map/register?category=cafe');await p.locator('[name=name]').fill(name);await p.locator('[name=region]').selectOption('애월');await p.locator('[name=address]').fill('제주특별자치도 제주시 테스트로 123');await p.locator('[name=address]').blur();await p.getByText('위치 확인됨 · 지도 위치를 찾았습니다.',{exact:true}).waitFor()}
  await form('브라우저 정상 '+suffix);
  for(const width of [320,360,375,390,430,1280]){await p.setViewportSize({width,height:900});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),width);console.log('member form width',width,'OK')}
  await p.setViewportSize({width:390,height:844});
  await p.locator('[name=image_0]').setInputFiles({name:'photo.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAKAAAAB4CAIAAAD6wG44AAABMUlEQVR4nO3RwQnAIADAQO3+I/p2CF+dQoRwN0Egc+0z6PpeB3CXwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGxxkcZ3CcwXEGj7YfOp8DrP7UD7QAAAAASUVORK5CYII=','base64')});
  await p.getByRole('button',{name:'장소 등록하기',exact:true}).click();await p.waitForURL('**/place/*').catch(async e=>{console.log(await p.locator('[data-submit-status]').innerText(),await p.locator('.image-feedback').allTextContents());throw e});
  assert.ok((await p.locator('body').innerText()).includes('사용자 등록 장소로 지도에 표시됩니다.'));
  const normalURL=p.url();
  await form('브라우저 정상 '+suffix);await p.getByRole('button',{name:'장소 등록하기',exact:true}).click();await p.locator('.duplicate-warning').waitFor();
  assert.equal(await p.locator('[name=duplicate_ack]').count(),0);
  assert.equal(await p.locator('.duplicate-warning a').first().getAttribute('href'),new URL(normalURL).pathname);
  await form('브라우저 보류 '+suffix);await p.locator('#locationPicker summary').click();await p.locator('#locationClear').click();await p.getByRole('button',{name:'장소 등록하기',exact:true}).click();await p.waitForURL('**/account/places#activity-*');
  assert.ok((await p.locator('body').innerText()).includes('추가 확인이 필요해 지도에는 아직 표시되지 않습니다.'));
  assert.ok((await p.locator('body').innerText()).includes('위치 확인 필요'));
  await p.screenshot({path:path.join(process.env.QA_DIR||'.','auto-publish-member-mobile.png'),fullPage:true});
  await login('/admin/login','auto-admin');await p.goto(base+'/admin/community');
  for(const width of [320,360,375,390,430,1280]){await p.setViewportSize({width,height:900});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),width);console.log('admin queue width',width,'OK')}
  assert.ok((await p.locator('body').innerText()).includes('공개 보류'));
  await p.screenshot({path:path.join(process.env.QA_DIR||'.','auto-publish-admin-desktop.png'),fullPage:true});
  await p.goto(base+'/admin/settings/general');assert.ok(await p.locator('[name=user_place_auto_publish]').isChecked());
  assert.deepEqual(errors,[]);console.log('Normal success / exact duplicate / no bypass / HOLD account redirect / flash / admin queue: PASS');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
