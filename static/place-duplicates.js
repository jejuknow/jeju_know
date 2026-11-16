/* Shared preflight only suggests candidates; the server always checks again. */
(() => {
  document.querySelectorAll('form[data-duplicate-check]').forEach(form => {
    const names=['name','address','phone','region','category_id','latitude','longitude'];
    const status=form.querySelector('[data-duplicate-status]');
    const box=()=>form.querySelector('[data-duplicate-warning]');
    let timer,revision=0,checkedKey='',pending,pass=false,submitting=false;
    function values(){return names.map(name=>String(form.elements.namedItem(name)?.value||''));}
    function key(){return JSON.stringify(values());}
    function render(items){
      const warning=box(), list=warning.querySelector('[data-duplicate-candidates]');
      list.replaceChildren(); warning.hidden=!items.length;
      warning.querySelector('[name=duplicate_ack]').checked=false;
      items.forEach(item=>{
        const article=document.createElement('article'); article.className='duplicate-candidate';
        if(item.image_url){const img=document.createElement('img'); img.src=item.image_url;img.alt='';img.loading='lazy';article.append(img);}
        const body=document.createElement('div'),title=document.createElement('strong'),detail=document.createElement('p'),link=document.createElement('a');
        title.textContent=item.name;detail.textContent=[item.address||item.region,item.category,item.label,...(item.reasons||[])].filter(Boolean).join(' · ');
        if(typeof item.url==='string'&&/^\/(?:admin\/places\/\d+\/edit|place\/[\w-]+)$/.test(item.url)){
          link.href=item.url;link.textContent='기존 장소 보기 →';link.target='_blank';link.rel='noopener';
        }
        body.append(title,detail,link);article.append(body);list.append(article);
      });
    }
    async function check(){
      const signature=key(), version=revision;
      if(signature===checkedKey)return true;
      if(pending?.key===signature)return pending.promise;
      const promise=(async()=>{
        const data=new FormData();names.forEach((name,i)=>data.set(name,values()[i]));
        data.set('csrf_token',form.elements.namedItem('csrf_token').value);
        if(form.dataset.duplicateExclude)data.set('exclude_id',form.dataset.duplicateExclude);
        status.textContent='비슷한 장소를 확인하고 있어요.';
        try{
          const response=await fetch(form.dataset.duplicateCheck,{method:'POST',body:data,credentials:'same-origin',headers:{Accept:'application/json'}});
          if(!response.ok)throw new Error('check failed');
          const result=await response.json();
          if(version!==revision||signature!==key())return false;
          render(result.candidates);checkedKey=signature;
          status.textContent=result.candidates.length?'기존 후보를 확인해주세요.':'현재 입력과 겹치는 공개 장소 후보가 없습니다.';
          if(form.dataset.duplicateAudience==='admin'&&!result.candidates.length)status.textContent='현재 입력과 겹치는 장소 후보가 없습니다.';
          return true;
        }catch(_){status.textContent='미리 확인하지 못했습니다. 저장할 때 서버에서 다시 확인합니다.';return true;}
      })();
      const request={key:signature,promise};pending=request;
      promise.finally(()=>{if(pending===request)pending=null;});return promise;
    }
    function changed(event){
      if(!names.includes(event.target.name))return;
      revision++;checkedKey='';clearTimeout(timer);
      const warning=box();if(warning)warning.querySelector('[name=duplicate_ack]').checked=false;
      if(form.elements.namedItem('name')?.value&&form.elements.namedItem('address')?.value)timer=setTimeout(check,500);
    }
    form.addEventListener('place-duplicate-warning',()=>{checkedKey=key();});
    form.addEventListener('input',changed);form.addEventListener('change',changed);
    // Capture before the existing photo uploader; selected files stay in its state.
    form.addEventListener('submit',async event=>{
      if(pass){pass=false;return;}
      event.preventDefault();event.stopImmediatePropagation();clearTimeout(timer);
      if(submitting)return;
      submitting=true;
      try{await form.prepareLocation?.();if(!await check())return;}
      catch(_){status.textContent='지도 위치를 확인한 뒤 다시 등록해주세요.';return;}
      finally{submitting=false;}
      const warning=box();
      if(warning&&!warning.hidden&&!warning.querySelector('[name=duplicate_ack]').checked){
        status.textContent='기존 후보를 확인한 뒤 다른 장소인지 선택해주세요.';
        warning.scrollIntoView({block:'center',behavior:'smooth'});return;
      }
      if(!form.reportValidity())return;
      pass=true;form.requestSubmit(event.submitter||undefined);
    },true);
    // A server warning rendered after final validation has already been checked.
    if(box()&&!box().hidden)checkedKey=key();
  });
})();
