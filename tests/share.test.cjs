const test=require('node:test'),assert=require('node:assert/strict');
const {share}=require('../static/share.js');
const data={title:'장소 | JEJU_KNOW',text:'실제 정보',url:'https://www.jeju-know.com/place/cafe'};
test('native share receives canonical content, not current query URL',async()=>{
 let received;assert.equal(await share(data,{navigator:{share:async v=>{received=v;}}}),'shared');assert.deepEqual(received,data);
});
test('unsupported and failing native share copy the canonical URL',async()=>{
 for(const shareMethod of [undefined,async()=>{throw new Error('not allowed');}]){
  let copied;assert.equal(await share(data,{navigator:{share:shareMethod,clipboard:{writeText:async v=>{copied=v;}}}}),'copied');assert.equal(copied,data.url);
 }
});
test('cancelling the native sheet does not unexpectedly copy',async()=>{
 assert.equal(await share(data,{navigator:{share:async()=>{throw Object.assign(new Error(),{name:'AbortError'});}}}),'cancelled');
});
test('denied clipboard tries legacy copy and offers manual copy when both fail',async()=>{
 for(const allowed of [true,false]){
  let removed=false,selected=false;
  const input={style:{},setAttribute(){},select(){selected=true;},setSelectionRange(){},remove(){removed=true;}};
  const env={navigator:{clipboard:{writeText:async()=>{throw new Error('denied');}}},document:{createElement:()=>input,body:{append(){}},execCommand:()=>allowed}};
  assert.equal(await share(data,env),allowed?'copied':'manual');assert.equal(input.value,data.url);assert.ok(selected&&removed);
 }
});
