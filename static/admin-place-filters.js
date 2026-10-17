/* DB-backed subtype fields follow the selected top-level category. */
(()=>{
  const category=document.querySelector('select[name="category_id"]');
  if(!category)return;
  function sync(){document.querySelectorAll('.place-term-group').forEach(group=>{
    const active=group.dataset.parentId===category.value;
    group.hidden=!active;group.querySelectorAll('input[name="term_ids"]').forEach(input=>{input.disabled=!active;});
  });
    const slug=category.selectedOptions[0]?.dataset.slug||'';
    document.getElementById('categoryEmoji').textContent=({food:'🍚',cafe:'☕',attraction:'📍'})[slug]||'📍';
    document.querySelectorAll('[data-place-categories]').forEach(group=>{
      group.hidden=!group.dataset.placeCategories.split(' ').includes(slug);
      // Keep optional facts enabled: category switches must not erase saved input.
    });
  }
  category.addEventListener('change',sync);sync();
  const form=category.form;
  function newHelp(){
    document.getElementById('newVisibilityHelp').hidden=!!(form.elements.opened_year.value&&form.elements.opened_month.value);
  }
  ['opened_year','opened_month'].forEach(name=>form.elements[name].addEventListener('input',newHelp));newHelp();
  document.querySelector('.place-form')?.addEventListener('invalid',event=>{
    let parent=event.target.parentElement;
    while(parent){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}
  },true);
})();
