/* DB-backed subtype fields follow the selected top-level category. */
(()=>{
  const category=document.querySelector('select[name="category_id"]');
  if(!category)return;
  function sync(){document.querySelectorAll('.place-term-group').forEach(group=>{
    const active=group.dataset.parentId===category.value;
    group.hidden=!active;group.querySelectorAll('input[name="term_ids"]').forEach(input=>{input.disabled=!active;});
  });
    const slug=category.selectedOptions[0]?.dataset.slug||'';
    document.querySelectorAll('[data-place-categories]').forEach(group=>{
      group.hidden=!group.dataset.placeCategories.split(' ').includes(slug);
      // Keep optional facts enabled: category switches must not erase saved input.
    });
  }
  category.addEventListener('change',sync);sync();
  document.querySelector('.place-form')?.addEventListener('invalid',event=>{
    let parent=event.target.parentElement;
    while(parent){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}
  },true);
})();
