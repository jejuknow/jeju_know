(function (root) {
  'use strict';
  let pending;
  root.JejunoKakao = {
    async geocode(key, address) {
      const sdk = await this.load(key);
      return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('주소 검색이 지연되고 있습니다. 다시 시도해주세요.')), 8000);
        new sdk.services.Geocoder().addressSearch(address, (data, status) => {
          clearTimeout(timeout);
          if (status === sdk.services.Status.OK && data.length === 1) resolve({lat:Number(data[0].y), lng:Number(data[0].x)});
          else reject(new Error('주소로 정확한 위치를 찾지 못했습니다. 지도에서 직접 위치를 선택해주세요.'));
        });
      });
    },
    load(key) {
      if (!key) return Promise.reject(new Error('지도를 준비 중입니다. 장소 목록과 상세보기는 이용할 수 있어요.'));
      if (pending) return pending;
      pending = new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('지도를 불러오지 못했습니다. 잠시 후 새로고침해 주세요.')), 12000);
        const script = document.createElement('script');
        script.src = 'https://dapi.kakao.com/v2/maps/sdk.js?' + new URLSearchParams({appkey:key, autoload:'false', libraries:'services'});
        script.onerror = () => { clearTimeout(timer); reject(new Error('지도 연결을 확인해 주세요. 장소 목록은 계속 이용할 수 있어요.')); };
        script.onload = () => {
          if (!root.kakao?.maps) { script.onerror(); return; }
          root.kakao.maps.load(() => { clearTimeout(timer); resolve(root.kakao.maps); });
        };
        document.head.appendChild(script);
      });
      return pending;
    }
  };
})(window);
