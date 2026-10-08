const {test}=require('node:test');
const assert=require('node:assert/strict');
const {hasCoordinates,createMapView}=require('../static/map.js');
function fixture(){
 const calls={bounds:[],pans:[],overlays:[],centers:[],levels:[]};
 const sdk={
  LatLng:class{constructor(lat,lng){this.lat=lat;this.lng=lng;}},
  LatLngBounds:class{constructor(){this.points=[];}extend(p){this.points.push(p);}},
  Map:class{constructor(canvas,options){this.center=options.center;}setBounds(bounds){calls.bounds.push(bounds.points);}setCenter(center){this.center=center;calls.centers.push(center);}getCenter(){return this.center;}panTo(point){calls.pans.push(point);}setLevel(level){calls.levels.push(level);}relayout(){}},
  CustomOverlay:class{constructor(options){Object.assign(this,options);calls.overlays.push(this);}setMap(map){this.map=map;}setZIndex(value){this.zIndex=value;}}
 };
 const selected=[];
 const view=createMapView(sdk,{},(id,fromMarker)=>selected.push([id,fromMarker]),(place,index,click)=>({place,index,click,attributes:{},setAttribute(key,value){this.attributes[key]=value;}}));
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
