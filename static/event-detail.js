(()=>{
 const canvas=document.getElementById('eventDetailMap');if(!canvas)return;
 window.JejunoKakao.load(canvas.dataset.kakaoKey).then(sdk=>{
  const center=new sdk.LatLng(Number(canvas.dataset.lat),Number(canvas.dataset.lng));
  const map=new sdk.Map(canvas,{center,level:4});
  new sdk.Marker({map,position:center});
  new ResizeObserver(()=>{map.relayout();map.setCenter(center);}).observe(canvas);
 }).catch(()=>{canvas.textContent='지도를 불러오지 못했습니다. 아래 카카오맵 링크에서 위치를 확인해주세요.';});
})();
