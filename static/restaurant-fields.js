/* The same dependent selects serve member and administrator forms. */
(()=>{
  const group=document.querySelector('[data-restaurant-fields]');
  if(!group)return;
  const category=document.querySelector('select[name="category_id"]');
  const main=group.querySelector('[name="restaurant_main_id"]');
  const subtype=group.querySelector('[name="restaurant_subtype_id"]');
  const choices=Array.from(subtype.options).filter(option=>option.value).map(option=>option.cloneNode(true));
  function sync(){
    const active=category.value===group.dataset.foodId,selected=subtype.value;
    group.hidden=!active;
    group.querySelectorAll('input,select').forEach(input=>input.disabled=!active);
    main.required=active&&main.dataset.required==='1';
    subtype.replaceChildren(new Option(main.value?'대분류만 선택':'대분류를 먼저 선택해주세요',''));
    choices.filter(option=>option.dataset.parent===main.value).forEach(option=>subtype.add(option.cloneNode(true)));
    subtype.value=Array.from(subtype.options).some(option=>option.value===selected)?selected:'';
    subtype.disabled=!active||!main.value||subtype.options.length===1;
  }
  category.addEventListener('change',sync);main.addEventListener('change',sync);sync();
})();
