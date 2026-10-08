(function () {
  'use strict';
  const button = document.getElementById('addressLocate');
  if (!button) return;
  const message = document.getElementById('locationFeedback');
  button.addEventListener('click', async () => {
    const address = document.querySelector('[name="address"]').value.trim();
    if (!address) { message.textContent = '주소를 먼저 입력해 주세요.'; return; }
    button.disabled = true;
    message.textContent = '주소 위치를 확인하고 있어요.';
    try {
      const maps = await window.JejunoKakao.load(button.dataset.kakaoKey);
      const results = await new Promise((resolve, reject) => {
        new maps.services.Geocoder().addressSearch(address, (data, status) => {
          if (status === maps.services.Status.OK && data.length) resolve(data);
          else reject(new Error('정확한 도로명·지번 주소로 다시 검색하거나 좌표를 직접 입력해 주세요.'));
        });
      });
      if (results.length !== 1) throw new Error('주소가 여러 곳에 해당합니다. 건물 번호까지 입력해 주세요.');
      document.querySelector('[name="latitude"]').value = results[0].y;
      document.querySelector('[name="longitude"]').value = results[0].x;
      message.textContent = `${results[0].address_name} 위치를 찾았습니다. 주소와 좌표를 확인하고 장소를 저장해 주세요.`;
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
  });
})();
