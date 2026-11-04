const {test}=require('node:test');
const assert=require('node:assert/strict');
const {hasCoordinates,placeStatus,cardPresentation,legendStatuses,makeStatusBadge,makeStatusPin,createMapView}=require('../static/map.js');
const cardStatus=require('../static/place-card-status.js');
function fixture(){
 const calls={bounds:[],pans:[],overlays:[],centers:[],levels:[]};
 const sdk={
  LatLng:class{constructor(lat,lng){this.lat=lat;this.lng=lng;}},
  LatLngBounds:class{constructor(){this.points=[];}extend(p){this.points.push(p);}},
  Map:class{constructor(canvas,options){this.center=options.center;}setBounds(bounds){calls.bounds.push(bounds.points);}setCenter(center){this.center=center;calls.centers.push(center);}getCenter(){return this.center;}panTo(point){calls.pans.push(point);}setLevel(level){calls.levels.push(level);}relayout(){}},
  CustomOverlay:class{constructor(options){Object.assign(this,options);calls.overlays.push(this);}setMap(map){this.map=map;}setZIndex(value){this.zIndex=value;}}
 };
 const selected=[];
 const view=createMapView(sdk,{},(id,fromMarker)=>selected.push([id,fromMarker]),(place,click)=>({place,click,attributes:{},setAttribute(key,value){this.attributes[key]=value;}}));
 return {calls,view,selected};
}
const cafe={id:1,latitude:33.4,longitude:126.3};
const food={id:2,latitude:33.5,longitude:126.6};
test('only real finite coordinate pairs become markers',()=>{
 for(const place of [{},{latitude:null,longitude:126},{latitude:'33',longitude:126},{latitude:NaN,longitude:126},{latitude:33,longitude:Infinity},{latitude:91,longitude:126}])assert.equal(hasCoordinates(place),false);
 assert.equal(hasCoordinates({latitude:0,longitude:0}),true);
 const {view,calls}=fixture();view.render([cafe,{id:3,latitude:null,longitude:null}]);
 assert.equal(calls.overlays.length,1);assert.equal(calls.levels[0],4);
});
test('changing filter replaces all markers and refits to matching bounds',()=>{
 const {view,calls}=fixture();view.render([cafe,food]);assert.equal(calls.bounds[0].length,2);
 view.render([food]);assert.equal(calls.overlays[0].map,null);assert.equal(calls.overlays[1].map,null);assert.equal(calls.overlays[2].content.place.id,2);
 view.render([]);assert.equal(calls.overlays[2].map,null);assert.equal(calls.overlays.length,3);
});
test('list selection pans and highlights; marker selection calls list callback',()=>{
 const {view,calls,selected}=fixture();view.render([cafe,food]);view.select(2);
 assert.equal(calls.pans[0].lat,33.5);assert.equal(calls.overlays[1].content.attributes['aria-pressed'],'true');assert.equal(calls.overlays[0].content.attributes['aria-pressed'],'false');
 calls.overlays[0].content.click();assert.deepEqual(selected,[[1,true]]);
 view.select(null);assert.equal(calls.overlays[1].content.attributes['aria-pressed'],'false');
});
test('resize preserves center without repeating fit; missing coordinates do not pan',()=>{
 const {view,calls}=fixture();view.render([cafe,food]);view.resize();view.resize();assert.equal(calls.bounds.length,1);
 view.select(999);assert.equal(calls.pans.length,0);view.fit();assert.equal(calls.bounds.length,2);
});

test('current monthly PICK takes priority over explicit verification, independently of category',()=>{
 for(const category of ['food','cafe','attraction']){
  assert.equal(placeStatus({category,is_pick:true,verification_status:'jejuno_verified'}).key,'pick');
  assert.equal(placeStatus({category,is_pick:false,verification_status:'jejuno_verified'}).key,'verified');
 }
 assert.equal(placeStatus({is_pick:1}).key,'pick');
 assert.equal(placeStatus({is_pick:0,source_type:'user'}).key,'user_submitted');
});

test('authorship, missing status and community confirmation never imply editorial verification',()=>{
 for(const source_type of ['admin','jejuno','user',undefined]){
  for(const verification_status of ['unverified','community_confirmed','unknown',undefined]){
   assert.equal(placeStatus({source_type,verification_status}).style,'unverified');
  }
 }
 assert.equal(placeStatus({source_type:'user',status_label:'검토 대기'}).label,'사용자 등록');
 assert.equal(placeStatus({source_type:'jejuno',status_label:'JEJU_KNOW 등록'}).label,'JEJU_KNOW 등록');
 assert.equal(placeStatus({verification_status:'unknown'}).label,'상태 확인 필요');
 for(const place of [{},{source_type:''},{source_type:'admin'},{submitted_by_user_id:10},{status_label:'사용자 등록'}]){
  assert.notEqual(placeStatus(place).label,'사용자 등록','missing provenance must never imply user submission');
 }
});

test('six provenance/priority cases agree across marker and MAP card without changing review workflow',()=>{
 const cases=[
  [{source_type:'user',verification_status:'unverified',status_label:'검토 대기'},'user_submitted','사용자 등록'],
  [{source_type:'user',verification_status:'jejuno_verified'},'verified','JEJU_KNOW 확인'],
  [{source_type:'user',verification_status:'jejuno_verified',is_pick:true},'pick','JEJU_KNOW PICK'],
  [{source_type:'jejuno',verification_status:'jejuno_verified'},'verified','JEJU_KNOW 확인'],
  [{source_type:'jejuno',verification_status:'unverified'},'editorial','JEJU_KNOW 등록'],
  [{verification_status:'unverified'},'default','상태 확인 필요']
 ];
 for(const [place,key,label]of cases){
  const original=JSON.stringify(place),marker=placeStatus(place);
  assert.equal(marker.key,key);assert.equal(marker.label,label);
  const view=cardPresentation({...place,is_new:true,opening_label:'2026년 11월 오픈'});
  assert.deepEqual(cardStatus.entries(view).map(s=>s.label),[label,'NEW']);
  assert.equal(view.opening_label,'2026년 11월 오픈');
  assert.equal(JSON.stringify(place),original,'presentation must not alter source/workflow data');
 }
});

test('legend explains real mapped fallback origins and uses the same status definitions',()=>{
 const places=[{...cafe,source_type:'jejuno'},{...food},{id:3,source_type:'user'}];
 assert.deepEqual(legendStatuses(places).map(s=>s.label),['사용자 등록','JEJU_KNOW 등록','상태 확인 필요','JEJU_KNOW 확인','JEJU_KNOW PICK']);
 assert.deepEqual(legendStatuses([{source_type:'jejuno'}]).map(s=>s.key),['user_submitted','verified','pick']);
 assert.equal(legendStatuses(places)[1],placeStatus(places[0]));
});

test('marker and card share status, with accessible text and a distinct symbol for each status',()=>{
 const doc={createElement:tag=>({tag,children:[],append(child){this.children.push(child);},attributes:{},events:{},setAttribute(k,v){this.attributes[k]=v;},addEventListener(k,v){this.events[k]=v;}})};
 for(const data of [{source_type:'user'},{verification_status:'jejuno_verified'},{is_pick:true,verification_status:'jejuno_verified'}]){
  const place={name:'<img onerror=alert(1)>',...data},status=placeStatus(place);
  let clicks=0;
  const pin=makeStatusPin(place,()=>clicks++,doc),badge=makeStatusBadge(place,doc);
  assert.ok(pin.className.includes(`map-status--${status.style}`));
  assert.ok(badge.className.includes(`map-status--${status.style}`));
  assert.equal(pin.attributes['data-marker-type'],status.key);
  assert.equal(pin.children[0].textContent,status.symbol);
  assert.equal(pin.children[0].attributes['aria-hidden'],'true');
  assert.doesNotMatch(pin.children[0].textContent,/\d/);
  assert.equal(badge.textContent,`${status.symbol} ${status.label}`);
  assert.ok(pin.attributes['aria-label'].includes(status.label));
  assert.equal(pin.innerHTML,undefined);
  assert.equal(pin.attributes['aria-pressed'],'false');
  const originalClass=pin.className;pin.setAttribute('aria-pressed','true');assert.equal(pin.className,originalClass);
  pin.events.click();assert.equal(clicks,1);
 }
});
