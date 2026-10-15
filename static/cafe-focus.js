/* Shared optional orientation field for administrator and member forms. */
(()=>{
  const category=document.querySelector('select[name="category_id"]');
  if(!category)return;
  document.querySelectorAll('[data-cafe-focus]').forEach(group=>{
    const inputs=[...group.querySelectorAll('input[name="cafe_focus"]')];
    const feedback=group.querySelector('[data-focus-feedback]');
    const sync=()=>{
      const active=category.value===group.dataset.parentId;
      group.hidden=!active;inputs.forEach(input=>{input.disabled=!active;});
    };
    group.addEventListener('change',event=>{
      if(event.target.checked&&inputs.filter(i=>i.checked).length>2){
        event.target.checked=false;
        feedback.textContent='카페 성향은 최대 2개까지 선택할 수 있어요.';
      }else feedback.textContent=`${inputs.filter(i=>i.checked).length}/2 선택 · 모르면 선택하지 않아도 괜찮아요.`;
    });
    category.addEventListener('change',sync);sync();
  });
})();
