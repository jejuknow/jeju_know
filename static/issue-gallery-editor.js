(() => {
  const root=document.querySelector('[data-issue-gallery-editor]'); if(!root)return;
  const form=root.closest('form'),list=root.querySelector('[data-gallery-list]'),picker=root.querySelector('[data-gallery-picker]'),status=root.querySelector('[data-gallery-status]');
  let entries=[...list.children].map(row=>({key:row.dataset.galleryKey,url:row.dataset.url,name:row.dataset.name})),queue=Promise.resolve(),processing=false,drag=null;
  form.elements.gallery_original.value=JSON.stringify(entries.map(e=>e.key));
  function render(){
    list.replaceChildren();
    entries.forEach((entry,i)=>{
      const row=document.createElement('li');row.dataset.galleryKey=entry.key;
      const image=document.createElement('img');image.src=entry.url;image.alt=`추가 이미지 ${i+1} 미리보기`;
      const name=document.createElement('span');name.className='issue-gallery-name';name.textContent=`${i+1}. ${entry.name}`;
      const controls=document.createElement('div');controls.className='issue-gallery-actions';
      for(const [action,label] of [['drag','☰'],['up','↑'],['down','↓'],['remove','삭제']]){
        const b=document.createElement('button');b.type='button';b.className='secondary-btn';b.dataset.action=action;b.textContent=label;
        b.setAttribute('aria-label',`추가 이미지 ${i+1} ${action==='drag'?'순서 이동':action==='up'?'위로':action==='down'?'아래로':'삭제'}`);
        b.disabled=processing||(action==='up'&&i===0)||(action==='down'&&i===entries.length-1);controls.append(b);
      }
      row.append(image,name,controls);list.append(row);
    });
    picker.disabled=processing||entries.length>=5;
    form.elements.gallery_order.value=JSON.stringify(entries.map(e=>e.key));
    status.textContent=processing?'사진 확인·미리보기 준비 중...':entries.length>=5?'추가 이미지는 최대 5장까지 등록할 수 있습니다.':`${entries.length}/5장 · 저장하면 반영됩니다.`;
  }
  picker.addEventListener('change',()=>{
    const files=[...picker.files];picker.value='';
    if(files.length+entries.length>5){status.textContent='추가 이미지는 최대 5장까지 등록할 수 있습니다.';return;}
    processing=true;render();
    queue=queue.catch(()=>{}).then(async()=>{
      for(const file of files){
        if(new URLSearchParams(location.search).get('photo_debug')==='1')console.debug('[JEJU_KNOW photo selection]',{name:file.name,type:file.type,size:file.size});
        if(file.size>32*1024*1024)throw new Error('파일이 너무 큽니다. 한 장당 32MB까지 처리할 수 있어요.');
        const data=new FormData();data.set('csrf_token',form.elements.csrf_token.value);data.set('image_0',file,file.name);
        const response=await fetch('/admin/news/image-preview',{method:'POST',body:data,credentials:'same-origin'});
        if(!response.ok||!response.headers.get('content-type')?.includes('image/webp')){
          let message='이미지를 처리하지 못했습니다. 파일 형식과 관리자 로그인 상태를 확인해주세요.';
          try{message=(await response.json()).detail||message;}catch(_){}
          throw new Error(message);
        }
        const url=URL.createObjectURL(await response.blob());
        const slot=[0,1,2,3,4].find(n=>!entries.some(e=>e.key===`new:${n}`));
        entries.push({key:`new:${slot}`,url,name:file.name,file});render();
      }
    }).then(()=>{processing=false;render();}).catch(error=>{processing=false;render();status.textContent=error.message;});
  });
  form.prepareGallery=()=>queue;
  form.appendGallery=data=>{data.set('gallery_order',JSON.stringify(entries.map(e=>e.key)));entries.forEach(e=>{if(e.file)data.set('gallery_'+e.key.slice(4),e.file,e.file.name);});};
  function move(from,to){if(from===to||to<0||to>=entries.length)return;entries.splice(to,0,entries.splice(from,1)[0]);render();list.children[to].querySelector('[data-action=drag]').focus();}
  list.addEventListener('click',e=>{
    const button=e.target.closest('button');if(!button||processing)return;
    const i=entries.findIndex(x=>x.key===button.closest('li').dataset.galleryKey),action=button.dataset.action;
    if(action==='up'||action==='down')move(i,i+(action==='up'?-1:1));
    if(action==='remove'&&confirm('추가 이미지를 삭제할까요? 대표 포스터와 ISSUE는 유지되며 저장하기를 눌러야 반영됩니다.')){
      const [removed]=entries.splice(i,1);if(removed.file)URL.revokeObjectURL(removed.url);render();
    }
  });
  // Reuse BEST/category ordering's pointer handle + keyboard-accessible arrow pattern.
  list.addEventListener('pointerdown',e=>{const h=e.target.closest('[data-action=drag]');if(!h||h.disabled||e.button!==0)return;drag={h,id:e.pointerId,key:h.closest('li').dataset.galleryKey,target:null};h.setPointerCapture(e.pointerId);e.preventDefault();});
  list.addEventListener('pointermove',e=>{if(!drag||drag.id!==e.pointerId)return;const row=document.elementFromPoint(e.clientX,e.clientY)?.closest('[data-gallery-key]');list.querySelectorAll('.drop-target').forEach(x=>x.classList.remove('drop-target'));drag.target=row&&list.contains(row)?row.dataset.galleryKey:null;row?.classList.add('drop-target');});
  function clear(){if(!drag)return;const d=drag;drag=null;if(d.h.hasPointerCapture(d.id))d.h.releasePointerCapture(d.id);list.querySelectorAll('.drop-target').forEach(x=>x.classList.remove('drop-target'));return d;}
  list.addEventListener('pointerup',()=>{const d=clear();if(d?.target)move(entries.findIndex(x=>x.key===d.key),entries.findIndex(x=>x.key===d.target));});
  list.addEventListener('pointercancel',clear);list.addEventListener('lostpointercapture',clear);
  document.addEventListener('keydown',e=>{if(e.key==='Escape')clear();});
  addEventListener('pagehide',()=>entries.forEach(e=>{if(e.file)URL.revokeObjectURL(e.url);}));render();
})();
