/* Shared visit controls; member-only optional classifications keep their own toggle. */
(function () {
  'use strict';
  const category=document.querySelector('#memberPlaceCategory, .place-form select[name="category_id"]');
  if(!category)return;
  const form=category.form,details=form.elements.details_enabled;
  function show(group,visible){
    group.hidden=!visible;
    group.querySelectorAll('input,select,textarea').forEach(input=>{input.disabled=!visible;});
  }
  function update(){
    if(details){
    const expanded=details.checked;
    document.getElementById('submissionDetails').hidden=!expanded;
    details.setAttribute('aria-expanded',String(expanded));
    document.querySelectorAll('#submissionDetails input,#submissionDetails select').forEach(input=>{input.disabled=!expanded;});
    document.querySelectorAll('#memberPlaceTerms fieldset').forEach(group=>{
      const active=group.dataset.categoryId===category.value;
      group.hidden=!active||!group.querySelector('input');
      group.querySelectorAll('input').forEach(input=>{input.disabled=!active||!expanded;});
    });
    }
    const slug=category.selectedOptions[0]?.dataset.slug;
    document.querySelectorAll('[data-menu-field]').forEach(group=>show(group,['cafe','food'].includes(slug)));
  }
  function hours(){
    const unknown=form.elements.hours_unknown.checked,same=form.elements.hours_same.checked;
    show(form.querySelector('[data-hours-times]'),!unknown&&same);
    show(form.querySelector('[data-hours-custom]'),!unknown&&!same);
    form.elements.hours_start.required=form.elements.hours_end.required=!unknown&&same;
  }
  function closed(){
    show(form.querySelector('[data-closed-days]'),form.elements.closed_mode.value==='days');
    show(form.querySelector('[data-closed-custom]'),form.elements.closed_mode.value==='custom');
  }
  form.elements.hours_same.addEventListener('change',hours);
  form.elements.hours_unknown.addEventListener('change',hours);
  form.elements.closed_mode.addEventListener('change',closed);
  const price=form.elements.representative_price;
  price.addEventListener('blur',()=>{
    const digits=price.value.trim().replace(/[,원\s]/g,'');
    if(/^\d{1,8}$/.test(digits))price.value=Number(digits).toLocaleString('ko-KR');
  });
  details?.addEventListener('change',update);
  category.addEventListener('change',update);update();hours();closed();
})();
