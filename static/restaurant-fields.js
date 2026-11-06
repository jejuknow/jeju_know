/* Shared DB-backed classification controls for admin, member and corrections. */
(()=>{
  const group=document.querySelector('[data-restaurant-fields]');
  if(!group)return;
  const category=document.querySelector('select[name="category_id"]');
  const correction=document.getElementById('suggestionField');
  const main=group.querySelector('[name="restaurant_main_id"]');
  const choices=Array.from(group.querySelectorAll('[data-subtype-parent]'));
  const count=group.querySelector('[data-subtype-count]');
  const notice=group.querySelector('[data-subtype-notice]');
  const empty=group.querySelector('[data-subtype-empty]');
  function selected(){return choices.filter(label=>{const input=label.querySelector('input');return !input.disabled&&input.checked;});}
  function sync(){
    const active=correction?correction.value==='submission_details':category?.value===group.dataset.foodId;
    group.hidden=!active;
    group.querySelectorAll('input,select').forEach(input=>input.disabled=!active);
    main.required=active&&main.dataset.required==='1';
    choices.forEach(label=>{
      const valid=label.dataset.subtypeParent===main.value,input=label.querySelector('input');
      label.hidden=!valid;input.disabled=!active||!valid;
      if(!valid)input.checked=false;
    });
    count.textContent=selected().length+'/3';notice.textContent='';
    empty.hidden=choices.some(label=>!label.hidden);
    empty.textContent=main.value?'이 대분류에는 등록된 세부유형이 없습니다. 대분류만 저장합니다.':'대분류를 먼저 선택해주세요.';
  }
  choices.forEach(label=>label.querySelector('input').addEventListener('change',event=>{
    if(selected().length>3){event.target.checked=false;notice.textContent='세부유형은 최대 3개까지 선택할 수 있습니다.';}
    else notice.textContent='';
    count.textContent=selected().length+'/3';
  }));
  category?.addEventListener('change',sync);correction?.addEventListener('change',sync);
  main.addEventListener('change',sync);sync();
})();
