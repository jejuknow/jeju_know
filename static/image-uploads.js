/* Best-effort, serial browser optimization. The server always validates the pixels again. */
(function (root) {
  'use strict';
  const MAX_BYTES=32*1024*1024, MAX_PIXELS=50000000, EDGE=1920;
  function dimensions(bytes) {
    const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
    if(bytes.length>=24&&view.getUint32(0)===0x89504e47) return {width:view.getUint32(16),height:view.getUint32(20)};
    if(bytes[0]===255&&bytes[1]===216) {
      let p=2;
      while(p+8<bytes.length) {
        if(bytes[p]!==255)break;
        const marker=bytes[p+1],length=view.getUint16(p+2);
        if([192,193,194,195,197,198,199,201,202,203,205,206,207].includes(marker))return {width:view.getUint16(p+7),height:view.getUint16(p+5)};
        if(length<2)break;p+=length+2;
      }
    }
    if(bytes.length>=30&&view.getUint32(0)===0x52494646&&view.getUint32(8)===0x57454250&&view.getUint32(12)===0x56503858) {
      const le24=p=>bytes[p]+bytes[p+1]*256+bytes[p+2]*65536;
      return {width:le24(24)+1,height:le24(27)+1};
    }
    return null; // Unknown headers go to the bounded server decoder.
  }
  function boundedSize(width,height) {
    const ratio=Math.min(1,EDGE/Math.max(width,height));
    return {width:Math.max(1,Math.round(width*ratio)),height:Math.max(1,Math.round(height*ratio))};
  }
  async function optimizePhoto(file) {
    if(!root.createImageBitmap||!/\.(jpe?g|png|webp)$/i.test(file.name))return file;
    let bitmap;
    try {
      const size=dimensions(new Uint8Array(await file.slice(0,524288).arrayBuffer()));
      if(!size||!size.width||!size.height||size.width*size.height>MAX_PIXELS||Math.max(size.width,size.height)/Math.min(size.width,size.height)>10)return file;
      // Specify one dimension so EXIF rotation cannot distort the aspect ratio.
      bitmap=await root.createImageBitmap(file,{imageOrientation:'from-image',resizeWidth:Math.min(EDGE,size.width,size.height),resizeQuality:'high'});
      const target=boundedSize(bitmap.width,bitmap.height),canvas=document.createElement('canvas');
      canvas.width=target.width;canvas.height=target.height;
      canvas.getContext('2d').drawImage(bitmap,0,0,target.width,target.height);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/webp',.86));
      canvas.width=canvas.height=1;
      if(!blob||blob.type!=='image/webp'||blob.size>=file.size)return file;
      return new File([blob],'optimized-photo.webp',{type:'image/webp'});
    } catch (_) { return file; }
    finally { bitmap?.close(); }
  }
  if(typeof module==='object'&&module.exports)module.exports={dimensions,boundedSize,optimizePhoto};
  if(typeof document==='undefined')return;
  let queue=Promise.resolve();
  document.querySelectorAll('form').forEach(form=>{
    const slots=[...form.querySelectorAll('[data-image-slot]')];if(!slots.length)return;
    const states=[],buttons=[...form.querySelectorAll('button[type=submit]')];
    const prepareUrl=form.dataset.photoPrepare;
    async function preparePhoto(file){
      if(!prepareUrl)return optimizePhoto(file);
      const data=new FormData();data.set('csrf_token',form.elements.csrf_token.value);data.set('photo',file,file.name);
      let response;
      try{response=await fetch(prepareUrl,{method:'POST',body:data,credentials:'same-origin'});}
      catch(_){throw new Error('사진 업로드 중 문제가 발생했습니다. 다시 시도해주세요.');}
      if(!response.ok){
        let message='사진을 처리하지 못했습니다. 다시 시도해주세요.';
        if(response.status===413)message='사진 파일이 너무 큽니다. 한 장당 32MB까지 처리할 수 있어요.';
        else {try{message=(await response.json()).error||message;}catch(_){}}
        throw new Error(message);
      }
      const blob=await response.blob();
      if(blob.type!=='image/webp')throw new Error('사진을 처리하지 못했습니다. 다시 시도해주세요.');
      return new File([blob],'optimized-photo.webp',{type:'image/webp'});
    }
    const feedback=form.querySelector('[data-submit-status]')||form.appendChild(Object.assign(document.createElement('p'),{className:'form-help'}));
    feedback.setAttribute('role','status');
    let sending=false;
    function busy(){buttons.forEach(b=>{b.disabled=sending||states.some(s=>s.busy);});}
    slots.forEach(slot=>{
      const input=slot.querySelector('input[type=file]'),remove=slot.querySelector('input[type=checkbox]');
      const preview=slot.querySelector('.image-preview'),image=preview.querySelector('img'),placeholder=preview.querySelector('.image-placeholder');
      const status=slot.querySelector('.image-feedback'),label=slot.querySelector('.image-file-button span');
      const retry=document.createElement('button');retry.type='button';retry.className='text-btn';retry.textContent='다시 시도';retry.hidden=true;status.after(retry);
      const state={input,remove,file:null,busy:false,revision:0,promise:Promise.resolve(),url:null};states.push(state);
      function render(){
        if(state.url)URL.revokeObjectURL(state.url);state.url=null;
        const url=!remove.checked&&(state.file?(state.url=URL.createObjectURL(state.file)):preview.dataset.original);
        image.hidden=!url;placeholder.hidden=!!url;
        placeholder.textContent='선택한 사진 미리보기';
        if(url)image.src=url;else image.removeAttribute('src');
        label.textContent=url?'사진 변경':'사진 선택';
      }
      function selectPhoto(){
        const revision=++state.revision,file=input.files[0];state.file=null;state.busy=false;
        input.setCustomValidity('');status.textContent='';retry.hidden=true;
        if(!file){render();busy();return;}
        if(file.size>MAX_BYTES){
          status.textContent='파일이 너무 큽니다. 사진은 한 장당 32MB까지 처리할 수 있어요.';
          input.setCustomValidity(status.textContent);render();busy();return;
        }
        remove.checked=false;state.file=file;render();
        state.busy=true;status.textContent='사진 처리 대기 중...';busy();
        state.promise=queue=queue.catch(()=>{}).then(async()=>{
          if(revision!==state.revision)return;
          status.textContent='사진 업로드·최적화 중...';
          const optimized=await preparePhoto(file);
          if(revision!==state.revision)return;
          state.file=optimized;render();
          status.textContent=optimized!==file?'사진 최적화 완료 · 저장하면 반영됩니다.':'사진 선택 완료 · 서버에서 안전하게 최적화합니다.';
        }).catch(error=>{
          if(revision!==state.revision)return;
          status.textContent=error.message||'사진을 처리하지 못했습니다.';
          input.setCustomValidity(status.textContent);retry.hidden=false;
        }).finally(()=>{if(revision===state.revision){state.busy=false;busy();}});
      }
      input.addEventListener('change',selectPhoto);retry.addEventListener('click',selectPhoto);
      image.addEventListener('error',()=>{
        if(!state.file)return;
        image.hidden=true;placeholder.hidden=false;
        placeholder.textContent='저장 후 미리보기';
        if(!state.busy&&!prepareUrl)status.textContent='이 브라우저에서는 미리보기를 지원하지 않습니다. 원본을 서버에서 처리합니다.';
        // HEIC preview failure is not a validation failure.
      });
      remove.addEventListener('change',()=>{
        state.revision++;state.busy=false;
        if(remove.checked){input.value='';state.file=null;input.setCustomValidity('');}
        retry.hidden=true;status.textContent=remove.checked?'저장하면 이 사진이 삭제됩니다.':'';render();busy();
      });
      root.addEventListener('pagehide',()=>{if(state.url)URL.revokeObjectURL(state.url);});
    });
    function showDuplicate(doc){
      const warning=doc.querySelector('.duplicate-warning');
      if(!warning)return;
      form.parentNode.querySelector('.duplicate-warning')?.remove();
      // This is our escaped, same-origin server form, never an external response.
      form.before(warning);
      if(!form.querySelector('[name=duplicate_ack]')){
        const label=document.createElement('label');label.className='member-check';
        const check=document.createElement('input');check.type='checkbox';check.name='duplicate_ack';check.value='1';check.required=true;
        label.append(check,document.createTextNode('기존 장소를 확인했으며 서로 다른 장소입니다.'));feedback.before(label);
      }
    }
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(sending)return;
      sending=true;busy();feedback.textContent='사진과 지도 위치를 확인하고 있어요.';
      try {
        await Promise.all(states.map(s=>s.promise));
        await form.prepareLocation?.();
        if(!form.reportValidity())return;
        const data=new FormData(form);
        states.forEach(s=>{if(s.file&&!s.remove.checked)data.set(s.input.name,s.file,s.file.name);});
        feedback.textContent=form.hasAttribute('data-member-place-form')?'장소 등록 중...':'사진과 내용을 저장 중...';
        const response=await fetch(form.action||location.href,{method:'POST',body:data,credentials:'same-origin'});
        if(response.redirected&&response.ok){location.assign(response.url);return;}
        const text=await response.text(),doc=new DOMParser().parseFromString(text,'text/html');
        if(response.status===409)showDuplicate(doc);
        const message=doc.querySelector('.member-error,.flash.error,[role=alert]')?.textContent?.trim();
        feedback.textContent=message||(!/<(?:html|body)/i.test(text)&&text.length<500?text:'사진 업로드 중 문제가 발생했습니다. 입력 내용을 확인하고 다시 시도해주세요.');
        feedback.setAttribute('role','alert');feedback.scrollIntoView({block:'nearest'});
      } catch (_) {
        feedback.textContent='사진 업로드 중 연결 문제가 발생했습니다. 내 장소에서 등록 여부를 확인한 뒤 다시 시도해주세요.';
        feedback.setAttribute('role','alert');
      } finally {sending=false;busy();}
    });
    root.addEventListener('pageshow',()=>{sending=false;busy();});
  });
})(typeof window==='undefined'?globalThis:window);
