/* Shared hours controls and member category visibility. */
(function () {
  'use strict';
  const category=document.querySelector('#memberPlaceCategory, .place-form select[name="category_id"]');
  if(!category)return;
  const form=category.form;
  function show(group,visible){
    group.hidden=!visible;
    group.querySelectorAll('input,select,textarea').forEach(input=>{input.disabled=!visible;});
  }
  function update(){
    document.querySelectorAll('#memberPlaceTerms fieldset').forEach(group=>{
      show(group,group.dataset.categoryId===category.value&&Boolean(group.querySelector('input')));
    });
    const termSection=document.getElementById('memberPlaceTerms');
    if(termSection)termSection.hidden=!termSection.querySelector('fieldset:not([hidden])');
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
  category.addEventListener('change',update);update();hours();closed();
})();
