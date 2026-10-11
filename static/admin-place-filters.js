/* DB-backed subtype fields follow the selected top-level category. */
(()=>{
  const category=document.querySelector('select[name="category_id"]');
  if(!category)return;
  function sync(){document.querySelectorAll('.place-term-group').forEach(group=>{
    const active=group.dataset.parentId===category.value;
    group.hidden=!active;group.querySelectorAll('input[name="term_ids"]').forEach(input=>{input.disabled=!active;});
  });}
  category.addEventListener('change',sync);sync();
})();
