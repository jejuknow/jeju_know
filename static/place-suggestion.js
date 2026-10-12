(() => {
  const field=document.getElementById('suggestionField'), value=document.getElementById('suggestionValue');
  const category=document.getElementById('suggestionCategory'), text=document.getElementById('suggestionText'), help=document.getElementById('suggestionHelp');
  function render(){
    const isCategory=field.value==='category_id';category.hidden=!isCategory;text.hidden=isCategory;
    category.querySelector('select').disabled=!isCategory;value.disabled=isCategory;
    const hints={opening:'오픈 연도 또는 연도-월을 입력해주세요. 예: 2025 또는 2025-01',coordinates:'변경된 위도와 경도를 쉼표로 구분해주세요. 예: 33.4507,126.5707',map_url:'변경된 지도 링크를 입력해주세요.',website_url:'홈페이지 또는 Instagram 링크를 입력해주세요.',category_id:'장소에 맞는 카테고리를 선택해주세요.'};
    help.textContent=hints[field.value]||'변경된 내용을 입력해주세요. 폐업·기타는 확인한 내용을 문장으로 적어주세요.';
  }
  field.addEventListener('change',render);render();
})();
