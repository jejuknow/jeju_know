(function (root) {
  'use strict';
  let pending;
  root.JejunoKakao = {
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
