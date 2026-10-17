(() => {
  const field=document.getElementById('suggestionField'), value=document.getElementById('suggestionValue');
  const category=document.getElementById('suggestionCategory'), text=document.getElementById('suggestionText'), help=document.getElementById('suggestionHelp');
  const status=document.getElementById('suggestionStatus');
  function render(){
    const isCategory=field.value==='category_id', isStatus=['reservation_status','parking_status'].includes(field.value);
    category.hidden=!isCategory;status.hidden=!isStatus;text.hidden=isCategory||isStatus;
    category.querySelector('select').disabled=!isCategory;status.querySelector('select').disabled=!isStatus;value.disabled=isCategory||isStatus;
    value.maxLength=['business_hours','closed_days'].includes(field.value)?200:field.value==='phone'?80:2000;
    value.inputMode=field.value==='phone'?'tel':'text';
    const hints={phone:'변경된 전화번호를 입력해주세요. 예: 064-123-4567',business_hours:'확인한 영업시간과 휴무 정보를 입력해주세요.',opening:'오픈 연도 또는 연도-월을 입력해주세요. 예: 2025 또는 2025-01',coordinates:'변경된 위도와 경도를 쉼표로 구분해주세요. 예: 33.4507,126.5707',closed:'폐업 또는 휴업 여부와 확인한 시점을 알려주세요.',business:'영업시간, 휴무일 등 달라진 영업 정보를 알려주세요.',map_url:'변경된 지도 링크를 입력해주세요.',website_url:'홈페이지 또는 Instagram 링크를 입력해주세요.',category_id:'장소에 맞는 카테고리를 선택해주세요.'};
    help.textContent=isStatus?'확인한 이용 상태를 선택해주세요. 모르면 미확인으로 남겨주세요.':hints[field.value]||'변경된 내용을 입력해주세요.';
  }
  field.addEventListener('change',render);render();
})();
