const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const {dimensions,boundedSize}=require('../static/image-uploads.js');

test('photo picker diagnostics are opt-in, local, and contain only name/type/size',()=>{
 const logs=[];
 const context={module:{exports:{}},URLSearchParams,location:{search:''},console:{debug:(...args)=>logs.push(args)}};
 vm.runInNewContext(fs.readFileSync(require.resolve('../static/image-uploads.js'),'utf8'),context);
 const file={name:'IMG_1234.JPG',type:'image/jpeg',size:2192514,privateMetadata:'never log'};
 context.module.exports.debugPhoto(file);assert.equal(logs.length,0);
 context.location.search='?photo_debug=1';context.module.exports.debugPhoto(file);
 assert.equal(logs.length,1);
 assert.deepEqual(JSON.parse(JSON.stringify(logs[0][1])),{name:file.name,type:file.type,size:file.size});
});

test('phone image headers and portrait bounds',()=>{
 const png=Buffer.alloc(24);png.writeUInt32BE(0x89504e47);png.writeUInt32BE(6048,16);png.writeUInt32BE(8064,20);
 assert.deepEqual(dimensions(png),{width:6048,height:8064});
 assert.deepEqual(boundedSize(6048,8064),{width:1440,height:1920});
 assert.deepEqual(boundedSize(800,600),{width:800,height:600});
 assert.equal(dimensions(Buffer.from('unknown')),null);
});

test('HEIC and unsupported/failed browser decoders fall back to original',async()=>{
 const context={module:{exports:{}},Uint8Array,DataView,createImageBitmap:async()=>{throw new Error('unsupported');}};
 vm.runInNewContext(fs.readFileSync(require.resolve('../static/image-uploads.js'),'utf8'),context);
 for(const name of ['phone.heic','phone.jpg']){
  const bytes=Buffer.alloc(24);bytes.writeUInt32BE(0x89504e47);bytes.writeUInt32BE(100,16);bytes.writeUInt32BE(100,20);
  const file={name,slice:()=>({arrayBuffer:async()=>bytes})};
  assert.equal(await context.module.exports.optimizePhoto(file),file);
 }
});

function locationFixture(){
 const element=(value='')=>({value,events:{},dataset:{},addEventListener(n,f){this.events[n]=f;}});
 const address=element(),lat=element(),lng=element(),button=element(),message=element(),clear=element();
 const details={...element(),open:false,hasAttribute:()=>true};address.form={};
 const calls=[],events={};let marker;
 class LatLng{constructor(lat,lng){this.lat=lat;this.lng=lng;}getLat(){return this.lat;}getLng(){return this.lng;}}
 const sdk={LatLng,Map:class{constructor(_,opts){this.center=opts.center;}getCenter(){return this.center;}setCenter(p){this.center=p;}setLevel(){}relayout(){}},
  Marker:class{constructor(opts){this.position=opts.position;marker=this;}setPosition(p){this.position=p;}getPosition(){return this.position;}setMap(m){this.map=m;}},
  event:{addListener:(_,n,f)=>{events[n]=f;}},services:{Status:{OK:'OK'},Geocoder:class{addressSearch(value,callback){calls.push({value,callback});}coord2Address(){}}}};
 const ids={addressLocate:button,locationFeedback:message,placeLocationMap:{},locationPicker:details,locationClear:clear};
 const selectors={'[name="address"]':address,'[name="latitude"]':lat,'[name="longitude"]':lng};
 const timers=new Map();let timerId=0;
 const context={document:{getElementById:id=>ids[id],querySelector:s=>selectors[s]},window:{},ResizeObserver:class{observe(){}},
  setTimeout:f=>{timers.set(++timerId,f);return timerId;},clearTimeout:id=>timers.delete(id)};
 vm.runInNewContext(fs.readFileSync(require.resolve('../static/kakao-loader.js'),'utf8'),context);
 context.window.JejunoKakao.load=async()=>sdk;
 vm.runInNewContext(fs.readFileSync(require.resolve('../static/admin-location.js'),'utf8'),context);
 const tick=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};
 const start=async value=>{address.value=value;address.events.input();address.events.blur();await tick();};
 const finish=async(index,lat,lng)=>{calls[index].callback([{y:String(lat),x:String(lng)}],'OK');await tick();};
 return {address,lat,lng,button,message,details,clear,calls,events,sdk,tick,start,finish,get marker(){return marker;}};
}
test('new address clears stale coordinates and ignores old geocoder response',async()=>{
 const f=locationFixture();await f.start('제주시 첫 주소');await f.start('제주시 다음 주소');
 await f.finish(1,33.5,126.5);await f.finish(0,33.1,126.1);
 assert.equal(f.lat.value,'33.5000000');assert.equal(f.details.open,false);
 f.address.value='서귀포 새 주소';f.address.events.input();assert.equal(f.lat.value,'');assert.equal(f.lng.value,'');
});
test('manual position wins over pending lookup; next address invalidates manual pin',async()=>{
 const f=locationFixture();f.details.open=true;await f.details.events.toggle();await f.tick();
 await f.start('제주 주소');f.events.click({latLng:new f.sdk.LatLng(33.4,126.4)});
 await f.finish(0,33.8,126.8);assert.equal(f.lat.value,'33.4000000');assert.equal(f.button.disabled,false);
 await f.start('다른 주소');assert.equal(f.lat.value,'');await f.finish(1,33.6,126.6);assert.equal(f.lat.value,'33.6000000');
});
test('failed lookup opens map fallback; explicit clear remains empty at submit',async()=>{
 const f=locationFixture();await f.start('존재하지 않는 주소');f.calls[0].callback([],'ZERO_RESULT');await f.tick();
 assert.equal(f.details.open,true);assert.match(f.message.textContent,/직접 위치/);
 f.clear.events.click();await f.address.form.prepareLocation();assert.equal(f.lat.value,'');assert.equal(f.calls.length,1);
});
