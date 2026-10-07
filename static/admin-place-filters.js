/* New tags use the existing features[] form field, so no DB migration is needed. */
(() => {
  const container=document.getElementById('adminFilterOptions');
  if(!container || !window.PlaceFilters)return;
  const taxonomy=window.PlaceFilters;
  const existing=new Set(Array.from(document.querySelectorAll('input[name="features"]'),input=>input.value));
  const groups=[
    {label:'메인 취향',options:taxonomy.preferences},
    ...Object.entries(taxonomy.menus).filter(([category])=>category!=='전체').map(([category,options])=>({label:`${category} · 메뉴/활동`,options})),
    {label:'이용 조건',options:taxonomy.conditions.filter(item=>!['parking','pet','child'].includes(item.id))},
    ...taxonomy.details.map(group=>({label:`세부 취향 · ${group.label}`,options:group.options}))
  ];
  groups.forEach(group=>{
    const fieldset=document.createElement('fieldset');
    const legend=document.createElement('legend');legend.textContent=group.label;fieldset.append(legend);
    const grid=document.createElement('div');grid.className='check-grid';
    group.options.forEach(item=>{
      if(existing.has(item.label))return;
      existing.add(item.label);
      const label=document.createElement('label');label.className='check';
      const input=document.createElement('input');input.type='checkbox';input.name='features';input.value=item.label;
      const text=document.createElement('span');text.textContent=item.label;
      label.append(input,text);grid.append(label);
    });
    if(grid.childElementCount){fieldset.append(grid);container.append(fieldset);}
  });
  document.querySelectorAll('select[name="region"] option').forEach(option=>{option.textContent=taxonomy.regionLabel(option.textContent);});
})();
