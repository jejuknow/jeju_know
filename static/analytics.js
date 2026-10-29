/* A single page-display event. Feature entry counts are derived on the server. */
(function(root){
  'use strict';
  function createTracker({send,now=Date.now,id=()=>crypto.randomUUID()}){
    let viewed=false,lastSearch='',lastAt=-Infinity;
    return {
      page(){if(viewed)return;viewed=true;send({type:'page_view',id:id()});},
      search(state){
        const filters={region:state.region||'',category:state.category||'',
          terms:[...new Set(state.terms||[])].sort(),companions:[...new Set(state.companions||[])].sort()};
        const key=JSON.stringify(filters),at=now();
        if(key===lastSearch&&at-lastAt<2000)return;
        lastSearch=key;lastAt=at;send({type:'find_search',id:id(),filters});
      }
    };
  }
  if(typeof module==='object')module.exports={createTracker};
  if(!root.document)return;
  const token=document.querySelector('meta[name="analytics-token"]')?.content;
  if(!token)return;
  // Serialize the first requests so the server visitor cookie is established once.
  let queue=Promise.resolve();
  const send=event=>{
    event.touch_tablet=/Macintosh/.test(navigator.userAgent)&&navigator.maxTouchPoints>1;
    queue=queue.then(()=>fetch('/api/analytics/events',{method:'POST',credentials:'same-origin',keepalive:true,
      headers:{'Content-Type':'application/json','X-Analytics-Token':token},body:JSON.stringify(event)})).catch(()=>{});
  };
  const tracker=createTracker({send});
  root.JejuAnalytics=tracker;
  const visible=()=>{if(document.visibilityState==='visible')tracker.page();};
  if(document.readyState==='complete')visible();else addEventListener('load',visible,{once:true});
  document.addEventListener('visibilitychange',visible);
})(typeof window==='undefined'?{}:window);
