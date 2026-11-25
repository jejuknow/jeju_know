(() => {
  const links=[...document.querySelectorAll('[data-issue-photo]')];if(!links.length)return;
  const dialog=document.createElement('dialog');if(!dialog.showModal)return;
  dialog.className='issue-lightbox';dialog.setAttribute('aria-label','행사 이미지 크게 보기');
  const controls=document.createElement('div');controls.className='issue-lightbox-controls';
  const previous=document.createElement('button'),next=document.createElement('button'),close=document.createElement('button'),count=document.createElement('span'),image=document.createElement('img');
  for(const b of [previous,next,close]){b.type='button';b.className='secondary-btn';}
  previous.textContent='← 이전';next.textContent='다음 →';close.textContent='닫기';
  count.setAttribute('aria-live','polite');controls.append(previous,count,next,close);dialog.append(controls,image);document.body.append(dialog);
  let index=0;
  function render(){image.src=links[index].href;image.alt=links[index].querySelector('img').alt;count.textContent=`${index+1} / ${links.length}`;previous.disabled=index===0;next.disabled=index===links.length-1;}
  links.forEach((link,i)=>link.addEventListener('click',e=>{e.preventDefault();index=i;render();dialog.showModal();}));
  previous.onclick=()=>{index--;render();};next.onclick=()=>{index++;render();};close.onclick=()=>dialog.close();
  dialog.addEventListener('keydown',e=>{if(e.key==='ArrowLeft'&&index>0){index--;render();}if(e.key==='ArrowRight'&&index<links.length-1){index++;render();}});
  dialog.addEventListener('close',()=>links[index].focus());
  dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});
})();
