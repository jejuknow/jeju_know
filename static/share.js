/* One canonical share path for place and BEST; no account or third-party SDK. */
(function(root){
  'use strict';
  async function copyLink(url,env){
    if(env.navigator.clipboard?.writeText){try{await env.navigator.clipboard.writeText(url);return true;}catch{}}
    const input=env.document.createElement('textarea');input.value=url;input.setAttribute('readonly','');
    input.style.position='fixed';input.style.opacity='0';env.document.body.append(input);input.select();input.setSelectionRange(0,url.length);
    let copied=false;try{copied=env.document.execCommand('copy');}catch{}finally{input.remove();}
    return copied;
  }
  async function share(data,env){
    if(typeof env.navigator.share==='function'){
      try{await env.navigator.share(data);return 'shared';}catch(error){if(error.name==='AbortError')return 'cancelled';}
    }
    return await copyLink(data.url,env)?'copied':'manual';
  }
  if(typeof module!=='undefined'&&module.exports){module.exports={copyLink,share};return;}
  const box=document.querySelector('[data-share-content]');if(!box)return;
  const data={title:box.dataset.title,text:box.dataset.text,url:box.dataset.url};
  const status=box.querySelector('.share-status'),manual=box.querySelector('.share-manual');
  document.querySelectorAll('[data-share],[data-copy-link]').forEach(button=>button.addEventListener('click',async()=>{
    button.disabled=true;status.textContent='';manual.hidden=true;
    try{
      const result=button.hasAttribute('data-copy-link')?(await copyLink(data.url,root)?'copied':'manual'):await share(data,root);
      if(result==='copied')status.textContent='링크가 복사되었습니다.';
      if(result==='manual'){manual.hidden=false;manual.querySelector('input').focus();manual.querySelector('input').select();status.textContent='아래 링크를 복사해주세요.';}
    }finally{button.disabled=false;}
  }));
})(typeof window==='undefined'?globalThis:window);
