/* Run against tests/admin_place_save_server.py, never a production database. */
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.ADMIN_SAVE_BASE||'http://127.0.0.1:8957';
assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
  page.setDefaultTimeout(15000);page.setDefaultNavigationTimeout(15000);
  page.on('pageerror',e=>errors.push(e.message));
  const network=[];
  page.on('response',r=>{if(new URL(r.url()).pathname.startsWith('/admin/places'))network.push({method:r.request().method(),path:new URL(r.url()).pathname,status:r.status()});});
  const state=async()=>{const response=await context.request.get(base+'/qa/state');assert.equal(response.status(),200);return response.json()};
  const form=page.locator('.place-form'),button=form.locator('button[type=submit]');
  async function open(id=1){await page.goto(`${base}/admin/places/${id}/edit`);await form.waitFor();await form.locator('details').evaluateAll(ns=>ns.forEach(n=>n.open=true));}
  async function save(edit,id=1){
   await open(id);const before=await state();network.length=0;
   await edit();await button.click();await page.waitForURL(url=>url.pathname==='/admin/places');
   const after=await state();
   assert.deepEqual(network.filter(r=>r.method==='POST'),[{method:'POST',path:`/admin/places/${id}/edit`,status:200}]);
   assert.equal(network.filter(r=>r.method==='GET'&&r.path==='/admin/places').length,1);
   assert.match(await page.locator('.flash.success').innerText(),/수정사항을 저장했습니다/);
   assert.equal(after.updates.length-before.updates.length,1,'one primary DB UPDATE');
   assert.equal(after.logs.filter(r=>r.action==='장소 수정').length-before.logs.filter(r=>r.action==='장소 수정').length,1,'one committed edit audit');
   return after.places.find(r=>r.id===id);
  }
  await page.goto(base+'/qa/admin');
  let row=await save(()=>form.locator('[name=name]').fill('한 번에 저장한 카페'));
  assert.equal(row.name,'한 번에 저장한 카페');console.log('PASS name / POST once / DB update once / flash once');
  row=await save(async()=>{await form.locator('[name=info_source]').fill('마지막 입력 후 바로 저장');assert.equal(await page.evaluate(()=>document.activeElement.name),'info_source')});
  assert.equal(row.info_source,'마지막 입력 후 바로 저장');console.log('PASS focused last field');
  for(const checked of [true,false]){
   row=await save(async()=>{for(const name of ['published','is_pick','is_example'])await form.locator(`[name=${name}]`).setChecked(checked)});
   for(const name of ['published','is_pick','is_example'])assert.equal(row[name],Number(checked));
  }
  console.log('PASS published / PICK / example ON and OFF');
  row=await save(async()=>{await form.locator('[name=new_override]').selectOption('hide');await form.locator('[name=opened_year]').fill('2026');await form.locator('[name=opened_month]').fill('11')});
  assert.deepEqual([row.new_override,row.opened_year,row.opened_month],['hide',2026,11]);console.log('PASS NEW / last focused month');
  let selected=[];
  row=await save(async()=>{
   const terms=form.locator('[name=term_ids]:enabled');await terms.first().check();await terms.nth(1).check();selected=await terms.evaluateAll(ns=>ns.filter(n=>n.checked).map(n=>Number(n.value)));
   await form.locator('[name=companions]').first().check();await form.locator('[name=companions]').nth(1).check();
  });
  assert.equal(JSON.parse(row.companions).length,2);assert.deepEqual((await state()).links.filter(r=>r.place_id===1).map(r=>r.category_id).sort(),selected.sort());
  row=await save(async()=>{
   const options=await form.locator('[name=restaurant_main_id] option').evaluateAll(ns=>ns.map(n=>({value:n.value,label:n.textContent.trim()})));
   await form.locator('[name=restaurant_main_id]').selectOption(options.find(n=>n.label==='일식').value);
   const subtypes=form.locator('[name=restaurant_subtype_ids]:enabled');await subtypes.first().check();await subtypes.nth(1).check();selected=await subtypes.evaluateAll(ns=>ns.filter(n=>n.checked).map(n=>Number(n.value)));
  },2);
  assert.deepEqual((await state()).links.filter(r=>r.place_id===2).map(r=>r.category_id).sort(),selected.sort());console.log('PASS cafe / food taxonomy / companions');
  await open();network.length=0;await form.locator('[name=name]').fill('');await button.click();
  await page.waitForFunction(()=>document.querySelector('.place-form [data-submit-status]').getAttribute('role')==='alert');
  assert.equal(network.filter(r=>r.method==='POST').length,0);assert.equal(await page.evaluate(()=>document.activeElement.name),'name');assert.match(await form.locator('[data-submit-status]').innerText(),/저장하지 못했습니다/);
  assert.equal(await button.isEnabled(),true);console.log('PASS native validation / focus / no request');
  await open();await form.locator('[name=website_url]').fill('invalid-url');await form.locator('[name=website_url]').evaluate(n=>n.closest('details').open=false);await button.click();
  assert.equal(await page.evaluate(()=>document.activeElement.name),'website_url');assert.equal(await form.locator('[name=website_url]').evaluate(n=>n.closest('details').open),true);console.log('PASS invalid URL in closed section');
  // Delayed response permits a real rapid double-click while pending.
  await open();let release;
  await page.route('**/admin/places/1/edit',async route=>{if(route.request().method()==='POST'){await new Promise(r=>release=r)}await route.continue()});
  network.length=0;const beforeDouble=await state();await button.dblclick();
  await page.waitForFunction(()=>document.querySelector('.place-form').getAttribute('aria-busy')==='true');
  assert.equal(await button.isDisabled(),true);assert.equal(await button.innerText(),'저장 중...');
  await page.waitForTimeout(150);assert.ok(release);release();await page.waitForURL(url=>url.pathname==='/admin/places');await page.unroute('**/admin/places/1/edit');
  assert.equal(network.filter(r=>r.method==='POST').length,1);assert.equal((await state()).updates.length-beforeDouble.updates.length,1);console.log('PASS double click / disabled / aria-busy');
  // The first click during image processing waits; it does not need another click.
  await open();await page.evaluate(()=>{const original=createImageBitmap;window.createImageBitmap=async(...args)=>{await new Promise(r=>window.finishPhoto=r);return original(...args)}});
  const png=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=c.height=100;return c.toDataURL('image/png').split(',')[1]});
  await form.locator('input[type=file]').first().setInputFiles({name:'still.png',mimeType:'image/png',buffer:Buffer.from(png,'base64')});
  await page.waitForFunction(()=>typeof window.finishPhoto==='function');network.length=0;
  assert.equal(await button.isEnabled(),true);await button.click();assert.equal(await button.isDisabled(),true);assert.match(await button.innerText(),/사진 처리 대기/);assert.equal(network.filter(r=>r.method==='POST').length,0);
  await page.evaluate(()=>finishPhoto());await page.waitForURL(url=>url.pathname==='/admin/places');
  assert.equal(network.filter(r=>r.method==='POST').length,1);assert.equal((await state()).images.filter(r=>r.place_id===1).length,1);console.log('PASS click during image processing / image persisted');
  for(const status of [400,403,422,500]){
   await open();const before=await state();await form.locator('[name=info_source]').fill('실패해도 보존 '+status);
   await page.route('**/admin/places/1/edit',route=>route.request().method()==='POST'?route.fulfill({status,contentType:'application/json',body:JSON.stringify({detail:'격리된 서버 오류 '+status})}):route.continue());
   await button.click();await page.waitForFunction(()=>document.querySelector('.place-form [data-submit-status]').getAttribute('role')==='alert');
   assert.equal(await button.isEnabled(),true);assert.equal(await button.innerText(),'수정사항 저장');assert.equal(await form.getAttribute('aria-busy'),'false');assert.equal(await form.locator('[name=info_source]').inputValue(),'실패해도 보존 '+status);assert.match(await form.locator('[data-submit-status]').innerText(),new RegExp(String(status)));assert.equal((await state()).updates.length,before.updates.length);
   await page.unroute('**/admin/places/1/edit');
  }
  await button.click();await page.waitForURL(url=>url.pathname==='/admin/places');console.log('PASS 400 / 403 / 422 / 500 / retry / preserved inputs');
  await open();const beforeCsrf=await state();await form.locator('[name=csrf_token]').evaluate(n=>n.value='invalid');await button.click();await page.waitForFunction(()=>document.querySelector('.place-form [data-submit-status]').getAttribute('role')==='alert');assert.match(await form.locator('[data-submit-status]').innerText(),/CSRF/);assert.equal((await state()).updates.length,beforeCsrf.updates.length);console.log('PASS real CSRF rejection');
  await open();const beforeDate=(await state()).places[0].checked_at;assert.equal(await form.locator('[name=checked_at]').count(),0);
  await page.locator('#confirmInformation button').click();await page.waitForFunction(()=>document.querySelector('#confirmInformation [role=status]').textContent.length>0);
  assert.notEqual((await state()).places[0].checked_at,beforeDate);console.log('PASS separate information-confirmation policy');
  // New form duplicate preflight + image helper still result in one POST.
  await page.goto(base+'/admin/places/new');await form.locator('details').evaluateAll(ns=>ns.forEach(n=>n.open=true));
  await form.locator('[name=hours_unknown]').check();
  await form.locator('[name=name]').fill('첫 클릭 신규 장소');await form.locator('[name=region]').selectOption({label:'구좌'});await form.locator('[name=category_id]').selectOption({label:'카페'});await form.locator('[name=address]').fill('제주시 구좌읍 신규 테스트로 987');
  await form.locator('[name=latitude]').fill('33.5');await form.locator('[name=longitude]').fill('126.8');await form.locator('[name=checked_at]').fill('2026-11-25');
  await page.waitForFunction(()=>document.querySelector('[data-duplicate-status]').textContent.includes('겹치는 장소 후보가 없습니다'));
  network.length=0;await button.click();await page.waitForURL(url=>url.pathname==='/admin/places');
  assert.equal(network.filter(r=>r.method==='POST'&&r.path==='/admin/places/new').length,1);assert.equal((await state()).places.find(r=>r.name==='첫 클릭 신규 장소').checked_at,'2026-11-25');console.log('PASS new place / duplicate preflight / focused date');
  for(const width of [320,390,1440]){
   await page.setViewportSize({width,height:900});await open();await form.locator('[name=info_source]').fill('화면 폭 '+width);await button.click();await page.waitForURL(url=>url.pathname==='/admin/places');assert.equal((await state()).places[0].info_source,'화면 폭 '+width);
  }
  console.log('PASS Chrome desktop / 320px / 390px single click');
  assert.deepEqual(errors,[]);console.log('PASS no JavaScript errors');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
