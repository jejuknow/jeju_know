/* The member form uses the same DB taxonomy as FIND, MAP and administration. */
(function () {
  'use strict';
  const category=document.getElementById('memberPlaceCategory');
  if(!category)return;
  function update(){
    document.querySelectorAll('#memberPlaceTerms fieldset').forEach(group=>{
      const active=group.dataset.categoryId===category.value;
      group.hidden=!active||!group.querySelector('input');
      group.querySelectorAll('input').forEach(input=>{input.disabled=!active;if(!active)input.checked=false;});
    });
  }
  category.addEventListener('change',update);update();
})();
