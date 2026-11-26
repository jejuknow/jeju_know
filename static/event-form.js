/* Conditional benefit inputs; unsubmitted fields retain their stored values. */
(()=>{
 const section=document.querySelector('[data-event-benefits]');if(!section)return;
 const form=section.closest('form'),category=form.elements.category;
 const required=['event_start','event_end','summary'].map(name=>[form.elements[name],form.elements[name].required]);
 function update(){
  const active=category.value===section.dataset.category;
  section.hidden=!active;section.querySelectorAll('input').forEach(input=>input.disabled=!active);
  required.forEach(([input,wasRequired])=>input.required=active||wasRequired);
  form.elements.external_url.setCustomValidity('');
 }
 function source(){
  const missing=category.value===section.dataset.category&&!form.elements.external_url.value.trim()&&!form.elements.source_name?.value.trim();
  form.elements.external_url.setCustomValidity(missing?'혜택을 확인할 수 있는 출처 또는 공식 링크를 입력해주세요.':'');
 }
 category.addEventListener('change',()=>{update();source();});
 ['external_url','source_name'].forEach(name=>form.elements[name]?.addEventListener('input',source));
 update();source();
})();
